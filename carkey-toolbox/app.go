package main

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"path/filepath"
	"sync"
	"time"

	"carkey-toolbox/internal/pybridge"

	"github.com/wailsapp/wails/v2/pkg/runtime"
)

// ToolParam / ToolInfo mirror ToolSpec.info() on the Python side.
type ToolParam struct {
	Name     string           `json:"name"`
	Type     string           `json:"type"`
	Label    string           `json:"label"`
	Default  any              `json:"default"`
	Help     string           `json:"help"`
	Options  []string         `json:"options"`
	Required bool             `json:"required"`
	ShowIf   map[string][]any `json:"show_if,omitempty"`
}

type ToolInfo struct {
	Name        string      `json:"name"`
	Title       string      `json:"title"`
	Description string      `json:"description"`
	Category    string      `json:"category"`
	Source      string      `json:"source"`
	Params      []ToolParam `json:"params"`
}

type WorkerInfo struct {
	Running     bool     `json:"running"`
	Interpreter string   `json:"interpreter"`
	Python      string   `json:"python"`
	Pid         int      `json:"pid"`
	PythonDir   string   `json:"pythonDir"`
	ToolsDirs   []string `json:"toolsDirs"`
	Error       string   `json:"error"`
}

// App is bound to the frontend: every exported method is callable from JS
// as window.go.main.App.<Method>.
type App struct {
	ctx context.Context

	mu        sync.Mutex
	bridge    *pybridge.Bridge
	pyDir     string
	python    string
	lastError string
}

func NewApp() *App { return &App{} }

func (a *App) startup(ctx context.Context) {
	a.ctx = ctx
	if err := a.startWorker(); err != nil {
		a.setError(err)
	}
}

func (a *App) shutdown(ctx context.Context) {
	a.mu.Lock()
	b := a.bridge
	a.bridge = nil
	a.mu.Unlock()
	if b != nil {
		c, cancel := context.WithTimeout(ctx, 2*time.Second)
		defer cancel()
		b.Close(c)
	}
}

func (a *App) setError(err error) {
	a.mu.Lock()
	a.lastError = err.Error()
	a.mu.Unlock()
	runtime.LogError(a.ctx, err.Error())
	runtime.EventsEmit(a.ctx, "worker.status", map[string]any{"running": false, "error": err.Error()})
}

func (a *App) startWorker() error {
	pyDir, err := pybridge.FindPythonDir()
	if err != nil {
		return err
	}
	python, err := pybridge.FindInterpreter(pyDir)
	if err != nil {
		return err
	}
	var self *pybridge.Bridge
	b, err := pybridge.Start(pybridge.Config{
		Python: python,
		Script: filepath.Join(pyDir, "worker.py"),
		Dir:    pyDir,
		OnNotify: func(n pybridge.Notification) {
			// Forward Python notifications to the frontend as Wails events.
			runtime.EventsEmit(a.ctx, n.Method, n.Params)
		},
		OnStderr: func(line string) {
			runtime.EventsEmit(a.ctx, "worker.log", map[string]any{"level": "stderr", "message": line})
		},
		OnExit: func(err error) {
			a.mu.Lock()
			if a.bridge != self { // restarted meanwhile, ignore the old bridge
				a.mu.Unlock()
				return
			}
			a.bridge = nil
			a.mu.Unlock()
			msg := "python worker exited"
			if err != nil {
				msg += ": " + err.Error()
			}
			a.setError(fmt.Errorf("%s", msg))
		},
	})
	if err != nil {
		return err
	}
	self = b
	a.mu.Lock()
	a.bridge, a.pyDir, a.python, a.lastError = b, pyDir, python, ""
	a.mu.Unlock()
	runtime.EventsEmit(a.ctx, "worker.status", map[string]any{"running": true})
	return nil
}

func (a *App) getBridge() (*pybridge.Bridge, error) {
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.bridge == nil {
		if a.lastError != "" {
			return nil, fmt.Errorf("%s", a.lastError)
		}
		return nil, pybridge.ErrClosed
	}
	return a.bridge, nil
}

func (a *App) call(method string, params, result any) error {
	b, err := a.getBridge()
	if err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(a.ctx, 30*time.Second)
	defer cancel()
	return b.Call(ctx, method, params, result)
}

// ---------------- Methods called from the frontend ----------------

func (a *App) ListTools() ([]ToolInfo, error) {
	var tools []ToolInfo
	err := a.call("list_tools", nil, &tools)
	return tools, err
}

func (a *App) ReloadTools() ([]ToolInfo, error) {
	var tools []ToolInfo
	err := a.call("reload_tools", nil, &tools)
	return tools, err
}

// RunTool starts a tool in the background and returns its run_id. Logs, progress and the result arrive as events
// run.log, run.progress, run.finished.
func (a *App) RunTool(name string, params map[string]any) (string, error) {
	buf := make([]byte, 6)
	_, _ = rand.Read(buf)
	runID := hex.EncodeToString(buf)
	runtime.EventsEmit(a.ctx, "run.started", map[string]any{"run_id": runID, "tool": name, "params": params,
		"started_at": time.Now().Format(time.RFC3339)})
	err := a.call("run_tool", map[string]any{"run_id": runID, "tool": name, "params": params}, nil)
	if err != nil {
		runtime.EventsEmit(a.ctx, "run.finished", map[string]any{"run_id": runID, "ok": false, "error": err.Error()})
	}
	return runID, err
}

func (a *App) CancelRun(runID string) error {
	return a.call("cancel", map[string]any{"run_id": runID}, nil)
}

func (a *App) RestartWorker() error {
	a.shutdown(a.ctx)
	if err := a.startWorker(); err != nil {
		a.setError(err)
		return err
	}
	return nil
}

func (a *App) GetWorkerInfo() WorkerInfo {
	a.mu.Lock()
	info := WorkerInfo{Interpreter: a.python, PythonDir: a.pyDir, Error: a.lastError, Running: a.bridge != nil}
	a.mu.Unlock()
	if !info.Running {
		return info
	}
	var ping struct {
		Python    string   `json:"python"`
		Pid       int      `json:"pid"`
		ToolsDirs []string `json:"tools_dirs"`
	}
	if err := a.call("ping", nil, &ping); err != nil {
		info.Error = err.Error()
		return info
	}
	info.Python, info.Pid, info.ToolsDirs = ping.Python, ping.Pid, ping.ToolsDirs
	return info
}

// SaveResult lets the user save a run's result as a JSON file.
func (a *App) SaveResult(defaultName string, data any) (string, error) {
	path, err := runtime.SaveFileDialog(a.ctx, runtime.SaveDialogOptions{DefaultFilename: defaultName,
		Filters: []runtime.FileFilter{{DisplayName: "JSON", Pattern: "*.json"}}})
	if err != nil || path == "" {
		return "", err
	}
	b, err := json.MarshalIndent(data, "", "  ")
	if err != nil {
		return "", err
	}
	return path, writeFile(path, b)
}

// PickFile opens a file picker for params of type "file".
func (a *App) PickFile(title string) (string, error) {
	return runtime.OpenFileDialog(a.ctx, runtime.OpenDialogOptions{Title: title})
}
