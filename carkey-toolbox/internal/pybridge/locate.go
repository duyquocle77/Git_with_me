package pybridge

import (
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
)

// FindPythonDir locates the directory containing worker.py, in order:
// $CARKEY_PYTHON_DIR, next to the executable, Resources/ (macOS .app), current directory.
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
			filepath.Join(dir, "..", "..", "python"), // build/bin/<exe> during development
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
	return "", errors.New("cannot find the python/ directory containing worker.py (set CARKEY_PYTHON_DIR)")
}

// FindInterpreter picks Python: $CARKEY_PYTHON, then python/.venv, then PATH.
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
	return "", errors.New("Python not found (install Python 3.10+ or set CARKEY_PYTHON)")
}
