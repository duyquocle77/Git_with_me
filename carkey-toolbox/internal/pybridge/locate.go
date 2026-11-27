package pybridge

import (
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
)

// FindPythonDir tìm thư mục chứa worker.py theo thứ tự:
// $CARKEY_PYTHON_DIR, cạnh file thực thi, Resources/ (macOS .app), thư mục hiện tại.
func FindPythonDir() (string, error) {
	var candidates []string
	if d := os.Getenv("CARKEY_PYTHON_DIR"); d != "" {
		candidates = append(candidates, d)
	}
	if exe, err := os.Executable(); err == nil {
		dir := filepath.Dir(exe)
		candidates = append(candidates,
			filepath.Join(dir, "python"),
			filepath.Join(dir, "..", "Resources", "python"),
			filepath.Join(dir, "..", "..", "python"), // build/bin/<exe> khi dev
		)
	}
	if wd, err := os.Getwd(); err == nil {
		candidates = append(candidates, filepath.Join(wd, "python"))
	}
	for _, c := range candidates {
		if _, err := os.Stat(filepath.Join(c, "worker.py")); err == nil {
			return filepath.Abs(c)
		}
	}
	return "", errors.New("không tìm thấy thư mục python/ chứa worker.py (đặt CARKEY_PYTHON_DIR)")
}

// FindInterpreter chọn Python: $CARKEY_PYTHON, venv trong python/.venv, rồi PATH.
func FindInterpreter(pyDir string) (string, error) {
	if p := os.Getenv("CARKEY_PYTHON"); p != "" {
		return p, nil
	}
	venv := filepath.Join(pyDir, ".venv", "bin", "python")
	if runtime.GOOS == "windows" {
		venv = filepath.Join(pyDir, ".venv", "Scripts", "python.exe")
	}
	if _, err := os.Stat(venv); err == nil {
		return venv, nil
	}
	for _, name := range []string{"python3", "python", "py"} {
		if p, err := exec.LookPath(name); err == nil {
			return p, nil
		}
	}
	return "", errors.New("không tìm thấy Python (cài Python 3.10+ hoặc đặt CARKEY_PYTHON)")
}
