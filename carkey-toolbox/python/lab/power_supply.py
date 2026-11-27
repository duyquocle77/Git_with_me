"""Programmable bench power supply over SCPI.

Transports (config "transport"):
    mock    in-memory simulation, no hardware needed
    tcp     raw SCPI socket, e.g. {"host": "192.168.1.50", "port": 5025}
    serial  pyserial, e.g. {"port": "COM5", "baudrate": 9600}
    visa    pyvisa resource, e.g. {"resource": "USB0::0x1AB1::0x0E11::DP8XXXX::INSTR"}

SCPI dialects differ between vendors, so every command is a template that can be
overridden in lab_config.json under "commands". The defaults below follow plain SCPI
(Keysight E36xx / R&S HMC style). {ch}, {v}, {i} are substituted.
"""

from __future__ import annotations

import socket
import time
from typing import Any

from toolkit import Param
from .base import Device, action, register

DEFAULT_COMMANDS = {
    "idn": "*IDN?",
    "select": "INST:NSEL {ch}",
    "output_on": "OUTP ON",
    "output_off": "OUTP OFF",
    "output_query": "OUTP?",
    "set_voltage": "VOLT {v}",
    "set_current": "CURR {i}",
    "meas_voltage": "MEAS:VOLT?",
    "meas_current": "MEAS:CURR?",
}

# Known dialects; pick with "preset" in the config.
PRESETS: dict[str, dict[str, str]] = {
    "scpi": {},
    "rigol_dp800": {
        "select": "", "output_on": ":OUTP CH{ch},ON", "output_off": ":OUTP CH{ch},OFF",
        "output_query": ":OUTP? CH{ch}", "set_voltage": ":SOUR{ch}:VOLT {v}", "set_current": ":SOUR{ch}:CURR {i}",
        "meas_voltage": ":MEAS:VOLT? CH{ch}", "meas_current": ":MEAS:CURR? CH{ch}",
    },
    "keysight_e36300": {
        "select": "", "output_on": "OUTP ON,(@{ch})", "output_off": "OUTP OFF,(@{ch})",
        "output_query": "OUTP? (@{ch})", "set_voltage": "VOLT {v},(@{ch})", "set_current": "CURR {i},(@{ch})",
        "meas_voltage": "MEAS:VOLT? (@{ch})", "meas_current": "MEAS:CURR? (@{ch})",
    },
}


class _Mock:
    def __init__(self) -> None:
        self.out = False
        self.v = 12.0
        self.i = 1.0

    def write(self, cmd: str) -> None:
        c = cmd.upper()
        if "OUTP" in c and "ON" in c.split("OUTP", 1)[1]:
            self.out = True
        elif "OUTP" in c and "OFF" in c:
            self.out = False
        elif c.startswith(("VOLT", ":SOUR")) and "VOLT" in c:
            self.v = float(cmd.split()[-1].split(",")[0])
        elif "CURR " in c:
            self.i = float(cmd.split()[-1].split(",")[0])

    def query(self, cmd: str) -> str:
        c = cmd.upper()
        if c.startswith("*IDN"):
            return "MOCK,PSU-1000,0,1.0"
        if "OUTP?" in c:
            return "1" if self.out else "0"
        if "MEAS:VOLT" in c:
            return f"{self.v if self.out else 0.0:.3f}"
        if "MEAS:CURR" in c:
            return f"{0.085 if self.out else 0.0:.4f}"  # ~85 mA, e.g. an idle ECU
        return ""

    def close(self) -> None:
        pass


class _Tcp:
    def __init__(self, host: str, port: int, timeout: float) -> None:
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        self.buf = b""

    def write(self, cmd: str) -> None:
        self.sock.sendall((cmd + "\n").encode())

    def query(self, cmd: str) -> str:
        self.write(cmd)
        while b"\n" not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("instrument closed the connection")
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        return line.decode(errors="replace").strip()

    def close(self) -> None:
        self.sock.close()


class _Serial:
    def __init__(self, port: str, baudrate: int, timeout: float) -> None:
        import serial  # pip install pyserial
        self.ser = serial.Serial(port, baudrate=baudrate, timeout=timeout)

    def write(self, cmd: str) -> None:
        self.ser.write((cmd + "\n").encode())

    def query(self, cmd: str) -> str:
        self.write(cmd)
        return self.ser.readline().decode(errors="replace").strip()

    def close(self) -> None:
        self.ser.close()


class _Visa:
    def __init__(self, resource: str, timeout: float) -> None:
        import pyvisa  # pip install pyvisa pyvisa-py
        self.inst = pyvisa.ResourceManager().open_resource(resource)
        self.inst.timeout = int(timeout * 1000)

    def write(self, cmd: str) -> None:
        self.inst.write(cmd)

    def query(self, cmd: str) -> str:
        return self.inst.query(cmd).strip()

    def close(self) -> None:
        self.inst.close()


@register
class PowerSupply(Device):
    driver = "power_supply"
    title = "Power supply"

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.cmds = {**DEFAULT_COMMANDS, **PRESETS.get(self.cfg("preset", "scpi"), {}), **self.cfg("commands", {})}
        self.channel = int(self.cfg("channel", 1))
        self.io: Any = None
        self.idn = ""

    # ---- transport ----
    def _open(self) -> Any:
        if self.io is None:
            t = self.cfg("transport", "mock")
            timeout = float(self.cfg("timeout_s", 2.0))
            if t == "mock":
                self.io = _Mock()
            elif t == "tcp":
                self.io = _Tcp(self.cfg("host"), int(self.cfg("port", 5025)), timeout)
            elif t == "serial":
                self.io = _Serial(self.cfg("port"), int(self.cfg("baudrate", 9600)), timeout)
            elif t == "visa":
                self.io = _Visa(self.cfg("resource"), timeout)
            else:
                raise ValueError(f"unknown transport '{t}'")
            self.idn = self.io.query(self.cmds["idn"])
            self.log(f"Connected: {self.idn}")
        return self.io

    def _cmd(self, key: str, **kw: Any) -> str:
        return self.cmds[key].format(ch=self.channel, **kw)

    def _write(self, key: str, **kw: Any) -> None:
        io = self._open()
        if self.cmds.get("select"):
            io.write(self._cmd("select"))
        io.write(self._cmd(key, **kw))

    def _query(self, key: str) -> str:
        io = self._open()
        if self.cmds.get("select"):
            io.write(self._cmd("select"))
        return io.query(self._cmd(key))

    def _drop(self) -> None:
        if self.io is not None:
            try:
                self.io.close()
            finally:
                self.io = None

    # ---- status ----
    def status(self) -> dict[str, Any]:
        try:
            on = self._query("output_query").strip().upper() in {"1", "ON"}
            v = float(self._query("meas_voltage"))
            i = float(self._query("meas_current"))
        except Exception:
            self._drop()  # reconnect on next poll
            raise
        return {"connected": True, "state": "ON" if on else "OFF", "on": on,
                "fields": {"Instrument": self.idn, "Channel": self.channel,
                           "Voltage": f"{v:.3f} V", "Current": f"{i * 1000:.1f} mA"}}

    # ---- actions ----
    @action(label="Power ON", group="Output", style="success")
    def power_on(self) -> str:
        self._write("output_on")
        return f"CH{self.channel} output ON"

    @action(label="Power OFF", group="Output", style="danger")
    def power_off(self) -> str:
        self._write("output_off")
        return f"CH{self.channel} output OFF"

    @action(label="Power cycle", group="Output",
            params=[Param("off_s", "float", label="Off time (s)", default=2.0)])
    def power_cycle(self, off_s: float) -> str:
        self._write("output_off")
        self.log(f"Output OFF, waiting {off_s} s")
        time.sleep(off_s)
        self._write("output_on")
        return f"CH{self.channel} power cycled ({off_s} s off)"

    @action(label="Apply", group="Setpoint",
            params=[Param("voltage", "float", label="Voltage (V)", default=12.0),
                    Param("current", "float", label="Current limit (A)", default=1.0)])
    def set_output(self, voltage: float, current: float) -> str:
        self._write("set_voltage", v=voltage)
        self._write("set_current", i=current)
        return f"Setpoint {voltage} V / {current} A"

    @action(label="Reconnect", group="Connection")
    def reconnect(self) -> str:
        self._drop()
        self._open()
        return self.idn

    def close(self) -> None:
        self._drop()
