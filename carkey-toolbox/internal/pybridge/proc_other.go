//go:build !windows

package pybridge

import "os/exec"

func hideWindow(cmd *exec.Cmd) {}
