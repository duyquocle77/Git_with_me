"""Giải mã APDU (ISO 7816-4) và BER-TLV - hữu ích khi debug trao đổi NFC/SE của digital key."""
from toolkit import tool, Param


def parse_tlv(data: bytes, depth=0, out=None):
    out = [] if out is None else out
    i = 0
    while i < len(data):
        start = i
        tag = data[i]; i += 1
        if tag & 0x1F == 0x1F:  # tag nhiều byte
            while i < len(data) and data[i] & 0x80:
                tag = (tag << 8) | data[i]; i += 1
            tag = (tag << 8) | data[i]; i += 1
        if i >= len(data):
            raise ValueError(f"TLV cụt tại offset {start}")
        length = data[i]; i += 1
        if length & 0x80:
            n = length & 0x7F
            length = int.from_bytes(data[i:i + n], "big"); i += n
        value = data[i:i + length]
        if len(value) != length:
            raise ValueError(f"tag {tag:X}: cần {length} byte, chỉ còn {len(value)}")
        i += length
        constructed = bool((tag >> (8 * ((tag.bit_length() - 1) // 8))) & 0x20)
        out.append({"depth": depth, "tag": f"{tag:X}", "len": length,
                    "value": "" if constructed else value.hex().upper()})
        if constructed:
            parse_tlv(value, depth + 1, out)
    return out


def parse_command(b: bytes):
    if len(b) < 4:
        raise ValueError("APDU command cần tối thiểu 4 byte (CLA INS P1 P2)")
    cla, ins, p1, p2 = b[:4]
    rest = b[4:]
    cmd = {"CLA": f"{cla:02X}", "INS": f"{ins:02X}", "P1": f"{p1:02X}", "P2": f"{p2:02X}"}
    data = b""
    if len(rest) == 1:
        cmd["Le"] = rest[0]
    elif len(rest) > 1:
        lc = rest[0]
        data = rest[1:1 + lc]
        cmd["Lc"] = lc
        cmd["data"] = data.hex().upper()
        if len(rest) == 2 + lc:
            cmd["Le"] = rest[-1]
    return cmd, data


@tool(
    name="apdu_parser",
    title="APDU / TLV parser",
    description="Dán hex của APDU command hoặc response, tool tách header và giải mã BER-TLV.",
    category="NFC / SE",
    params=[
        Param("hex", "string", label="Hex", required=True,
              default="00A4040008A000000809434343"),
        Param("kind", "choice", label="Loại", default="command", options=["command", "response", "tlv"]),
    ],
)
def run(ctx, hex, kind):
    b = bytes.fromhex(hex.replace(" ", "").replace(":", ""))
    result = {"bytes": len(b)}
    data = b
    if kind == "command":
        result["header"], data = parse_command(b)
        ctx.log(f"Header: {result['header']}")
        if result["header"]["INS"] == "A4" and result["header"]["P1"] == "04":
            result["aid"] = data.hex().upper()  # SELECT by AID: data là AID thô, không phải TLV
            ctx.log(f"SELECT AID = {result['aid']}")
            data = b""
    elif kind == "response":
        if len(b) < 2:
            raise ValueError("response cần tối thiểu SW1 SW2")
        data, sw = b[:-2], b[-2:]
        result["SW"] = sw.hex().upper()
        ctx.log(f"SW = {result['SW']}" + ("  (OK)" if sw == b"\x90\x00" else ""), "info" if sw == b"\x90\x00" else "warn")
    if data:
        try:
            result["tlv"] = parse_tlv(data)
            for t in result["tlv"]:
                ctx.log("  " * t["depth"] + f"{t['tag']} [{t['len']}] {t['value']}")
        except Exception as exc:
            ctx.log(f"Không parse được TLV: {exc}", "warn")
    return result
