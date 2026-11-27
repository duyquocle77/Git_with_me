package main

import (
	"context"
	"encoding/json"
	"fmt"
	"os/exec"
	goruntime "runtime"
	"time"
)

// ---------------- Lab Control tab ----------------
// Instrument drivers live in python/lab/. These methods are thin pass-throughs;
// results are returned as raw JSON so the frontend sees exactly what Python sent.

func (a *App) callTimeout(timeout time.Duration, method string, params any) (json.RawMessage, error) {
	b, err := a.getBridge()
	if err != nil {
		return nil, err
	}
	ctx, cancel := context.WithTimeout(a.ctx, timeout)
	defer cancel()
	var out json.RawMessage
	err = b.Call(ctx, method, params, &out)
	return out, err
}

func (a *App) LabList() (json.RawMessage, error) {
	return a.callTimeout(30*time.Second, "lab_list", nil)
}

func (a *App) LabReload() (json.RawMessage, error) {
	return a.callTimeout(30*time.Second, "lab_reload", nil)
}

func (a *App) LabStatus(ids []string) (json.RawMessage, error) {
	return a.callTimeout(20*time.Second, "lab_status", map[string]any{"ids": ids})
}

// LabAction runs one device action. Long actions (timed captures, power cycles)
// are allowed up to 10 minutes.
func (a *App) LabAction(device, action string, params map[string]any) (json.RawMessage, error) {
	return a.callTimeout(10*time.Minute, "lab_action",
		map[string]any{"device": device, "action": action, "params": params})
}

// OpenPath opens a file or folder with the OS default application.
func (a *App) OpenPath(path string) error {
	var cmd *exec.Cmd
	switch goruntime.GOOS {
	case "windows":
		cmd = exec.Command("explorer", path)
	case "darwin":
		cmd = exec.Command("open", path)
	default:
		cmd = exec.Command("xdg-open", path)
	}
	if err := cmd.Start(); err != nil {
		return fmt.Errorf("open %s: %w", path, err)
	}
	go cmd.Wait()
	return nil
}
