"""Ellisys Bluetooth analyzer / sniffer (Vanguard, Tracker, BEX400, ...).

Ellisys analyzers are driven by the Ellisys Bluetooth Analyzer software on the lab PC.
How that software is automated depends on the version and licence the lab has, so this
driver delegates the actual control to configurable commands:

    "backend": "mock"      simulated, for UI testing
    "backend": "command"   run shell commands from the config, e.g.
        "commands": {
            "start":  "python C:/lab/ellisys_ctl.py start",
            "stop":   "python C:/lab/ellisys_ctl.py stop",
            "save":   "python C:/lab/ellisys_ctl.py save \\"{path}\\"",
            "status": "python C:/lab/ellisys_ctl.py status"      (optional, stdout shown)
        }

When the exact automation interface is known, add a backend here that talks to it
directly (the public methods and UI do not change).
"""

from __future__ import annotations

import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

from toolkit import Param
from .base import Device, action, register


@register
class EllisysSniffer(Device):
    driver = "ellisys"
    title = "Ellisys Bluetooth sniffer"

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.backend = self.cfg("backend", "mock")
        self.capturing = False
        self.started = 0.0
        self.last_file = ""
        if self.backend not in {"mock", "command"}:
            raise ValueError(f"unknown backend '{self.backend}'")

    def _run(self, key: str, **kw: Any) -> str:
        if self.backend == "mock":
            time.sleep(0.2)
            return f"[mock] {key} {kw or ''}".strip()
        tmpl = self.cfg("commands", {}).get(key)
        if not tmpl:
            raise RuntimeError(f"no '{key}' command configured for {self.id}")
        cmd = tmpl.format(**kw)
        self.log(f"$ {cmd}")
        proc = subprocess.run(cmd if isinstance(cmd, str) and _is_windows() else shlex.split(cmd),
                              capture_output=True, text=True, timeout=float(self.cfg("timeout_s", 30)),
                              shell=False)
        out = (proc.stdout + proc.stderr).strip()
        if proc.returncode != 0:
            raise RuntimeError(f"'{key}' failed ({proc.returncode}): {out}")
        return out

    def _out_dir(self) -> Path:
        d = Path(self.cfg("output_dir", "~/CarKeyToolbox/ellisys")).expanduser()
        d.mkdir(parents=True, exist_ok=True)
        return d

    def status(self) -> dict[str, Any]:
        fields: dict[str, Any] = {"Backend": self.backend}
        if self.backend == "command" and self.cfg("commands", {}).get("status"):
            fields["Analyzer"] = self._run("status")[:200]
        if self.capturing:
            fields["Recording for"] = f"{time.time() - self.started:.0f} s"
        if self.last_file:
            fields["Last trace"] = self.last_file
        return {"connected": True, "state": "recording" if self.capturing else "idle", "fields": fields}

    @action(label="Start recording", group="Recording", style="success")
    def start(self) -> str:
        out = self._run("start")
        self.capturing, self.started = True, time.time()
        return out or "recording started"

    @action(label="Stop recording", group="Recording", style="danger")
    def stop(self) -> str:
        out = self._run("stop")
        self.capturing = False
        return out or "recording stopped"

    @action(label="Save trace", group="Recording",
            params=[Param("name", "string", label="File name", default="", help="empty = timestamp")])
    def save(self, name: str) -> dict[str, Any]:
        ext = self.cfg("trace_extension", ".btt")
        path = self._out_dir() / f"{name or time.strftime('trace_%Y%m%d_%H%M%S')}{ext}"
        out = self._run("save", path=str(path))
        self.last_file = str(path)
        return {"file": str(path), "output": out}


def _is_windows() -> bool:
    import os
    return os.name == "nt"
