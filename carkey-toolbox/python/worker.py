"""Python worker for Car Key Toolbox.

The Go app starts this file as a child process and talks to it using
JSON-RPC 2.0, one JSON message per line on stdin/stdout.

Methods (Go -> Python):
    ping                         -> {"python": "...", "pid": ...}
    list_tools                   -> [ToolInfo, ...]
    reload_tools                 -> [ToolInfo, ...]
    run_tool {run_id, tool, params} -> {"run_id": ...}   (runs in background, returns immediately)
    cancel   {run_id}            -> {"ok": bool}

Notifications (Python -> Go, no "id"):
    run.log      {run_id, level, message}
    run.progress {run_id, value, message}
    run.finished {run_id, ok, result?, error?, cancelled}
    worker.log   {level, message}
    lab.log      {device, level, message}

CI/CD: cicd_list, cicd_run {run_id, pipeline, params, skip}; notifications cicd.info, cicd.step
(plus run.log / run.progress / run.finished; cancel with "cancel").
Lab Control methods: lab_list, lab_reload, lab_status {ids?}, lab_action {device, action, params}
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import platform
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import toolkit  # noqa: E402

# ---------------------------------------------------------------------------
# Protect the protocol channel: keep the original fd 1 for JSON-RPC only and
# point fd 1 at stderr, so print() or subprocesses writing to stdout cannot corrupt it.
# ---------------------------------------------------------------------------
_proto_fd = os.dup(1)
os.dup2(2, 1)
_proto = os.fdopen(_proto_fd, "w", encoding="utf-8", buffering=1, newline="\n")
_proto_lock = threading.Lock()


def _send(msg: dict[str, Any]) -> None:
    line = json.dumps(msg, ensure_ascii=False, default=str)
    with _proto_lock:
        _proto.write(line + "\n")
        _proto.flush()


def notify(method: str, params: dict[str, Any]) -> None:
    _send({"jsonrpc": "2.0", "method": method, "params": params})


# ---------------------------------------------------------------------------
# Route print() from each tool thread to run.log
# ---------------------------------------------------------------------------
_thread_ctx = threading.local()


class _RoutedStream(io.TextIOBase):
    def __init__(self, fallback, level: str):
        self._fallback = fallback
        self._level = level

    def writable(self) -> bool:
        return True

    def write(self, s: str) -> int:
        ctx = getattr(_thread_ctx, "ctx", None)
        if ctx is None:
            self._fallback.write(s)
            self._fallback.flush()
            return len(s)
        buf = getattr(_thread_ctx, "buf_" + self._level, "") + s
        *lines, rest = buf.split("\n")
        for line in lines:
            ctx.log(line, self._level)
        setattr(_thread_ctx, "buf_" + self._level, rest)
        return len(s)

    def flush_thread(self) -> None:
        ctx = getattr(_thread_ctx, "ctx", None)
        rest = getattr(_thread_ctx, "buf_" + self._level, "")
        if ctx is not None and rest:
            ctx.log(rest, self._level)
        setattr(_thread_ctx, "buf_" + self._level, "")


_stdout = _RoutedStream(sys.__stderr__, "info")
_stderr = _RoutedStream(sys.__stderr__, "error")
sys.stdout = _stdout
sys.stderr = _stderr


# ---------------------------------------------------------------------------
# Tool loading
# ---------------------------------------------------------------------------
def tools_dirs() -> list[Path]:
    dirs = [HERE / "tools"]
    extra = os.environ.get("CARKEY_TOOLS_PATH", "")
    dirs += [Path(p) for p in extra.split(os.pathsep) if p]
    return [d for d in dirs if d.is_dir()]


def load_tools() -> list[dict[str, Any]]:
    toolkit._REGISTRY.clear()
    for d in tools_dirs():
        if str(d) not in sys.path:
            sys.path.insert(0, str(d))
        for path in sorted(d.glob("*.py")):
            if path.name.startswith("_"):
                continue
            mod_name = f"carkey_tool_{path.stem}"
            toolkit._current_source = str(path)
            try:
                spec = importlib.util.spec_from_file_location(mod_name, path)
                assert spec and spec.loader
                module = importlib.util.module_from_spec(spec)
                sys.modules[mod_name] = module
                spec.loader.exec_module(module)
            except Exception:
                notify("worker.log", {"level": "error", "message": f"Failed to load {path.name}:\n{traceback.format_exc()}"})
            finally:
                toolkit._current_source = ""
    return [t.info() for t in toolkit._registry().values()]


# ---------------------------------------------------------------------------
# Running tools
# ---------------------------------------------------------------------------
_runs: dict[str, toolkit.RunContext] = {}
_runs_lock = threading.Lock()


def _run_thread(spec: toolkit.ToolSpec, ctx: toolkit.RunContext, raw_params: dict[str, Any]) -> None:
    _thread_ctx.ctx = ctx
    payload: dict[str, Any] = {"run_id": ctx.run_id, "ok": False, "cancelled": False}
    try:
        kwargs = {p.name: p.coerce(raw_params.get(p.name)) for p in spec.params}
        result = spec.func(ctx, **kwargs)
        payload.update(ok=True, result=result)
    except toolkit.Cancelled:
        payload.update(cancelled=True, error="Stopped by user")
    except Exception as exc:  # a failing tool must never crash the worker
        payload.update(error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc())
    finally:
        _stdout.flush_thread()
        _stderr.flush_thread()
        _thread_ctx.ctx = None
        with _runs_lock:
            _runs.pop(ctx.run_id, None)
        try:
            json.dumps(payload.get("result"), default=str)
        except Exception:
            payload["result"] = repr(payload.get("result"))
        notify("run.finished", payload)


def m_ping(_: dict) -> dict:
    return {"python": platform.python_version(), "executable": sys.executable, "pid": os.getpid(),
            "tools_dirs": [str(d) for d in tools_dirs()]}


def m_list_tools(_: dict) -> list:
    return [t.info() for t in toolkit._registry().values()]


def m_reload_tools(_: dict) -> list:
    return load_tools()


def m_run_tool(p: dict) -> dict:
    run_id, name = p["run_id"], p["tool"]
    spec = toolkit._registry().get(name)
    if spec is None:
        raise KeyError(f"unknown tool '{name}'")
    ctx = toolkit.RunContext(run_id, notify)
    with _runs_lock:
        _runs[run_id] = ctx
    threading.Thread(target=_run_thread, args=(spec, ctx, p.get("params") or {}), daemon=True,
                     name=f"run-{run_id}").start()
    return {"run_id": run_id}


def m_cancel(p: dict) -> dict:
    with _runs_lock:
        ctx = _runs.get(p["run_id"])
    if ctx:
        ctx.request_cancel()
    return {"ok": ctx is not None}


# ---------------------------------------------------------------------------
# Lab Control (instrument drivers in python/lab/)
# ---------------------------------------------------------------------------
_lab = None
_lab_init = threading.Lock()


def lab():
    global _lab
    with _lab_init:
        if _lab is not None:
            return _lab
        from lab.manager import LabManager
        mgr = LabManager(notify)
        mgr.load()
        _lab = mgr
        return _lab


def m_lab_list(_: dict) -> dict:
    return lab().list()


def m_lab_reload(_: dict) -> dict:
    lab().load()
    return lab().list()


def m_lab_status(p: dict) -> dict:
    ids = p.get("ids") or list(lab().devices)
    return {i: lab().get(i).safe_status() for i in ids if i in lab().devices}


def m_lab_action(p: dict):
    dev = lab().get(p["device"])
    dev.log(f"> {p['action']}", "sys")
    return dev.run_action(p["action"], p.get("params") or {})


# ---------------------------------------------------------------------------
# CI/CD pipelines (python/cicd/)
# ---------------------------------------------------------------------------
def m_cicd_list(_: dict) -> dict:
    from cicd import pipeline
    return pipeline.list_pipelines()


def _cicd_thread(ctx: toolkit.RunContext, p: dict) -> None:
    from cicd import pipeline
    payload: dict[str, Any] = {"run_id": ctx.run_id, "ok": False, "cancelled": False}
    try:
        runner = pipeline.Runner(ctx, notify, p["pipeline"], p.get("params") or {}, p.get("skip") or [],
                                 p.get("settings"))
        payload.update(ok=True, result=runner.run())
    except toolkit.Cancelled:
        payload.update(cancelled=True, error="Stopped by user")
    except Exception as exc:
        payload.update(error=f"{exc}" if isinstance(exc, pipeline.PipelineError) else f"{type(exc).__name__}: {exc}")
    finally:
        with _runs_lock:
            _runs.pop(ctx.run_id, None)
        notify("run.finished", payload)


def m_cicd_preview(p: dict) -> dict:
    from cicd import pipeline
    return pipeline.preview(p["pipeline"], p.get("params") or {}, p.get("settings") or {})


def m_cicd_save_settings(p: dict) -> dict:
    from cicd import pipeline
    pipeline.save_settings(p["pipeline"], p.get("settings") or {})
    return {"ok": True}


def m_cicd_run(p: dict) -> dict:
    ctx = toolkit.RunContext(p["run_id"], notify)
    with _runs_lock:
        if any(r.startswith("ci-") for r in _runs):
            raise RuntimeError("a pipeline is already running")
        _runs[ctx.run_id] = ctx
    threading.Thread(target=_cicd_thread, args=(ctx, p), daemon=True, name=f"cicd-{ctx.run_id}").start()
    return {"run_id": ctx.run_id}


METHODS = {
    "ping": m_ping,
    "list_tools": m_list_tools,
    "reload_tools": m_reload_tools,
    "run_tool": m_run_tool,
    "cancel": m_cancel,
    "lab_list": m_lab_list,
    "lab_reload": m_lab_reload,
    "lab_status": m_lab_status,
    "lab_action": m_lab_action,
    "cicd_list": m_cicd_list,
    "cicd_run": m_cicd_run,
    "cicd_preview": m_cicd_preview,
    "cicd_save_settings": m_cicd_save_settings,
}

# Methods that may block (talking to instruments) run on a thread pool so the
# request loop stays responsive. Responses can therefore arrive out of order,
# which JSON-RPC (and pybridge) handle via the request id.
THREADED = {"lab_status", "lab_action", "lab_reload"}
_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="rpc")


def handle(line: str) -> None:
    try:
        req = json.loads(line)
    except json.JSONDecodeError as exc:
        _send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": str(exc)}})
        return
    rid = req.get("id")
    fn = METHODS.get(req.get("method", ""))
    if fn is None:
        _send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"method not found: {req.get('method')}"}})
        return
    if req.get("method") in THREADED:
        _pool.submit(_execute, fn, rid, req.get("params") or {})
    else:
        _execute(fn, rid, req.get("params") or {})


def _execute(fn, rid, params: dict) -> None:
    try:
        result = fn(params)
        _send({"jsonrpc": "2.0", "id": rid, "result": result})
    except Exception as exc:
        _send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32000, "message": f"{type(exc).__name__}: {exc}",
                                                        "data": traceback.format_exc()}})


def main() -> None:
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    load_tools()
    for line in sys.stdin:
        line = line.strip()
        if line:
            handle(line)
    # stdin closed => the Go app exited; stop any running tools
    with _runs_lock:
        for ctx in _runs.values():
            ctx.request_cancel()
    if _lab is not None:
        _lab.close()


if __name__ == "__main__":
    main()
