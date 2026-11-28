//go:build windows

package pybridge

import (
	"os/exec"
	"syscall"
)

// Do not pop up a console window when starting python.exe from the GUI app.
func hideWindow(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000} // CREATE_NO_WINDOW
}
