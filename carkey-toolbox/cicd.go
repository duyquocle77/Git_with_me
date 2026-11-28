package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"time"

	"github.com/wailsapp/wails/v2/pkg/runtime"
)

// ---------------- CI/CD tab ----------------
// Pipelines are defined in python/cicd/cicd_config.json. Progress arrives as events:
// cicd.info, cicd.step, run.log, run.progress, run.finished (run ids start with "ci-").

func (a *App) CicdList() (json.RawMessage, error) {
	return a.callTimeout(30*time.Second, "cicd_list", nil)
}

// CicdPreview resolves the pipeline paths (release folder, output dirs) and checks which exist.
func (a *App) CicdPreview(pipeline string, params map[string]any, settings map[string]string) (json.RawMessage, error) {
	return a.callTimeout(10*time.Second, "cicd_preview",
		map[string]any{"pipeline": pipeline, "params": params, "settings": settings})
}

// CicdSaveSettings stores the per-machine path settings of a pipeline.
func (a *App) CicdSaveSettings(pipeline string, settings map[string]string) error {
	_, err := a.callTimeout(10*time.Second, "cicd_save_settings",
		map[string]any{"pipeline": pipeline, "settings": settings})
	return err
}

// PickFolder opens a folder chooser.
func (a *App) PickFolder(title, start string) (string, error) {
	return runtime.OpenDirectoryDialog(a.ctx, runtime.OpenDialogOptions{Title: title, DefaultDirectory: start})
}

func (a *App) CicdRun(pipeline string, params map[string]any, skip []string, settings map[string]string) (string, error) {
	buf := make([]byte, 6)
	_, _ = rand.Read(buf)
	runID := "ci-" + hex.EncodeToString(buf)
	_, err := a.callTimeout(30*time.Second, "cicd_run",
		map[string]any{"run_id": runID, "pipeline": pipeline, "params": params, "skip": skip, "settings": settings})
	return runID, err
}
