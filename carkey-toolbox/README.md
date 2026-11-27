# Car Key Toolbox

A local desktop app (Go + [Wails v2](https://wails.io)) that brings the smart car access (phone key)
team's Python tools, scripts and simulators together behind one UI, with logs, progress, Stop and result export.

## Architecture

```
┌──────────────── Wails app (single executable) ────────────────┐
│  Frontend (HTML/JS, WebView)                                  │
│     │  window.go.main.App.RunTool(...)        ▲ events        │
│     ▼                                         │ run.log …     │
│  Go: app.go  ──►  internal/pybridge  ─────────┘               │
└───────────────────────┬───────────────────────────────────────┘
                        │ JSON-RPC 2.0, one message per line, over stdin/stdout
                        ▼
            python/worker.py  (child process, lives for the whole session)
                        │ import
                        ▼
            python/tools/*.py  ← your tools (@tool decorator)
```

Why this design:
- **Python stays Python**: existing scripts and Python APIs only need an `@tool` wrapper; nothing is rewritten in Go.
- **stdin/stdout instead of HTTP**: no open ports, no corporate firewall issues, no conflicts between users.
- **Long-lived worker**: heavy libraries (python-can, bleak, crypto, ...) are imported once, so tools start fast.
- **Fault isolation**: a failing tool just shows red in the UI; if the worker crashes, click *Restart Python*.
- **Safe protocol channel**: `worker.py` reserves the original stdout fd for JSON-RPC, so `print()` or
  subprocesses writing to stdout cannot corrupt it. `print()` inside a tool is shown as a log line.

## Development setup

| | Requirements |
|---|---|
| All | Go ≥ 1.22, Python ≥ 3.10, `go install github.com/wailsapp/wails/v2/cmd/wails@v2.10.1` |
| Windows | WebView2 runtime (preinstalled on Windows 10/11) |
| macOS | Xcode command line tools |
| Ubuntu 24.04 | `sudo apt install libgtk-3-dev libwebkit2gtk-4.1-dev` (and build with `-tags webkit2_41`) |

Check your setup with `wails doctor`.

```bash
cd carkey-toolbox
python -m venv python/.venv                    # the app uses this venv automatically if present
python/.venv/bin/pip install -r python/requirements.txt   # Windows: python\.venv\Scripts\pip

wails dev                                      # dev mode, frontend reloads on change
wails build                                    # produces build/bin/carkey-toolbox(.exe)
go test ./internal/...                         # Go <-> Python bridge tests
```

## Adding a tool

Create `python/tools/<tool_name>.py` (start from a copy of `hello.py`):

```python
from toolkit import tool, Param

@tool(
    name="uwb_ranging_log",
    title="UWB ranging log analyzer",
    description="Read a UWB log and compute distance statistics",
    category="UWB",
    params=[
        Param("log_file", "file", label="Log file", required=True),
        Param("max_m", "float", label="Threshold (m)", default=3.0),
    ],
)
def run(ctx, log_file, max_m):
    ctx.log("Starting...")          # or print(...)
    for i, line in enumerate(open(log_file)):
        ctx.check_cancel()          # makes the Stop button work
        ...
        ctx.progress(i / total)     # 0..1
    return {"count": ...}           # any JSON-serialisable value -> shown in the UI and exportable
```

Click **⟳ Reload tools** in the app — no rebuild needed.

Parameter types: `string`, `int`, `float`, `bool`, `choice` (with `options=[...]`), `file` (adds a file picker).

Have a script/CLI you don't want to touch? See `tools/legacy_script.py` (runs a subprocess, streams output, supports Stop).

Tools outside the repo (e.g. a shared team folder): set `CARKEY_TOOLS_PATH=/path/a:/path/b`
(use `;` on Windows).

## Lab Control tab

The **🔌 Lab Control** tab shows one card per lab instrument: live status (polled every 2.5 s),
grouped action buttons and a per-device log. Files produced by actions (captures, screenshots,
logcat) get an **open** link.

Devices are configured per lab PC in `python/lab/lab_config.json` (copy
`lab_config.example.json`; the local file is git-ignored). Edit it and click **⟳ Reload config**.

| Driver | What it controls | Needs |
|---|---|---|
| `power_supply` | Output ON/OFF, power cycle, voltage/current setpoint, live V/I readback | SCPI over `tcp` / `serial` / `visa`, or `mock`. Vendor dialect via `preset` (`scpi`, `rigol_dp800`, `keysight_e36300`) or per-command overrides in `commands` |
| `saleae_logic` | Connect, start / stop & save, timed capture, optional CSV export | Logic 2 with automation enabled; `pip install logic2-automation` |
| `ellisys` | Start / stop recording, save trace | `backend: "command"` with your start/stop/save commands, or `mock` |
| `android_adb` | Wake/sleep/unlock, screenshot, BT / NFC / airplane on/off, BT snoop log mode, logcat to file, launch / force-stop app, reboot, any `adb shell` command | Android platform-tools (`adb`) on PATH or `"adb"` path in config |

Adding a new instrument = one Python class in `python/lab/` (see `base.py`), add its module to
`DRIVER_MODULES` in `manager.py`, then reference its `driver` name in the config. The UI is generated
from the `@action` metadata, so no frontend changes are needed.

Device actions run on a thread pool in the worker and each instrument has its own lock, so a
long capture never blocks the UI or other instruments.

## Environment variables

| Variable | Meaning |
|---|---|
| `CARKEY_PYTHON` | Specific interpreter path (default: `python/.venv`, then `python3`/`python`/`py` on PATH) |
| `CARKEY_PYTHON_DIR` | Directory containing `worker.py` (default: next to the executable, or `./python`) |
| `CARKEY_TOOLS_PATH` | Extra tool directories, separated by `:` (`;` on Windows) |
| `CARKEY_LAB_CONFIG` | Path to the lab config (default: `python/lab/lab_config.json`, else the example) |

## Sharing with colleagues

Phase 1 (simple, good enough internally): share a folder containing
```
carkey-toolbox(.exe)
python/            (worker.py, toolkit.py, tools/, requirements.txt)
```
Colleagues need Python installed plus `pip install -r python/requirements.txt` (or a `python/.venv`).

Later (see roadmap): bundle Python itself (python-build-standalone, or the embeddable zip on Windows)
so users don't need to install anything.

## Protocol (reference)

Go → Python: `ping`, `list_tools`, `reload_tools`, `run_tool {run_id, tool, params}`, `cancel {run_id}`,
`lab_list`, `lab_reload`, `lab_status {ids}`, `lab_action {device, action, params}`.
Python → Go (notifications): `run.log`, `run.progress`, `run.finished`, `worker.log`, `lab.log`.
Go forwards these notifications unchanged as Wails events to the frontend (plus `run.started`, `worker.status`).

## Roadmap

- [ ] Move the team's real tools into `python/tools/` (one category per area: BLE, UWB, NFC/SE, CAN, Diag, ...)
- [ ] Bundle an embedded Python + `requirements.txt` into the build for one-click sharing
- [ ] Long-running "session" tools (keep a serial/CAN/BLE connection open, send commands interactively) + live charts
- [ ] Shared configuration profiles (VIN, key ID, COM port, ...) across tools
- [ ] Shared team tool repository + version check / auto-update
- [ ] CI builds for Windows/macOS/Linux with GitHub Actions
