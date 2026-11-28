"""Small SDK for declaring Car Key Toolbox tools.

Each file in python/tools/ defines one or more functions decorated with @tool.
The function receives `ctx` (RunContext) first; the remaining arguments come from the UI form.

    from toolkit import tool, Param

    @tool(
        name="hello",
        title="Hello",
        description="Minimal example",
        params=[Param("who", "string", default="world")],
    )
    def hello(ctx, who):
        ctx.log(f"Hello {who}")
        return {"greeting": f"Hello {who}"}

`print()` inside a tool is also shown as a log line in the UI,
so existing scripts can be wrapped with few changes.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field, asdict
from typing import Any, Callable

PARAM_TYPES = {"string", "int", "float", "bool", "choice", "file"}


@dataclass
class Param:
    name: str
    type: str = "string"
    label: str = ""
    default: Any = None
    help: str = ""
    options: list[str] = field(default_factory=list)  # for type="choice"
    required: bool = False
    # Show this field only when other fields have one of the given values,
    # e.g. {"source": ["file"]}. Purely a UI hint; hidden values are still sent.
    show_if: dict[str, list] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.type not in PARAM_TYPES:
            raise ValueError(f"param {self.name}: type '{self.type}' is invalid, must be one of {sorted(PARAM_TYPES)}")
        if not self.label:
            self.label = self.name

    def coerce(self, value: Any) -> Any:
        if value is None or value == "":
            if self.required:
                raise ValueError(f"missing required parameter '{self.name}'")
            return self.default
        if self.type == "int":
            return int(value)
        if self.type == "float":
            return float(value)
        if self.type == "bool":
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "on"}
            return bool(value)
        if self.type == "choice" and self.options and value not in self.options:
            raise ValueError(f"'{value}' is not one of {self.options}")
        return str(value) if self.type in {"string", "file"} else value


@dataclass
class ToolSpec:
    name: str
    title: str
    description: str
    category: str
    params: list[Param]
    func: Callable[..., Any]
    source: str = ""

    def info(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "category": self.category,
            "source": self.source,
            "params": [asdict(p) for p in self.params],
        }


class Cancelled(Exception):
    """Raised (e.g. by ctx.check_cancel()) when the user presses Stop."""


class RunContext:
    """Passed to each tool to report logs / progress and check for cancellation."""

    def __init__(self, run_id: str, emit: Callable[[str, dict], None]):
        self.run_id = run_id
        self._emit = emit
        self._cancel = threading.Event()

    # --- for use inside tools ---
    def log(self, message: str, level: str = "info") -> None:
        self._emit("run.log", {"run_id": self.run_id, "level": level, "message": str(message)})

    def progress(self, value: float, message: str = "") -> None:
        """value in the range 0..1"""
        self._emit("run.progress", {"run_id": self.run_id, "value": max(0.0, min(1.0, float(value))), "message": message})

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def check_cancel(self) -> None:
        if self._cancel.is_set():
            raise Cancelled()

    def sleep(self, seconds: float) -> None:
        """Sleep, but return immediately (raising Cancelled) when cancelled."""
        if self._cancel.wait(seconds):
            raise Cancelled()

    # --- internal ---
    def request_cancel(self) -> None:
        self._cancel.set()


_REGISTRY: dict[str, ToolSpec] = {}
_current_source = ""


def tool(*, name: str, title: str = "", description: str = "", category: str = "General",
         params: list[Param] | None = None) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def deco(func: Callable[..., Any]) -> Callable[..., Any]:
        _REGISTRY[name] = ToolSpec(
            name=name,
            title=title or name,
            description=description or (func.__doc__ or "").strip(),
            category=category,
            params=list(params or []),
            func=func,
            source=_current_source,
        )
        return func

    return deco


def _registry() -> dict[str, ToolSpec]:
    return _REGISTRY
