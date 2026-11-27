"""Python worker cho Car Key Toolbox.

Go app khởi chạy file này như một tiến trình con và nói chuyện bằng
JSON-RPC 2.0, mỗi message là một dòng JSON trên stdin/stdout.

Methods (Go -> Python):
    ping                         -> {"python": "...", "pid": ...}
    list_tools                   -> [ToolInfo, ...]
    reload_tools                 -> [ToolInfo, ...]
    run_tool {run_id, tool, params} -> {"run_id": ...}   (chạy nền, trả về ngay)
    cancel   {run_id}            -> {"ok": bool}

Notifications (Python -> Go, không có "id"):
    run.log      {run_id, level, message}
    run.progress {run_id, value, message}
    run.finished {run_id, ok, result?, error?, cancelled}
    worker.log   {level, message}
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
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import toolkit  # noqa: E402

# ---------------------------------------------------------------------------
# Bảo vệ kênh protocol: giữ fd 1 gốc riêng cho JSON-RPC, rồi trỏ fd 1 sang
# stderr. Nhờ vậy print() hay subprocess ghi ra stdout không làm hỏng protocol.
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
# Chuyển print() của từng thread tool thành run.log
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
# Nạp tool
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
                notify("worker.log", {"level": "error", "message": f"Lỗi nạp {path.name}:\n{traceback.format_exc()}"})
            finally:
                toolkit._current_source = ""
    return [t.info() for t in toolkit._registry().values()]


# ---------------------------------------------------------------------------
# Chạy tool
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
        payload.update(cancelled=True, error="Đã dừng theo yêu cầu")
    except Exception as exc:  # lỗi của tool không được làm sập worker
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
        raise KeyError(f"không có tool '{name}'")
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


METHODS = {
    "ping": m_ping,
    "list_tools": m_list_tools,
    "reload_tools": m_reload_tools,
    "run_tool": m_run_tool,
    "cancel": m_cancel,
}


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
    try:
        result = fn(req.get("params") or {})
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
    # stdin đóng => Go app đã thoát, dừng các run đang chạy
    with _runs_lock:
        for ctx in _runs.values():
            ctx.request_cancel()


if __name__ == "__main__":
    main()
