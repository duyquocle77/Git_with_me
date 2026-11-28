"""Android phone (Pixel) control over USB with adb.

Config: {"adb": "adb" (or full path to platform-tools/adb), "serial": "" (empty = only device),
         "app_package": "com.google.android.apps.walletnfcrel", "output_dir": "~/CarKeyToolbox/android"}
"""

from __future__ import annotations

import re
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

from toolkit import Param
from .base import Device, action, register

KEY_WAKEUP, KEY_SLEEP, KEY_MENU = 224, 223, 82


@register
class AndroidAdb(Device):
    driver = "android_adb"
    title = "Android phone (ADB)"

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.serial = self.cfg("serial", "")
        self.logcat: subprocess.Popen | None = None
        self.logcat_file = ""
        self._info: dict[str, Any] = {}
        self._info_at = 0.0

    # ---- adb helpers ----
    def _adb(self, *args: str, timeout: float = 15, check: bool = True, binary: bool = False) -> Any:
        cmd = [self.cfg("adb", "adb")]
        if self.serial:
            cmd += ["-s", self.serial]
        cmd += list(args)
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout, text=not binary)
        except FileNotFoundError as exc:
            raise RuntimeError("adb not found: install Android platform-tools or set \"adb\" in lab_config.json") from exc
        if check and proc.returncode != 0:
            err = proc.stderr if not binary else proc.stderr.decode(errors="replace")
            raise RuntimeError(f"adb {' '.join(args)} failed: {err.strip()}")
        return proc.stdout if binary else proc.stdout.strip()

    def _shell(self, command: str, timeout: float = 15) -> str:
        return self._adb("shell", command, timeout=timeout)

    def _out_dir(self) -> Path:
        d = Path(self.cfg("output_dir", "~/CarKeyToolbox/android")).expanduser()
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _list_devices(self) -> list[tuple[str, str]]:
        try:
            out = subprocess.run([self.cfg("adb", "adb"), "devices"], capture_output=True, text=True, timeout=5).stdout
        except FileNotFoundError as exc:
            raise RuntimeError("adb not found: install Android platform-tools or set \"adb\" in lab_config.json") from exc
        return [tuple(l.split("\t")[:2]) for l in out.splitlines()[1:] if "\t" in l]  # type: ignore[misc]

    def _refresh_info(self) -> None:
        props = {"Model": "ro.product.model", "Android": "ro.build.version.release", "Build": "ro.build.id"}
        info = {k: self._shell(f"getprop {p}", timeout=5) for k, p in props.items()}
        m = re.search(r"level: (\d+)", self._shell("dumpsys battery", timeout=5))
        info["Battery"] = f"{m.group(1)} %" if m else "?"
        info["Bluetooth"] = "on" if self._shell("settings get global bluetooth_on", timeout=5) == "1" else "off"
        info["NFC"] = "on" if "mState=on" in self._shell("dumpsys nfc | grep mState=", timeout=5) else "off"
        info["Airplane"] = "on" if self._shell("settings get global airplane_mode_on", timeout=5) == "1" else "off"
        wake = re.search(r"mWakefulness=(\w+)", self._shell("dumpsys power | grep mWakefulness=", timeout=5))
        info["Screen"] = wake.group(1) if wake else "?"
        info["BT snoop log"] = self._shell("getprop persist.bluetooth.btsnooplogmode", timeout=5) or "default"
        self._info, self._info_at = info, time.time()

    # ---- status ----
    def status(self) -> dict[str, Any]:
        devices = self._list_devices()
        ready = [s for s, st in devices if st == "device"]
        if self.serial:
            connected = self.serial in ready
        else:
            connected = len(ready) == 1
            if len(ready) > 1:
                return {"connected": False, "state": "several phones",
                        "fields": {"Devices": ", ".join(ready), "Hint": "Use 'Select phone' to pick one"}}
        if not connected:
            self._info = {}
            others = ", ".join(f"{s} ({st})" for s, st in devices) or "none"
            return {"connected": False, "state": "no phone", "fields": {"adb devices": others}}
        if time.time() - self._info_at > 10:
            self._refresh_info()
        fields = {"Serial": self.serial or ready[0], **self._info}
        if self.logcat is not None:
            fields["Logcat"] = f"recording -> {self.logcat_file}"
        return {"connected": True, "state": self._info.get("Model", "connected"), "fields": fields}

    def _changed(self, msg: str) -> str:
        self._info_at = 0  # force a status refresh on the next poll
        return msg

    # ---- connection ----
    @action(label="Select phone", group="Connection",
            params=[Param("serial", "string", label="Serial", default="", help="empty = the only connected phone")])
    def select(self, serial: str) -> str:
        self.serial = serial.strip()
        return self._changed(f"using {self.serial or 'the only connected phone'}")

    @action(label="Reboot", group="Connection", style="danger", confirm="Reboot the phone?")
    def reboot(self) -> str:
        self._adb("reboot")
        return self._changed("rebooting")

    # ---- screen ----
    @action(label="Wake", group="Screen")
    def wake(self) -> str:
        self._shell(f"input keyevent {KEY_WAKEUP}")
        return self._changed("screen on")

    @action(label="Sleep", group="Screen")
    def sleep(self) -> str:
        self._shell(f"input keyevent {KEY_SLEEP}")
        return self._changed("screen off")

    @action(label="Unlock (no PIN)", group="Screen")
    def unlock(self) -> str:
        self._shell(f"input keyevent {KEY_WAKEUP}")
        self._shell(f"input keyevent {KEY_MENU}")
        return self._changed("unlocked (works only without a secure lock screen)")

    @action(label="Screenshot", group="Screen", style="primary")
    def screenshot(self) -> dict[str, str]:
        png = self._adb("exec-out", "screencap", "-p", binary=True, timeout=20)
        path = self._out_dir() / time.strftime("screen_%Y%m%d_%H%M%S.png")
        path.write_bytes(png)
        return {"file": str(path)}

    # ---- radios ----
    @action(label="Bluetooth ON", group="Radios", style="success")
    def bt_on(self) -> str:
        self._shell("cmd bluetooth_manager enable")
        return self._changed("Bluetooth enabled")

    @action(label="Bluetooth OFF", group="Radios")
    def bt_off(self) -> str:
        self._shell("cmd bluetooth_manager disable")
        return self._changed("Bluetooth disabled")

    @action(label="NFC ON", group="Radios", style="success")
    def nfc_on(self) -> str:
        self._shell("svc nfc enable")
        return self._changed("NFC enabled")

    @action(label="NFC OFF", group="Radios")
    def nfc_off(self) -> str:
        self._shell("svc nfc disable")
        return self._changed("NFC disabled")

    @action(label="Airplane ON", group="Radios")
    def airplane_on(self) -> str:
        self._shell("cmd connectivity airplane-mode enable")
        return self._changed("airplane mode on")

    @action(label="Airplane OFF", group="Radios")
    def airplane_off(self) -> str:
        self._shell("cmd connectivity airplane-mode disable")
        return self._changed("airplane mode off")

    # ---- debugging ----
    @action(label="BT snoop log: full", group="Debug",
            help="Takes effect after Bluetooth is toggled off/on")
    def snoop_full(self) -> str:
        self._shell("setprop persist.bluetooth.btsnooplogmode full")
        return self._changed("btsnoop full - toggle Bluetooth to apply")

    @action(label="BT snoop log: off", group="Debug")
    def snoop_off(self) -> str:
        self._shell("setprop persist.bluetooth.btsnooplogmode disabled")
        return self._changed("btsnoop disabled - toggle Bluetooth to apply")

    @action(label="Start logcat", group="Debug", style="success",
            params=[Param("clear", "bool", label="Clear buffer first", default=True),
                    Param("filter", "string", label="Filter spec", default="", help="e.g. BluetoothGatt:V *:S")])
    def logcat_start(self, clear: bool, filter: str) -> str:
        if self.logcat is not None:
            raise RuntimeError("logcat already running")
        if clear:
            self._adb("logcat", "-c")
        path = self._out_dir() / time.strftime("logcat_%Y%m%d_%H%M%S.txt")
        cmd = [self.cfg("adb", "adb")] + (["-s", self.serial] if self.serial else []) + ["logcat", "-v", "threadtime"]
        cmd += shlex.split(filter)
        self._logcat_fh = open(path, "w", encoding="utf-8", errors="replace")
        self.logcat = subprocess.Popen(cmd, stdout=self._logcat_fh, stderr=subprocess.STDOUT)
        self.logcat_file = str(path)
        return f"logcat -> {path}"

    @action(label="Stop logcat", group="Debug", style="danger")
    def logcat_stop(self) -> dict[str, str]:
        if self.logcat is None:
            raise RuntimeError("logcat is not running")
        self.logcat.terminate()
        try:
            self.logcat.wait(timeout=3)
        except subprocess.TimeoutExpired:
            self.logcat.kill()
        self._logcat_fh.close()
        self.logcat = None
        return {"file": self.logcat_file}

    # ---- apps ----
    @action(label="Launch app", group="App",
            params=[Param("package", "string", label="Package", default="")])
    def app_launch(self, package: str) -> str:
        pkg = package or self.cfg("app_package", "")
        if not pkg:
            raise ValueError("no package given")
        self._shell(f"monkey -p {shlex.quote(pkg)} -c android.intent.category.LAUNCHER 1")
        return f"launched {pkg}"

    @action(label="Force stop app", group="App",
            params=[Param("package", "string", label="Package", default="")])
    def app_stop(self, package: str) -> str:
        pkg = package or self.cfg("app_package", "")
        if not pkg:
            raise ValueError("no package given")
        self._shell(f"am force-stop {shlex.quote(pkg)}")
        return f"stopped {pkg}"

    @action(label="Run", group="Shell",
            params=[Param("command", "string", label="adb shell", default="getprop ro.product.model")])
    def shell(self, command: str) -> str:
        return self._shell(command, timeout=60)

    def close(self) -> None:
        if self.logcat is not None:
            self.logcat_stop()
