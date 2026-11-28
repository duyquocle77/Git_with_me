"""Loads lab_config.json and instantiates the configured devices."""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
from typing import Any, Callable

from . import base

HERE = Path(__file__).resolve().parent
DRIVER_MODULES = ["power_supply", "saleae_logic", "ellisys", "android_adb"]


def config_path() -> Path:
    env = os.environ.get("CARKEY_LAB_CONFIG")
    if env:
        return Path(env)
    local = HERE / "lab_config.json"          # per-machine, not committed
    return local if local.exists() else HERE / "lab_config.example.json"


class LabManager:
    def __init__(self, emit: Callable[[str, dict], None]):
        self._emit = emit
        self.devices: dict[str, base.Device] = {}
        self.errors: list[str] = []
        self.path: Path | None = None

    def load(self) -> None:
        self.close()
        self.errors = []
        for mod in DRIVER_MODULES:
            importlib.reload(importlib.import_module(f"lab.{mod}"))
        self.path = config_path()
        cfg = json.loads(self.path.read_text(encoding="utf-8"))
        for entry in cfg.get("devices", []):
            if not entry.get("enabled", True):
                continue
            dev_id, driver = entry.get("id"), entry.get("driver")
            cls = base.drivers().get(driver)
            if cls is None:
                self.errors.append(f"{dev_id}: unknown driver '{driver}'")
                continue
            try:
                self.devices[dev_id] = cls(dev_id, entry.get("name", ""), entry, self._emit)
            except Exception as exc:
                self.errors.append(f"{dev_id}: {type(exc).__name__}: {exc}")

    def close(self) -> None:
        for d in self.devices.values():
            try:
                d.close()
            except Exception:
                pass
        self.devices = {}

    def list(self) -> dict[str, Any]:
        return {"config": str(self.path), "errors": self.errors,
                "devices": [d.info() for d in self.devices.values()]}

    def get(self, dev_id: str) -> base.Device:
        d = self.devices.get(dev_id)
        if d is None:
            raise KeyError(f"unknown device '{dev_id}'")
        return d
