"""Minimal OSC 1.0 encoder/decoder.

WING 는 OSC 를 UDP 2223 포트로 받는다 (WING Remote Protocols V3.0.6-30).
외부 라이브러리 없이 필요한 타입(s, i, f)만 구현해 바이트 단위로 검증할 수 있게 한다.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field


class OscDecodeError(ValueError):
    pass


def _pad(data: bytes) -> bytes:
    # OSC 문자열은 NULL 종료 후 4바이트 경계까지 NULL 로 채운다.
    return data + b"\x00" * (4 - (len(data) % 4))


def encode_string(text: str) -> bytes:
    return _pad(text.encode("utf-8"))


@dataclass
class OscMessage:
    address: str
    args: list = field(default_factory=list)

    @property
    def typetags(self) -> str:
        tags = ""
        for a in self.args:
            if isinstance(a, bool):
                raise TypeError("bool 은 WING 에서 ,i 0/1 로 보낸다")
            if isinstance(a, int):
                tags += "i"
            elif isinstance(a, float):
                tags += "f"
            elif isinstance(a, str):
                tags += "s"
            else:
                raise TypeError(f"지원하지 않는 OSC 인자 타입: {type(a)!r}")
        return tags


def encode(address: str, *args) -> bytes:
    if not address.startswith("/"):
        raise ValueError("OSC 주소는 '/' 로 시작해야 한다")
    msg = OscMessage(address, list(args))
    out = encode_string(address)
    if not args:
        # WING 문서: 인자 없는 주소 = 값 조회(get)
        return out
    out += encode_string("," + msg.typetags)
    for a in args:
        if isinstance(a, int):
            out += struct.pack(">i", a)
        elif isinstance(a, float):
            out += struct.pack(">f", a)
        else:
            out += encode_string(a)
    return out


def _read_string(data: bytes, pos: int) -> tuple[str, int]:
    end = data.find(b"\x00", pos)
    if end < 0:
        raise OscDecodeError("NULL 종료 문자열이 아니다")
    text = data[pos:end].decode("utf-8", errors="replace")
    length = end - pos + 1
    pos += length + ((4 - length % 4) % 4)
    return text, pos


def decode(data: bytes) -> OscMessage:
    if not data or data[0:1] != b"/":
        raise OscDecodeError("OSC 메시지가 아니다")
    address, pos = _read_string(data, 0)
    if pos >= len(data):
        return OscMessage(address, [])
    if data[pos:pos + 1] != b",":
        raise OscDecodeError("타입 태그가 없다")
    tags, pos = _read_string(data, pos)
    args: list = []
    for t in tags[1:]:
        if t == "i":
            args.append(struct.unpack(">i", data[pos:pos + 4])[0])
            pos += 4
        elif t == "f":
            args.append(struct.unpack(">f", data[pos:pos + 4])[0])
            pos += 4
        elif t == "s":
            s, pos = _read_string(data, pos)
            args.append(s)
        elif t == "T":
            args.append(True)
        elif t == "F":
            args.append(False)
        elif t == "N":
            args.append(None)
        elif t == "b":
            (n,) = struct.unpack(">i", data[pos:pos + 4])
            pos += 4
            args.append(data[pos:pos + n])
            pos += n + ((4 - n % 4) % 4)
        else:
            raise OscDecodeError(f"지원하지 않는 타입 태그: {t}")
        if pos > len(data):
            raise OscDecodeError("메시지가 잘렸다")
    return OscMessage(address, args)
