"""Saleae Logic 2 via the official automation API.

Requirements on the lab PC:
  * Logic 2 running with the automation server enabled
    (Preferences > Automation > Enable, or start it with `Logic --automation`)
  * pip install logic2-automation

Config: {"port": 10430, "device_id": "" (empty = first device), "channels": [0,1,2,3],
         "sample_rate": 10000000, "output_dir": "~/CarKeyToolbox/saleae"}
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

from toolkit import Param
from .base import Device, action, register


def _parse_channels(text: str) -> list[int]:
    out: list[int] = []
    for part in str(text).replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-")
            out += list(range(int(a), int(b) + 1))
        else:
            out.append(int(part))
    return out


@register
class SaleaeLogic(Device):
    driver = "saleae_logic"
    title = "Saleae Logic 2"

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.manager: Any = None
        self.capture: Any = None
        self.capture_started = 0.0
        self.last_file = ""
        self.capture_channels: list[int] = []

    def _automation(self) -> Any:
        try:
            from saleae import automation
        except ImportError as exc:
            raise RuntimeError("Python package missing: pip install logic2-automation") from exc
        return automation

    def _mgr(self) -> Any:
        if self.manager is None:
            automation = self._automation()
            self.manager = automation.Manager.connect(port=int(self.cfg("port", 10430)), connect_timeout_seconds=5.0)
            self.log("Connected to Logic 2 automation server")
        return self.manager

    def _out_dir(self) -> Path:
        d = Path(self.cfg("output_dir", "~/CarKeyToolbox/saleae")).expanduser()
        d.mkdir(parents=True, exist_ok=True)
        return d

    def status(self) -> dict[str, Any]:
        if self.manager is None:
            return {"connected": False, "state": "not connected",
                    "fields": {"Hint": "Start Logic 2 with automation enabled, then press Connect"}}
        devices = self.manager.get_devices()
        fields = {"Devices": ", ".join(f"{d.device_type.name} ({d.device_id})" for d in devices) or "none"}
        if self.capture is not None:
            fields["Capturing for"] = f"{time.time() - self.capture_started:.0f} s"
        if self.last_file:
            fields["Last file"] = self.last_file
        return {"connected": True, "state": "capturing" if self.capture else "idle", "fields": fields}

    @action(label="Connect", group="Connection", style="primary")
    def connect(self) -> str:
        self.disconnect()
        self._mgr()
        return "connected"

    @action(label="Disconnect", group="Connection")
    def disconnect(self) -> str:
        if self.capture is not None:
            try:
                self.capture.close()
            finally:
                self.capture = None
        if self.manager is not None:
            try:
                self.manager.close()
            finally:
                self.manager = None
        return "disconnected"

    def _start(self, channels: str, sample_rate: int, mode: Any) -> Any:
        automation = self._automation()
        self.capture_channels = _parse_channels(channels)
        dev_cfg = automation.LogicDeviceConfiguration(
            enabled_digital_channels=self.capture_channels,
            digital_sample_rate=int(sample_rate),
        )
        return self._mgr().start_capture(
            device_id=self.cfg("device_id") or None,
            device_configuration=dev_cfg,
            capture_configuration=automation.CaptureConfiguration(capture_mode=mode),
        )

    _ch = Param("channels", "string", label="Digital channels", default="0-3", help="e.g. 0-3 or 0,2,5")
    _sr = Param("sample_rate", "int", label="Sample rate (S/s)", default=10_000_000)

    @action(label="Start capture", group="Capture", style="success", params=[_ch, _sr])
    def start_capture(self, channels: str, sample_rate: int) -> str:
        if self.capture is not None:
            raise RuntimeError("a capture is already running")
        self.capture = self._start(channels, sample_rate, self._automation().ManualCaptureMode())
        self.capture_started = time.time()
        return f"capturing channels {channels} @ {sample_rate} S/s"

    @action(label="Stop & save", group="Capture", style="danger",
            params=[Param("name", "string", label="File name", default="", help="empty = timestamp"),
                    Param("export_csv", "bool", label="Also export CSV", default=False)])
    def stop_capture(self, name: str, export_csv: bool) -> dict[str, Any]:
        if self.capture is None:
            raise RuntimeError("no capture running")
        cap, self.capture = self.capture, None
        try:
            cap.stop()
            cap.wait()
            return self._save(cap, name, export_csv)
        finally:
            cap.close()

    @action(label="Timed capture", group="Capture",
            params=[_ch, _sr, Param("seconds", "float", label="Duration (s)", default=5.0),
                    Param("name", "string", label="File name", default=""),
                    Param("export_csv", "bool", label="Also export CSV", default=False)])
    def timed_capture(self, channels: str, sample_rate: int, seconds: float, name: str, export_csv: bool) -> dict:
        if self.capture is not None:
            raise RuntimeError("a capture is already running")
        cap = self._start(channels, sample_rate, self._automation().TimedCaptureMode(duration_seconds=seconds))
        self.log(f"Capturing {seconds} s ...")
        try:
            cap.wait()
            return self._save(cap, name, export_csv)
        finally:
            cap.close()

    def _save(self, cap: Any, name: str, export_csv: bool) -> dict[str, Any]:
        stem = name or time.strftime("capture_%Y%m%d_%H%M%S")
        path = self._out_dir() / f"{stem}.sal"
        cap.save_capture(filepath=str(path))
        self.last_file = str(path)
        result: dict[str, Any] = {"file": str(path)}
        if export_csv:
            csv_dir = self._out_dir() / stem
            csv_dir.mkdir(exist_ok=True)
            cap.export_raw_data_csv(directory=str(csv_dir), digital_channels=self.capture_channels)
            result["csv_dir"] = str(csv_dir)
        self.log(f"Saved {path}")
        return result

    def close(self) -> None:
        self.disconnect()
