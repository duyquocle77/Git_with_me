package pybridge

import (
	"context"
	"encoding/json"
	"path/filepath"
	"testing"
	"time"
)

func startWorker(t *testing.T, onNotify func(Notification)) *Bridge {
	t.Helper()
	dir, _ := filepath.Abs("../../python")
	py, err := FindInterpreter(dir)
	if err != nil {
		t.Skip(err)
	}
	b, err := Start(Config{Python: py, Script: filepath.Join(dir, "worker.py"), OnNotify: onNotify,
		OnStderr: func(l string) { t.Log("stderr:", l) }})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() {
		ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		b.Close(ctx)
	})
	return b
}

func TestListAndRun(t *testing.T) {
	finished := make(chan map[string]any, 4)
	b := startWorker(t, func(n Notification) {
		if n.Method == "run.finished" {
			var m map[string]any
			json.Unmarshal(n.Params, &m)
			finished <- m
		}
	})
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()

	var tools []map[string]any
	if err := b.Call(ctx, "list_tools", nil, &tools); err != nil {
		t.Fatal(err)
	}
	if len(tools) == 0 {
		t.Fatal("no tools loaded")
	}

	if err := b.Call(ctx, "run_tool", map[string]any{"run_id": "t1", "tool": "apdu_parser",
		"params": map[string]any{"hex": "00A4040008A000000809434343", "kind": "command"}}, nil); err != nil {
		t.Fatal(err)
	}
	select {
	case m := <-finished:
		if m["ok"] != true {
			t.Fatalf("run failed: %v", m)
		}
	case <-ctx.Done():
		t.Fatal("timeout waiting for run.finished")
	}
}

func TestCancel(t *testing.T) {
	finished := make(chan map[string]any, 1)
	b := startWorker(t, func(n Notification) {
		if n.Method == "run.finished" {
			var m map[string]any
			json.Unmarshal(n.Params, &m)
			finished <- m
		}
	})
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer cancel()
	b.Call(ctx, "run_tool", map[string]any{"run_id": "c1", "tool": "hello", "params": map[string]any{"times": 100}}, nil)
	time.Sleep(200 * time.Millisecond)
	if err := b.Call(ctx, "cancel", map[string]any{"run_id": "c1"}, nil); err != nil {
		t.Fatal(err)
	}
	select {
	case m := <-finished:
		if m["cancelled"] != true {
			t.Fatalf("expected cancelled, got %v", m)
		}
	case <-ctx.Done():
		t.Fatal("timeout")
	}
}

func TestUnknownMethodAndExit(t *testing.T) {
	b := startWorker(t, nil)
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	var rpcErr *RPCError
	if err := b.Call(ctx, "nope", nil, nil); err == nil || !asRPC(err, &rpcErr) {
		t.Fatalf("expected RPCError, got %v", err)
	}
	b.cmd.Process.Kill()
	<-b.Done()
	if err := b.Call(ctx, "ping", nil, nil); err != ErrClosed {
		t.Fatalf("expected ErrClosed, got %v", err)
	}
}

func asRPC(err error, target **RPCError) bool {
	e, ok := err.(*RPCError)
	if ok {
		*target = e
	}
	return ok
}
