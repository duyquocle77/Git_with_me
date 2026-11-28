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
A parameter can be shown conditionally with `show_if={"other_param": ["value1", "value2"]}`.

Bundled utilities: `crc_calc` – CRC-8/16/32/64 with AUTOSAR and common presets (self-checked against
the catalogue check values) or custom parameters, over hex / text / binary / Intel HEX / S-record input
with address range and gap fill; "ALL" mode compares every preset.

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

## CI/CD tab

The **🏗 CI/CD** tab runs a local release pipeline on the build PC. On Windows, every command is
executed through `cmd.exe /d /s /c` (no console window pops up). Pick a pipeline, fill in the
parameters (version, build configuration, build type), untick steps you want to skip, and press **Run pipeline**.
The tab shows a step timeline and the live log. **Stop** kills the whole running process tree.
On success the release folder is opened automatically.

Default pipeline `cc2745_release` (TI CC2745R10, SimpleLink Low Power F3 SDK 9.20.00.21, CCS 20.2):

| # | Step | How |
|---|---|---|
| 1 | Fresh CCS workspace | deletes and recreates `${WORKSPACE}` |
| 2-3 | Import FBL / APP projects | `ccs-server-cli.bat ... -application com.ti.ccs.apps.importProject -ccs.location <project>` |
| 4-5 | Build FBL / APP | `... -application com.ti.ccs.apps.buildProject -ccs.projects <name> -ccs.configuration <cfg> -ccs.buildType full` |
| 6 | (optional) hex | `tiarmhex --intel` if the projects don't already produce `.hex` in a post-build step |
| 7 | Collect | copies `*.hex *.map *.out` to `<release>/APP` and `<release>/FBL` |
| 8 | BMW create & sign | runs the OEM tool, expects `<release>/SIGNED/*.bin` |

Release folder layout:

```
<RELEASE_ROOT>/<VERSION>_<CONFIG>_<TIMESTAMP>/
  APP/     app .hex .map .out
  FBL/     fbl .hex .map .out
  SIGNED/  signed .bin from the BMW tool
  logs/    pipeline.log, import.log, build_fbl.log, build_app.log, sign.log
```

A step fails when any of these happens:
- the command exits with a non-zero code;
- an output line matches one of the step's `fail_patterns`, e.g. `**** Build Failed`, `gmake: ***`, `error #`;
- a file listed in `expect_files` was not (re)created during the step.

The last check catches headless builds that exit with code 0 even though compilation failed.

**Paths** (left panel) – source code root, APP/FBL project folders and names, CI workspace, release
target folder and release folder name pattern, CCS / SDK install folders and the BMW tool. Each field has
a 📁 browse button and a live ✓ / ✗ check, and the resolved release folder is previewed before running.
Values are saved per machine in `python/cicd/cicd_settings.json` (git-ignored); defaults come from the
pipeline's `settings` in the config.

The main panel shows a stage graph (running step spins, ✓ / ✕ badges, duration and ETA from previous
runs), a progress bar, a result card (artifacts grouped by APP / FBL / SIGNED / logs, click to open; on
failure the failed step and the first error lines) and a console with *All / Errors / Commands* filters.

Pipeline definitions live in `python/cicd/cicd_config.json` (copy the example, git-ignored).
Before the first real run, set these to match your machine:
- `CCS_ROOT` and `SDK_ROOT`
- `SOURCE_ROOT`, the project names and directories
- `RELEASE_ROOT`
- `BMW_TOOL` and its arguments (`TODO_ARGS`)

All values support `${VAR}`. A `cmd_setup` line
(e.g. `call "C:/path/setenv.bat"`) is prepended to every command on Windows.

> The CCS workspace used by the pipeline must not be open in the CCS IDE at the same time.

The `demo` pipeline runs the same flow with simulated build/sign tools, so the tab can be tried anywhere.

## Environment variables

| Variable | Meaning |
|---|---|
| `CARKEY_PYTHON` | Specific interpreter path (default: `python/.venv`, then `python3`/`python`/`py` on PATH) |
| `CARKEY_PYTHON_DIR` | Directory containing `worker.py` (default: next to the executable, or `./python`) |
| `CARKEY_TOOLS_PATH` | Extra tool directories, separated by `:` (`;` on Windows) |
| `CARKEY_CICD_CONFIG` | Path to the CI/CD config (default: `python/cicd/cicd_config.json`, else the example) |
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
`lab_list`, `lab_reload`, `lab_status {ids}`, `lab_action {device, action, params}`,
`cicd_list`, `cicd_run {run_id, pipeline, params, skip}`.
Python → Go (notifications): `run.log`, `run.progress`, `run.finished`, `worker.log`, `lab.log`,
`cicd.info`, `cicd.step`.
Go forwards these notifications unchanged as Wails events to the frontend (plus `run.started`, `worker.status`).

## Roadmap

- [ ] Move the team's real tools into `python/tools/` (one category per area: BLE, UWB, NFC/SE, CAN, Diag, ...)
- [ ] Bundle an embedded Python + `requirements.txt` into the build for one-click sharing
- [ ] Long-running "session" tools (keep a serial/CAN/BLE connection open, send commands interactively) + live charts
- [ ] Shared configuration profiles (VIN, key ID, COM port, ...) across tools
- [ ] Shared team tool repository + version check / auto-update
- [ ] CI builds for Windows/macOS/Linux with GitHub Actions
