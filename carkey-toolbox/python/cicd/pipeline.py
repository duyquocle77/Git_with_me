"""Small local CI/CD pipeline engine for the CI/CD tab.

Pipelines are defined in cicd_config.json (see cicd_config.example.json). Each step is
one of:

    command    run one or more command lines (in cmd.exe on Windows, /bin/sh elsewhere)
               keys: cmd (str | [str]), cwd, env, timeout_s, log, fail_patterns,
                     expect_files, ok_exit_codes
    clean_dir  delete and recreate a directory (e.g. a fresh CCS workspace)
               keys: path
    collect    copy build artifacts into the release folder
               keys: items: [{"from": glob, "to": sub-folder of RELEASE_DIR, "required": true}]

Strings may use ${VAR}. Variables come from (later wins): built-ins, global "vars",
pipeline "vars", then the parameters entered in the UI. Plain %VAR% is left for cmd.exe.
"""

from __future__ import annotations

import getpass
import glob
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from toolkit import Cancelled, Param, RunContext  # noqa: E402

VAR_RE = re.compile(r"\$\{(\w+)\}")
IS_WIN = os.name == "nt"


class PipelineError(Exception):
    pass


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def config_path() -> Path:
    env = os.environ.get("CARKEY_CICD_CONFIG")
    if env:
        return Path(env)
    local = HERE / "cicd_config.json"  # per machine, not committed
    return local if local.exists() else HERE / "cicd_config.example.json"


def load_config() -> dict[str, Any]:
    return json.loads(config_path().read_text(encoding="utf-8"))


def _params(p: dict[str, Any]) -> list[Param]:
    return [Param(**x) for x in p.get("params", [])]


def list_pipelines() -> dict[str, Any]:
    cfg = load_config()
    out = []
    for p in cfg.get("pipelines", []):
        out.append({
            "id": p["id"], "name": p.get("name", p["id"]), "description": p.get("description", ""),
            "params": [asdict(x) for x in _params(p)],
            "steps": [{"id": s["id"], "name": s.get("name", s["id"]), "type": s.get("type", "command"),
                       "enabled": s.get("enabled", True)} for s in p.get("steps", [])],
            "open_release_dir": p.get("open_release_dir", True),
        })
    return {"config": str(config_path()), "pipelines": out}


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
class Runner:
    def __init__(self, ctx: RunContext, emit: Callable[[str, dict], None], pipeline_id: str,
                 params: dict[str, Any], skip: list[str]):
        self.ctx = ctx
        self.emit = emit
        self.cfg = load_config()
        self.pipe = next((p for p in self.cfg.get("pipelines", []) if p["id"] == pipeline_id), None)
        if self.pipe is None:
            raise PipelineError(f"unknown pipeline '{pipeline_id}'")
        self.skip = set(skip or [])
        self.logfile: Any = None
        self.vars = self._resolve_vars(params)

    # ---- variables ----
    def _resolve_vars(self, raw_params: dict[str, Any]) -> dict[str, str]:
        now = time.localtime()
        raw: dict[str, Any] = {
            "PYTHON": sys.executable,
            "TOOLBOX_PY": str(HERE.parent),
            "TIMESTAMP": time.strftime("%Y%m%d_%H%M%S", now),
            "DATE": time.strftime("%Y-%m-%d", now),
            "PIPELINE": self.pipe["id"],
            "HOST": socket.gethostname(),
            "USER": getpass.getuser(),
        }
        raw.update(self.cfg.get("vars", {}))
        raw.update(self.pipe.get("vars", {}))
        for p in _params(self.pipe):
            v = p.coerce(raw_params.get(p.name))
            raw[p.name] = ("true" if v else "false") if isinstance(v, bool) else ("" if v is None else str(v))

        resolved: dict[str, str] = {}

        def resolve(name: str, stack: tuple[str, ...]) -> str:
            if name in resolved:
                return resolved[name]
            if name in stack:
                raise PipelineError(f"variable cycle: {' -> '.join(stack + (name,))}")
            if name not in raw:
                raise PipelineError(f"undefined variable ${{{name}}}")
            val = VAR_RE.sub(lambda m: resolve(m.group(1), stack + (name,)), str(raw[name]))
            if val.startswith("~"):
                val = os.path.expanduser(val)
            resolved[name] = val
            return val

        for k in raw:
            resolve(k, ())
        if "RELEASE_DIR" not in resolved:
            raise PipelineError("pipeline must define RELEASE_DIR")
        return resolved

    def x(self, s: Any) -> str:
        """Expand ${VAR} in a string."""
        def sub(m: re.Match) -> str:
            if m.group(1) not in self.vars:
                raise PipelineError(f"undefined variable ${{{m.group(1)}}}")
            return self.vars[m.group(1)]
        return VAR_RE.sub(sub, str(s))

    # ---- logging ----
    def log(self, msg: str, level: str = "info") -> None:
        self.ctx.log(msg, level)
        if self.logfile:
            self.logfile.write(f"{time.strftime('%H:%M:%S')} [{level}] {msg}\n")
            self.logfile.flush()

    def step_event(self, i: int, step: dict, status: str, **kw: Any) -> None:
        self.emit("cicd.step", {"run_id": self.ctx.run_id, "index": i, "id": step["id"], "status": status, **kw})

    # ---- main ----
    def run(self) -> dict[str, Any]:
        steps = self.pipe.get("steps", [])
        release = Path(self.vars["RELEASE_DIR"])
        # Validate every step's variables before touching anything.
        def check(v: Any) -> None:
            if isinstance(v, str):
                self.x(v)
            elif isinstance(v, list):
                for e in v:
                    check(e)
            elif isinstance(v, dict):
                for k, e in v.items():
                    if k != "fail_patterns":  # regexes may contain $ / { }
                        check(e)
        for s in steps:
            check(s)

        (release / "logs").mkdir(parents=True, exist_ok=True)
        self.logfile = open(release / "logs" / "pipeline.log", "a", encoding="utf-8")
        self.emit("cicd.info", {"run_id": self.ctx.run_id, "release_dir": str(release),
                                "vars": {k: v for k, v in self.vars.items() if k.isupper()}})
        self.log(f"Pipeline '{self.pipe.get('name', self.pipe['id'])}' -> {release}", "sys")

        results = []
        try:
            for i, step in enumerate(steps):
                self.ctx.check_cancel()
                enabled = step.get("enabled", True) and step["id"] not in self.skip
                if not enabled:
                    self.step_event(i, step, "skipped")
                    results.append({"id": step["id"], "status": "skipped"})
                    continue
                self.ctx.progress(i / len(steps), step.get("name", step["id"]))
                self.log(f"━━ [{i + 1}/{len(steps)}] {step.get('name', step['id'])}", "sys")
                self.step_event(i, step, "running")
                t0 = time.time()
                try:
                    msg = self.run_step(step) or ""
                except Cancelled:
                    self.step_event(i, step, "cancelled", duration_s=round(time.time() - t0, 1))
                    raise
                except Exception as exc:
                    dur = round(time.time() - t0, 1)
                    self.log(f"✖ {step['id']} failed after {dur}s: {exc}", "error")
                    self.step_event(i, step, "failed", duration_s=dur, message=str(exc))
                    if step.get("continue_on_error"):
                        results.append({"id": step["id"], "status": "failed", "error": str(exc)})
                        continue
                    raise PipelineError(f"step '{step['id']}' failed: {exc}") from exc
                dur = round(time.time() - t0, 1)
                self.log(f"✔ {step['id']} ({dur}s) {msg}", "sys")
                self.step_event(i, step, "ok", duration_s=dur, message=msg)
                results.append({"id": step["id"], "status": "ok", "duration_s": dur})
            self.ctx.progress(1.0, "done")
            self.log("Pipeline finished successfully", "sys")
            return {"release_dir": str(release), "steps": results,
                    "open_release_dir": self.pipe.get("open_release_dir", True)}
        finally:
            self.logfile.close()
            self.logfile = None

    # ---- steps ----
    def run_step(self, step: dict) -> str:
        kind = step.get("type", "command")
        if kind == "command":
            return self.step_command(step)
        if kind == "clean_dir":
            return self.step_clean_dir(step)
        if kind == "collect":
            return self.step_collect(step)
        raise PipelineError(f"unknown step type '{kind}'")

    def step_clean_dir(self, step: dict) -> str:
        p = Path(self.x(step["path"])).resolve()
        if len(p.parts) < 3 or p == Path.home().resolve():
            raise PipelineError(f"refusing to delete suspicious path {p}")
        if p.exists():
            shutil.rmtree(p)
        p.mkdir(parents=True)
        return f"{p} recreated"

    def step_collect(self, step: dict) -> str:
        release = Path(self.vars["RELEASE_DIR"])
        copied = 0
        for item in step.get("items", []):
            pattern = self.x(item["from"])
            dest = release / self.x(item.get("to", ""))
            matches = [m for m in glob.glob(pattern) if os.path.isfile(m)]
            if not matches:
                if item.get("required", True):
                    raise PipelineError(f"no file matches {pattern}")
                self.log(f"(optional) nothing matches {pattern}", "warn")
                continue
            dest.mkdir(parents=True, exist_ok=True)
            for m in matches:
                shutil.copy2(m, dest / os.path.basename(m))
                self.log(f"  {m}  ->  {dest / os.path.basename(m)}")
                copied += 1
        return f"{copied} file(s) collected"

    def step_command(self, step: dict) -> str:
        cmds = step["cmd"] if isinstance(step["cmd"], list) else [step["cmd"]]
        cwd = self.x(step["cwd"]) if step.get("cwd") else None
        if cwd:
            Path(cwd).mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env.update({k: self.x(v) for k, v in self.cfg.get("env", {}).items()})
        env.update({k: self.x(v) for k, v in step.get("env", {}).items()})
        setup = self.x(self.cfg.get("cmd_setup", "")) if IS_WIN else ""
        patterns = [re.compile(p) for p in step.get("fail_patterns", [])]
        ok_codes = set(step.get("ok_exit_codes", [0]))
        timeout = float(step.get("timeout_s", 1800))
        log_name = step.get("log")
        step_log = None
        if log_name:
            step_log = open(Path(self.vars["RELEASE_DIR"]) / "logs" / self.x(log_name), "a", encoding="utf-8")
        started = time.time()
        try:
            for raw in cmds:
                line = self.x(raw)
                if setup:
                    line = f"{setup} && {line}"
                self.log(f"$ {line}", "sys")
                if step_log:
                    step_log.write(f"$ {line}\n")
                code, hits = self._exec(line, cwd, env, timeout, patterns, step_log)
                if code not in ok_codes:
                    raise PipelineError(f"exit code {code}")
                if hits:
                    raise PipelineError(f"output matched failure pattern: {hits[0]}")
        finally:
            if step_log:
                step_log.close()
        for pat in step.get("expect_files", []):
            pattern = self.x(pat)
            found = [f for f in glob.glob(pattern) if os.path.getmtime(f) >= started - 2]
            if not found:
                raise PipelineError(f"expected output not produced: {pattern}")
        return ""

    def _exec(self, line: str, cwd: str | None, env: dict, timeout: float,
              patterns: list[re.Pattern], step_log: Any) -> tuple[int, list[str]]:
        if IS_WIN:
            # /s + outer quotes: cmd.exe runs the line exactly as written (inner quotes preserved).
            args: Any = f'cmd.exe /d /s /c "{line}"'
            extra = {"creationflags": 0x08000000 | 0x00000200}  # CREATE_NO_WINDOW | NEW_PROCESS_GROUP
            encoding = "oem"
        else:
            args = ["/bin/sh", "-c", line]
            extra = {"start_new_session": True}
            encoding = "utf-8"
        proc = subprocess.Popen(args, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, **extra)
        hits: list[str] = []

        def reader() -> None:
            assert proc.stdout is not None
            for b in iter(proc.stdout.readline, b""):
                text = b.decode(encoding, errors="replace").rstrip("\r\n")
                bad = any(p.search(text) for p in patterns)
                if bad:
                    hits.append(text)
                self.log(text, "error" if bad else "info")
                if step_log:
                    step_log.write(text + "\n")

        t = threading.Thread(target=reader, daemon=True)
        t.start()
        deadline = time.time() + timeout
        while proc.poll() is None:
            if self.ctx.cancelled or time.time() > deadline:
                self._kill(proc)
                t.join(2)
                if self.ctx.cancelled:
                    raise Cancelled()
                raise PipelineError(f"timeout after {timeout:.0f}s")
            time.sleep(0.2)
        t.join(5)
        return proc.returncode, hits

    @staticmethod
    def _kill(proc: subprocess.Popen) -> None:
        try:
            if IS_WIN:
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True,
                               creationflags=0x08000000)
            else:
                os.killpg(proc.pid, signal.SIGTERM)
        except Exception:
            proc.kill()

