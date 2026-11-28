"""CRC calculator: presets common in automotive/embedded work (incl. AUTOSAR), fully custom
parameters, and input from hex / text / binary file / Intel HEX / Motorola S-record with an
address range and gap fill byte.

Presets follow the Rocksoft model (width, poly, init, refin, refout, xorout) and are verified at
load time against their catalogue check value (CRC of the ASCII string "123456789").
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from toolkit import tool, Param


@dataclass(frozen=True)
class CrcSpec:
    name: str
    width: int
    poly: int
    init: int
    refin: bool
    refout: bool
    xorout: int
    check: int | None = None  # CRC("123456789")
    note: str = ""


PRESETS = [
    CrcSpec("CRC-8/SAE-J1850", 8, 0x1D, 0xFF, False, False, 0xFF, 0x4B, "AUTOSAR Crc_CalculateCRC8"),
    CrcSpec("CRC-8/AUTOSAR (8H2F)", 8, 0x2F, 0xFF, False, False, 0xFF, 0xDF, "AUTOSAR Crc_CalculateCRC8H2F, E2E P2"),
    CrcSpec("CRC-8/SMBUS", 8, 0x07, 0x00, False, False, 0x00, 0xF4),
    CrcSpec("CRC-16/CCITT-FALSE", 16, 0x1021, 0xFFFF, False, False, 0x0000, 0x29B1, "AUTOSAR Crc_CalculateCRC16, aka IBM-3740"),
    CrcSpec("CRC-16/ARC", 16, 0x8005, 0x0000, True, True, 0x0000, 0xBB3D, "aka CRC-16/IBM, LHA"),
    CrcSpec("CRC-16/KERMIT", 16, 0x1021, 0x0000, True, True, 0x0000, 0x2189, "aka CCITT-TRUE"),
    CrcSpec("CRC-16/XMODEM", 16, 0x1021, 0x0000, False, False, 0x0000, 0x31C3),
    CrcSpec("CRC-16/MODBUS", 16, 0x8005, 0xFFFF, True, True, 0x0000, 0x4B37),
    CrcSpec("CRC-16/X-25", 16, 0x1021, 0xFFFF, True, True, 0xFFFF, 0x906E),
    CrcSpec("CRC-32/ISO-HDLC", 32, 0x04C11DB7, 0xFFFFFFFF, True, True, 0xFFFFFFFF, 0xCBF43926, "Ethernet, zlib, AUTOSAR Crc_CalculateCRC32"),
    CrcSpec("CRC-32/AUTOSAR (P4)", 32, 0xF4ACFB13, 0xFFFFFFFF, True, True, 0xFFFFFFFF, 0x1697D06A, "AUTOSAR Crc_CalculateCRC32P4, E2E P4"),
    CrcSpec("CRC-32/ISCSI (C)", 32, 0x1EDC6F41, 0xFFFFFFFF, True, True, 0xFFFFFFFF, 0xE3069283, "Castagnoli"),
    CrcSpec("CRC-32/MPEG-2", 32, 0x04C11DB7, 0xFFFFFFFF, False, False, 0x00000000, 0x0376E6E7, "STM32 CRC unit default"),
    CrcSpec("CRC-32/BZIP2", 32, 0x04C11DB7, 0xFFFFFFFF, False, False, 0xFFFFFFFF, 0xFC891918),
    CrcSpec("CRC-64/ECMA-182", 64, 0x42F0E1EBA9EA3693, 0x0, False, False, 0x0, 0x6C40DF5F0B497347),
    CrcSpec("CRC-64/XZ", 64, 0x42F0E1EBA9EA3693, 0xFFFFFFFFFFFFFFFF, True, True, 0xFFFFFFFFFFFFFFFF, 0x995DC9BBDF1939FA, "AUTOSAR Crc_CalculateCRC64"),
]
BY_NAME = {p.name: p for p in PRESETS}


# ---------------------------------------------------------------------------
# Engine (table driven, works for any width 8..64)
# ---------------------------------------------------------------------------
def _reflect(v: int, bits: int) -> int:
    r = 0
    for _ in range(bits):
        r = (r << 1) | (v & 1)
        v >>= 1
    return r


@lru_cache(maxsize=64)
def _table(width: int, poly: int, refin: bool) -> tuple[int, ...]:
    mask = (1 << width) - 1
    table = []
    if refin:
        rpoly = _reflect(poly, width)
        for i in range(256):
            c = i
            for _ in range(8):
                c = (c >> 1) ^ rpoly if c & 1 else c >> 1
            table.append(c & mask)
    else:
        top = 1 << (width - 1)
        for i in range(256):
            c = i << (width - 8)
            for _ in range(8):
                c = ((c << 1) ^ poly) if c & top else (c << 1)
            table.append(c & mask)
    return tuple(table)


def crc(data: bytes, spec: CrcSpec) -> int:
    if not 8 <= spec.width <= 64:
        raise ValueError("width must be between 8 and 64")
    w, mask = spec.width, (1 << spec.width) - 1
    t = _table(w, spec.poly, spec.refin)
    if spec.refin:
        reg = _reflect(spec.init, w)
        for b in data:
            reg = (reg >> 8) ^ t[(reg ^ b) & 0xFF]
        out = reg if spec.refout else _reflect(reg, w)
    else:
        reg, shift = spec.init, w - 8
        for b in data:
            reg = ((reg << 8) & mask) ^ t[((reg >> shift) ^ b) & 0xFF]
        out = _reflect(reg, w) if spec.refout else reg
    return (out ^ spec.xorout) & mask


def _self_test() -> None:
    for p in PRESETS:
        got = crc(b"123456789", p)
        if p.check is not None and got != p.check:
            raise AssertionError(f"{p.name}: check 0x{got:X} != 0x{p.check:X}")


_self_test()


# ---------------------------------------------------------------------------
# Input parsing
# ---------------------------------------------------------------------------
def parse_int(s: str, what: str) -> int | None:
    s = (s or "").strip().replace("_", "")
    if not s:
        return None
    try:
        return int(s, 0)
    except ValueError:
        raise ValueError(f"{what}: '{s}' is not a number (use 0x prefix for hex, e.g. 0x8000)") from None


def parse_hex_text(s: str) -> bytes:
    cleaned = s.replace("0x", "").replace("0X", "")
    for ch in " \t\r\n,:;-":
        cleaned = cleaned.replace(ch, "")
    if len(cleaned) % 2:
        raise ValueError("hex input has an odd number of digits")
    return bytes.fromhex(cleaned)


def load_intel_hex(path: str) -> dict[int, int]:
    mem: dict[int, int] = {}
    base = 0
    with open(path, encoding="ascii", errors="replace") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            if not line.startswith(":"):
                raise ValueError(f"line {n}: not an Intel HEX record")
            rec = bytes.fromhex(line[1:])
            if sum(rec) & 0xFF:
                raise ValueError(f"line {n}: checksum error")
            count, addr, rtype, payload = rec[0], (rec[1] << 8) | rec[2], rec[3], rec[4:4 + rec[0]]
            if len(payload) != count:
                raise ValueError(f"line {n}: truncated record")
            if rtype == 0x00:
                for i, b in enumerate(payload):
                    mem[base + addr + i] = b
            elif rtype == 0x01:
                break
            elif rtype == 0x02:
                base = int.from_bytes(payload, "big") << 4
            elif rtype == 0x04:
                base = int.from_bytes(payload, "big") << 16
    return mem


def load_srec(path: str) -> dict[int, int]:
    mem: dict[int, int] = {}
    alen = {"1": 2, "2": 3, "3": 4}
    with open(path, encoding="ascii", errors="replace") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            if not line.startswith("S"):
                raise ValueError(f"line {n}: not an S-record")
            rec = bytes.fromhex(line[2:])
            if (sum(rec) & 0xFF) != 0xFF:
                raise ValueError(f"line {n}: checksum error")
            if line[1] in alen:
                a = alen[line[1]]
                addr = int.from_bytes(rec[1:1 + a], "big")
                for i, b in enumerate(rec[1 + a:-1]):
                    mem[addr + i] = b
    return mem


def mem_to_bytes(mem: dict[int, int], start: int | None, length: int | None, fill: int) -> tuple[bytes, int, int, int]:
    if not mem:
        raise ValueError("file contains no data records")
    lo = min(mem) if start is None else start
    hi = (max(mem) + 1) if length is None else lo + length
    if hi <= lo:
        raise ValueError("empty address range")
    if hi - lo > 64 * 1024 * 1024:
        raise ValueError("range larger than 64 MiB - check start/length")
    buf = bytearray([fill]) * (hi - lo)
    filled = hi - lo
    for a, b in mem.items():
        if lo <= a < hi:
            buf[a - lo] = b
            filled -= 1
    return bytes(buf), lo, hi, filled


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------
ALL = "ALL (compare presets)"
CUSTOM = "Custom"


def _fmt(v: int, width: int) -> str:
    return f"0x{v:0{(width + 3) // 4}X}"


@tool(
    name="crc_calc",
    title="CRC calculator",
    description="CRC-8/16/32/64 with AUTOSAR & common presets or custom parameters. Input: hex, text, "
                "binary file, Intel HEX or S-record (address range + gap fill).",
    category="Utilities",
    params=[
        Param("algorithm", "choice", label="Algorithm", default="CRC-32/ISO-HDLC",
              options=[p.name for p in PRESETS] + [ALL, CUSTOM]),
        Param("source", "choice", label="Input type", default="hex", options=["hex", "text", "file"]),
        Param("data", "string", label="Data (hex or text)", default="31 32 33 34 35 36 37 38 39",
              help="Hex: spaces, commas, colons and 0x are ignored", show_if={"source": ["hex", "text"]}),
        Param("file", "file", label="File (.bin / .hex / .s19 …)", default="", show_if={"source": ["file"]}),
        Param("start", "string", label="Start address / offset", default="", help="Empty = first byte, e.g. 0x00008000"),
        Param("length", "string", label="Length", default="", help="Empty = to end, e.g. 0x7FFC"),
        Param("fill", "string", label="Gap fill byte (HEX/S-rec)", default="0xFF", show_if={"source": ["file"]}),
        Param("width", "int", label="Custom: width (bits)", default=32, show_if={"algorithm": ["Custom"]}),
        Param("poly", "string", label="Custom: poly", default="0x04C11DB7", show_if={"algorithm": ["Custom"]}),
        Param("init", "string", label="Custom: init", default="0xFFFFFFFF", show_if={"algorithm": ["Custom"]}),
        Param("refin", "bool", label="Custom: refin", default=True, show_if={"algorithm": ["Custom"]}),
        Param("refout", "bool", label="Custom: refout", default=True, show_if={"algorithm": ["Custom"]}),
        Param("xorout", "string", label="Custom: xorout", default="0xFFFFFFFF", show_if={"algorithm": ["Custom"]}),
    ],
)
def run(ctx, algorithm, source, data, file, start, length, fill, width, poly, init, refin, refout, xorout):
    # ---- gather input bytes ----
    start_i, length_i = parse_int(start, "start"), parse_int(length, "length")
    info: dict = {"source": source}
    if source == "hex":
        buf = parse_hex_text(data)
    elif source == "text":
        buf = data.encode("utf-8")
    else:
        if not file:
            raise ValueError("choose a file")
        ext = os.path.splitext(file)[1].lower()
        fill_b = parse_int(fill, "fill") or 0
        if ext in {".hex", ".ihex", ".ihx"}:
            mem, fmt = load_intel_hex(file), "Intel HEX"
        elif ext in {".s19", ".s28", ".s37", ".srec", ".mot", ".s"}:
            mem, fmt = load_srec(file), "S-record"
        else:
            mem, fmt = None, "binary"
        if mem is not None:
            buf, lo, hi, gaps = mem_to_bytes(mem, start_i, length_i, fill_b & 0xFF)
            info.update(format=fmt, range=f"{_fmt(lo, 32)} .. {_fmt(hi - 1, 32)}", gap_bytes_filled=gaps)
            ctx.log(f"{fmt}: {len(mem)} data bytes, range {_fmt(lo, 32)}..{_fmt(hi - 1, 32)}, {gaps} gap byte(s) filled with 0x{fill_b & 0xFF:02X}")
            if gaps:
                ctx.log(f"{gaps} byte(s) not present in the file were filled with 0x{fill_b & 0xFF:02X}", "warn")
        else:
            raw = open(file, "rb").read()
            off = start_i or 0
            end = len(raw) if length_i is None else off + length_i
            if off > len(raw) or end > len(raw):
                raise ValueError(f"range 0x{off:X}+{end - off} exceeds file size {len(raw)}")
            buf = raw[off:end]
            info.update(format=fmt, range=f"offset 0x{off:X} .. 0x{end - 1:X}", file_size=len(raw))
            ctx.log(f"binary: {len(raw)} bytes, using offset 0x{off:X}..0x{end - 1:X}")
        info["file"] = file
    info["bytes"] = len(buf)
    if source != "file" and (start_i is not None or length_i is not None):
        off = start_i or 0
        buf = buf[off: None if length_i is None else off + length_i]
        info["bytes"] = len(buf)
    preview = buf[:16].hex(" ").upper() + (" …" if len(buf) > 16 else "")
    ctx.log(f"{len(buf)} byte(s): {preview}")
    ctx.progress(0.3)

    # ---- compute ----
    if algorithm == ALL:
        rows = []
        for p in PRESETS:
            ctx.check_cancel()
            v = crc(buf, p)
            rows.append({"algorithm": p.name, "crc": _fmt(v, p.width), "note": p.note})
            ctx.log(f"{p.name:<24} {_fmt(v, p.width)}")
        return {**info, "results": rows}

    if algorithm == CUSTOM:
        spec = CrcSpec("Custom", int(width), parse_int(poly, "poly") or 0, parse_int(init, "init") or 0,
                       bool(refin), bool(refout), parse_int(xorout, "xorout") or 0)
        if spec.poly == 0:
            raise ValueError("poly must not be 0")
        check = crc(b"123456789", spec)
        ctx.log(f"Custom spec check value (\"123456789\") = {_fmt(check, spec.width)}", "sys")
    else:
        spec = BY_NAME[algorithm]

    v = crc(buf, spec)
    nbytes = (spec.width + 7) // 8
    ctx.log(f"{spec.name} = {_fmt(v, spec.width)}", "warn")  # highlighted
    ctx.progress(1.0)
    return {
        **info,
        "algorithm": spec.name,
        "crc": _fmt(v, spec.width),
        "crc_bytes_big_endian": v.to_bytes(nbytes, "big").hex(" ").upper(),
        "crc_bytes_little_endian": v.to_bytes(nbytes, "little").hex(" ").upper(),
        "params": {"width": spec.width, "poly": _fmt(spec.poly, spec.width), "init": _fmt(spec.init, spec.width),
                   "refin": spec.refin, "refout": spec.refout, "xorout": _fmt(spec.xorout, spec.width)},
        "note": spec.note,
    }
