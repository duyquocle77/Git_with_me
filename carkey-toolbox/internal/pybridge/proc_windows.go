//go:build windows

package pybridge

import (
	"os/exec"
	"syscall"
)

// Không bật cửa sổ console đen khi chạy python.exe từ app GUI.
func hideWindow(cmd *exec.Cmd) {
	cmd.SysProcAttr = &syscall.SysProcAttr{HideWindow: true, CreationFlags: 0x08000000} // CREATE_NO_WINDOW
}
