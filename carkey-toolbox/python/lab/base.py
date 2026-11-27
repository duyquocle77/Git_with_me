"""Base classes for lab instrument drivers.

A driver is a Device subclass. Methods decorated with @action become buttons in the
"Lab Control" tab; status() feeds the live status panel of the device card.

    class MyRelay(Device):
        driver = "my_relay"
        title = "USB relay"

        @action(label="ON", group="Output", style="primary")
        def on(self):
            ...
            return "relay on"   # shown in the card log
"""

from __future__ import annotations

import inspect
import threading
from typing import Any, Callable

from toolkit import Param

_DRIVERS: dict[str, type["Device"]] = {}


def register(cls: type["Device"]) -> type["Device"]:
    _DRIVERS[cls.driver] = cls
    return cls


def drivers() -> dict[str, type["Device"]]:
    return _DRIVERS


def action(*, label: str, group: str = "", style: str = "", params: list[Param] | None = None,
           confirm: str = "", help: str = "") -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Mark a Device method as a UI action.

    style: "" | "primary" | "danger" | "success"
    confirm: if set, the UI asks this question before running the action.
    """
    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        fn._lab_action = {  # type: ignore[attr-defined]
            "name": fn.__name__, "label": label, "group": group, "style": style,
            "params": list(params or []), "confirm": confirm, "help": help,
        }
        return fn
    return deco


class Device:
    driver = ""
    title = ""

    def __init__(self, dev_id: str, name: str, config: dict[str, Any], emit: Callable[[str, dict], None]):
        self.id = dev_id
        self.name = name or self.title or dev_id
        self.config = config
        self._emit = emit
        # One instrument = one lock: actions and status polls never interleave on the wire.
        self.lock = threading.RLock()

    # ---- helpers for drivers ----
    def log(self, message: str, level: str = "info") -> None:
        self._emit("lab.log", {"device": self.id, "level": level, "message": str(message)})

    def cfg(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, default)

    # ---- override in drivers ----
    def status(self) -> dict[str, Any]:
        """Return {"connected": bool, "state": short text, "fields": {label: value}}."""
        return {"connected": False, "state": "unknown", "fields": {}}

    def close(self) -> None:
        pass

    # ---- framework ----
    @classmethod
    def actions(cls) -> list[dict[str, Any]]:
        out = []
        for _, fn in inspect.getmembers(cls, predicate=inspect.isfunction):
            meta = getattr(fn, "_lab_action", None)
            if meta:
                out.append(meta)
        # keep source order so cards look like the driver author intended
        out.sort(key=lambda m: getattr(cls, m["name"]).__code__.co_firstlineno)
        return out

    def info(self) -> dict[str, Any]:
        from dataclasses import asdict
        return {
            "id": self.id, "name": self.name, "driver": self.driver, "title": self.title,
            "actions": [{**a, "params": [asdict(p) for p in a["params"]]} for a in self.actions()],
        }

    def run_action(self, name: str, raw: dict[str, Any]) -> Any:
        meta = next((a for a in self.actions() if a["name"] == name), None)
        if meta is None:
            raise KeyError(f"{self.id}: unknown action '{name}'")
        kwargs = {p.name: p.coerce(raw.get(p.name)) for p in meta["params"]}
        with self.lock:
            return getattr(self, name)(**kwargs)

    def safe_status(self) -> dict[str, Any]:
        # Status polls must not queue up behind a long action (e.g. a 60 s capture).
        if not self.lock.acquire(timeout=0.2):
            return {"connected": True, "state": "busy", "fields": {}, "busy": True}
        try:
            return self.status()
        except Exception as exc:
            return {"connected": False, "state": "error", "error": f"{type(exc).__name__}: {exc}", "fields": {}}
        finally:
            self.lock.release()
