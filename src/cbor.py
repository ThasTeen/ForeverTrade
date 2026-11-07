"""Pure-Python CBOR (RFC 7049) decoder and Lua string unescape utility.

Used for parsing modern Auctionator price databases (version 7/8+) where realm
price data is serialized into CBOR binary format.
"""

import struct
from typing import Any, Dict, List, Optional, Union


class CBORDecoder:
    """Lightweight, zero-dependency CBOR decoder supporting RFC 7049 standard types."""

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def decode(self) -> Any:
        if self.pos >= len(self.data):
            raise EOFError("Unexpected end of CBOR data")

        b = self.data[self.pos]
        self.pos += 1
        major = b >> 5
        info = b & 0x1F

        if info < 24:
            val = info
        elif info == 24:
            if self.pos >= len(self.data):
                raise EOFError("Truncated 1-byte integer")
            val = self.data[self.pos]
            self.pos += 1
        elif info == 25:
            if self.pos + 2 > len(self.data):
                raise EOFError("Truncated 2-byte integer")
            val = struct.unpack(">H", self.data[self.pos : self.pos + 2])[0]
            self.pos += 2
        elif info == 26:
            if self.pos + 4 > len(self.data):
                raise EOFError("Truncated 4-byte integer")
            val = struct.unpack(">I", self.data[self.pos : self.pos + 4])[0]
            self.pos += 4
        elif info == 27:
            if self.pos + 8 > len(self.data):
                raise EOFError("Truncated 8-byte integer")
            val = struct.unpack(">Q", self.data[self.pos : self.pos + 8])[0]
            self.pos += 8
        elif info == 31:  # Indefinite length
            val = None
        else:
            raise ValueError(f"Unknown CBOR additional info {info}")

        if major == 0:  # Unsigned integer
            return val
        elif major == 1:  # Negative integer
            return -1 - val
        elif major == 2:  # Byte string
            if val is None:
                parts: List[bytes] = []
                while True:
                    if self.pos < len(self.data) and self.data[self.pos] == 0xFF:
                        self.pos += 1
                        break
                    part = self.decode()
                    if isinstance(part, bytes):
                        parts.append(part)
                return b"".join(parts)
            if self.pos + val > len(self.data):
                raise EOFError("Truncated byte string")
            res_bytes = self.data[self.pos : self.pos + val]
            self.pos += val
            return res_bytes
        elif major == 3:  # Text string (UTF-8)
            if val is None:
                str_parts: List[str] = []
                while True:
                    if self.pos < len(self.data) and self.data[self.pos] == 0xFF:
                        self.pos += 1
                        break
                    spart = self.decode()
                    if isinstance(spart, str):
                        str_parts.append(spart)
                return "".join(str_parts)
            if self.pos + val > len(self.data):
                raise EOFError("Truncated text string")
            res_str = self.data[self.pos : self.pos + val].decode("utf-8", errors="replace")
            self.pos += val
            return res_str
        elif major == 4:  # Array
            if val is None:
                arr: List[Any] = []
                while True:
                    if self.pos < len(self.data) and self.data[self.pos] == 0xFF:
                        self.pos += 1
                        break
                    arr.append(self.decode())
                return arr
            return [self.decode() for _ in range(val)]
        elif major == 5:  # Map
            res_dict: Dict[Any, Any] = {}
            if val is None:
                while True:
                    if self.pos < len(self.data) and self.data[self.pos] == 0xFF:
                        self.pos += 1
                        break
                    k = self.decode()
                    v = self.decode()
                    res_dict[k] = v
                return res_dict
            for _ in range(val):
                k = self.decode()
                v = self.decode()
                res_dict[k] = v
            return res_dict
        elif major == 6:  # Semantic tag
            return self.decode()
        elif major == 7:  # Float / simple values
            if info < 24:
                if info == 20:
                    return False
                elif info == 21:
                    return True
                elif info == 22:
                    return None
                elif info == 23:
                    return None  # Undefined
                return info
            elif info == 25:
                # 16-bit half-precision float
                return struct.unpack(">e", self.data[self.pos - 2 : self.pos])[0]
            elif info == 26:
                # 32-bit single-precision float
                return struct.unpack(">f", self.data[self.pos - 4 : self.pos])[0]
            elif info == 27:
                # 64-bit double-precision float
                return struct.unpack(">d", self.data[self.pos - 8 : self.pos])[0]
            elif info == 31:
                return "BREAK"
            raise ValueError(f"Unsupported CBOR simple/float value {info}")

        raise ValueError(f"Unknown CBOR major type {major}")


def decode_cbor(data: bytes) -> Any:
    """Decode raw bytes containing a CBOR object."""
    return CBORDecoder(data).decode()


def unescape_lua_string(s: str) -> bytes:
    """Unescape a Lua string containing raw binary bytes and escape sequences (\\ddd, \\", etc.)."""
    res = bytearray()
    i = 0
    n = len(s)
    mapping = {
        "a": 7,
        "b": 8,
        "f": 12,
        "n": 10,
        "r": 13,
        "t": 9,
        "v": 11,
        "\\": 92,
        '"': 34,
        "'": 39,
    }
    while i < n:
        c = s[i]
        if c == "\\" and i + 1 < n:
            c2 = s[i + 1]
            if c2.isdigit():
                i += 1
                d_str = c2
                if i + 1 < n and s[i + 1].isdigit():
                    i += 1
                    d_str += s[i]
                    if i + 1 < n and s[i + 1].isdigit():
                        i += 1
                        d_str += s[i]
                res.append(int(d_str))
            elif c2 in mapping:
                i += 1
                res.append(mapping[c2])
            else:
                # Literal backslash (e.g. from luadata unescaping '\\' before another non-escape char)
                res.append(92)
        else:
            res.append(ord(c) & 0xFF)
        i += 1
    return bytes(res)


def normalize_cbor_dict(obj: Any) -> Any:
    """Recursively convert bytes keys/values in a decoded CBOR structure to clean strings."""
    if isinstance(obj, dict):
        new_d: Dict[str, Any] = {}
        for k, v in obj.items():
            if isinstance(k, bytes):
                k = k.decode("utf-8", errors="replace")
            else:
                k = str(k)
            new_d[k] = normalize_cbor_dict(v)
        return new_d
    elif isinstance(obj, list):
        return [normalize_cbor_dict(x) for x in obj]
    elif isinstance(obj, bytes):
        return obj.decode("utf-8", errors="replace")
    return obj
