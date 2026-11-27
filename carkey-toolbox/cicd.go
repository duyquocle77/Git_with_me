package main

import (
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"time"
)

// ---------------- CI/CD tab ----------------
// Pipelines are defined in python/cicd/cicd_config.json. Progress arrives as events:
// cicd.info, cicd.step, run.log, run.progress, run.finished (run ids start with "ci-").

func (a *App) CicdList() (json.RawMessage, error) {
	return a.callTimeout(30*time.Second, "cicd_list", nil)
}

func (a *App) CicdRun(pipeline string, params map[string]any, skip []string) (string, error) {
	buf := make([]byte, 6)
	_, _ = rand.Read(buf)
	runID := "ci-" + hex.EncodeToString(buf)
	_, err := a.callTimeout(30*time.Second, "cicd_run",
		map[string]any{"run_id": runID, "pipeline": pipeline, "params": params, "skip": skip})
	return runID, err
}
