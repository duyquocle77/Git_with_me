// Package pybridge starts the Python worker as a child process and talks to it
// using JSON-RPC 2.0 (one message per line) over stdin/stdout.
//
// It does not depend on Wails, so it can be tested on its own with `go test`.
package pybridge

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"os/exec"
	"sync"
	"sync/atomic"
)

// Notification is a message from Python without an id (log, progress, ...).
type Notification struct {
	Method string          `json:"method"`
	Params json.RawMessage `json:"params"`
}

// RPCError is an error returned by the Python side.
type RPCError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
	Data    any    `json:"data,omitempty"`
}

func (e *RPCError) Error() string { return fmt.Sprintf("python: %s", e.Message) }

// ErrClosed is returned when the worker has exited.
var ErrClosed = errors.New("python worker is not running")

type Config struct {
	Python     string   // interpreter path
	Script     string   // path to worker.py
	Dir        string   // working directory (optional)
	Env        []string // extra environment variables
	OnNotify   func(Notification)
	OnStderr   func(line string)
	OnExit     func(err error)
	MaxLineLen int // default 16 MiB
}

type message struct {
	ID     *int64          `json:"id,omitempty"`
	Method string          `json:"method,omitempty"`
	Params json.RawMessage `json:"params,omitempty"`
	Result json.RawMessage `json:"result,omitempty"`
	Error  *RPCError       `json:"error,omitempty"`
}

type Bridge struct {
	cfg   Config
	cmd   *exec.Cmd
	stdin io.WriteCloser

	writeMu sync.Mutex
	nextID  atomic.Int64

	mu      sync.Mutex
	pending map[int64]chan message
	closed  bool
	exitErr error
	done    chan struct{}
}

// Start runs `python -u worker.py` and begins reading its output.
func Start(cfg Config) (*Bridge, error) {
	if cfg.MaxLineLen == 0 {
		cfg.MaxLineLen = 16 << 20
	}
	cmd := exec.Command(cfg.Python, "-u", cfg.Script)
	cmd.Dir = cfg.Dir
	cmd.Env = append(os.Environ(), "PYTHONIOENCODING=utf-8", "PYTHONUTF8=1")
	cmd.Env = append(cmd.Env, cfg.Env...)
	hideWindow(cmd)

	stdin, err := cmd.StdinPipe()
	if err != nil {
		return nil, err
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		return nil, err
	}
	stderr, err := cmd.StderrPipe()
	if err != nil {
		return nil, err
	}
	if err := cmd.Start(); err != nil {
		return nil, fmt.Errorf("start %s: %w", cfg.Python, err)
	}

	b := &Bridge{cfg: cfg, cmd: cmd, stdin: stdin, pending: map[int64]chan message{}, done: make(chan struct{})}

	var readers sync.WaitGroup
	readers.Add(2)
	go func() { defer readers.Done(); b.readStdout(stdout) }()
	go func() { defer readers.Done(); b.readStderr(stderr) }()
	go func() {
		readers.Wait()
		err := cmd.Wait()
		b.shutdown(err)
	}()
	return b, nil
}

func (b *Bridge) readStdout(r io.Reader) {
	sc := bufio.NewScanner(r)
	sc.Buffer(make([]byte, 64*1024), b.cfg.MaxLineLen)
	for sc.Scan() {
		var m message
		if err := json.Unmarshal(sc.Bytes(), &m); err != nil {
			b.stderrLine("pybridge: bad message: " + sc.Text())
			continue
		}
		if m.ID == nil {
			if m.Method != "" && b.cfg.OnNotify != nil {
				b.cfg.OnNotify(Notification{Method: m.Method, Params: m.Params})
			}
			continue
		}
		b.mu.Lock()
		ch := b.pending[*m.ID]
		delete(b.pending, *m.ID)
		b.mu.Unlock()
		if ch != nil {
			ch <- m
		}
	}
	if err := sc.Err(); err != nil {
		b.stderrLine("pybridge: read error: " + err.Error())
	}
}

func (b *Bridge) readStderr(r io.Reader) {
	sc := bufio.NewScanner(r)
	sc.Buffer(make([]byte, 64*1024), b.cfg.MaxLineLen)
	for sc.Scan() {
		b.stderrLine(sc.Text())
	}
}

func (b *Bridge) stderrLine(s string) {
	if b.cfg.OnStderr != nil {
		b.cfg.OnStderr(s)
	}
}

func (b *Bridge) shutdown(err error) {
	b.mu.Lock()
	if b.closed {
		b.mu.Unlock()
		return
	}
	b.closed = true
	b.exitErr = err
	pending := b.pending
	b.pending = map[int64]chan message{}
	b.mu.Unlock()

	for _, ch := range pending {
		close(ch)
	}
	close(b.done)
	if b.cfg.OnExit != nil {
		b.cfg.OnExit(err)
	}
}

// Call sends a request and waits for the response. result may be nil.
func (b *Bridge) Call(ctx context.Context, method string, params any, result any) error {
	id := b.nextID.Add(1)
	ch := make(chan message, 1)

	b.mu.Lock()
	if b.closed {
		b.mu.Unlock()
		return ErrClosed
	}
	b.pending[id] = ch
	b.mu.Unlock()

	req := map[string]any{"jsonrpc": "2.0", "id": id, "method": method}
	if params != nil {
		req["params"] = params
	}
	line, err := json.Marshal(req)
	if err != nil {
		b.forget(id)
		return err
	}
	b.writeMu.Lock()
	_, err = b.stdin.Write(append(line, '\n'))
	b.writeMu.Unlock()
	if err != nil {
		b.forget(id)
		return fmt.Errorf("write to python: %w", err)
	}

	select {
	case m, ok := <-ch:
		if !ok {
			return ErrClosed
		}
		if m.Error != nil {
			return m.Error
		}
		if result != nil && len(m.Result) > 0 {
			return json.Unmarshal(m.Result, result)
		}
		return nil
	case <-ctx.Done():
		b.forget(id)
		return ctx.Err()
	}
}

func (b *Bridge) forget(id int64) {
	b.mu.Lock()
	delete(b.pending, id)
	b.mu.Unlock()
}

// Done is closed once the worker has exited.
func (b *Bridge) Done() <-chan struct{} { return b.done }

// Pid of the Python process.
func (b *Bridge) Pid() int { return b.cmd.Process.Pid }

// Close closes stdin (the worker exits on its own) and kills it if it does not exit in time.
func (b *Bridge) Close(ctx context.Context) error {
	_ = b.stdin.Close()
	select {
	case <-b.done:
	case <-ctx.Done():
		_ = b.cmd.Process.Kill()
		<-b.done
	}
	return nil
}
