# Astrix — by UNDEAD (https://github.com/itsund3ad)
# Wire format: Frame struct with marshal/unmarshal, flags constants.
# Byte-compatible with GooseRelayVPN internal/frame.
# Optimized: pre-allocated buffers, marshal_into zero-copy inline,
# pooled memory, zero-copy slicing.

import struct
from dataclasses import dataclass, field
from typing import Optional
from collections.abc import Sequence

SessionIDLen = 16
ClientIDLen = 16
maxTargetLen = 255
maxPayloadSize = 10 * 1024 * 1024

FlagSYN = 1 << 0
FlagFIN = 1 << 1
FlagACK = 1 << 2
FlagRST = 1 << 3

_marshal_pool: list[bytearray] = []
_marshal_pool_lock = __import__("threading").Lock()


def _get_marshal_buf(capacity: int = 65536) -> bytearray:
    global _marshal_pool
    with _marshal_pool_lock:
        if _marshal_pool:
            buf = _marshal_pool.pop()
            if len(buf) >= capacity:
                buf.clear()
                return buf
    return bytearray(capacity)


def _put_marshal_buf(buf: bytearray):
    global _marshal_pool
    buf.clear()
    with _marshal_pool_lock:
        if len(_marshal_pool) < 64:
            _marshal_pool.append(buf)


@dataclass(slots=True)
class Frame:
    session_id: bytes = field(default_factory=lambda: b"\x00" * SessionIDLen)
    seq: int = 0
    flags: int = 0
    target: str = ""
    payload: bytes = b""

    def has_flag(self, flag: int) -> bool:
        return (self.flags & flag) != 0

    def encoded_len(self) -> int:
        target_bytes = self.target.encode("utf-8")
        return SessionIDLen + 8 + 1 + 1 + len(target_bytes) + 4 + len(self.payload)


def _marshal_size(frame: Frame) -> int:
    """Fast inline size calculation without encoding target."""
    return SessionIDLen + 8 + 1 + 1 + len(frame.target) + 4 + len(frame.payload)


marshal_encoded_len = _marshal_size  # alias for hot-path clarity


def marshal_into(buf: bytearray, offset: int, frame: Frame) -> int:
    """Marshal a Frame directly into buf at offset. Returns bytes written.

    This is the zero-copy hot path — no intermediate bytes objects.
    Only writes payload length, not the payload bytes (payload is
    written separately by the caller to avoid double-copy).
    """
    target_bytes = frame.target.encode("utf-8")
    tlen = len(target_bytes)
    plen = len(frame.payload)

    off = offset
    buf[off : off + SessionIDLen] = frame.session_id
    off += SessionIDLen
    struct.pack_into(">Q", buf, off, frame.seq)
    off += 8
    buf[off] = frame.flags & 0xFF
    off += 1
    buf[off] = tlen
    off += 1
    if tlen:
        buf[off : off + tlen] = target_bytes
        off += tlen
    struct.pack_into(">I", buf, off, plen)
    off += 4

    return off - offset


def marshal(frame: Frame, buf: Optional[bytearray] = None) -> bytes:
    """Marshal a Frame into bytes with pre-allocated buffer pool."""
    target_bytes = frame.target.encode("utf-8")
    tlen = len(target_bytes)
    plen = len(frame.payload)

    if tlen > maxTargetLen:
        raise ValueError(f"target too long: {tlen} > {maxTargetLen}")
    if plen > maxPayloadSize:
        raise ValueError(f"payload too large: {plen}")

    size = SessionIDLen + 8 + 1 + 1 + tlen + 4 + plen

    own_buf = buf is None
    if own_buf:
        buf = _get_marshal_buf(size)
    else:
        buf.clear()

    offset = len(buf)
    buf.extend(b"\x00" * size)
    view = buf[offset:]

    off = 0
    view[off : off + SessionIDLen] = frame.session_id
    off += SessionIDLen
    struct.pack_into(">Q", view, off, frame.seq)
    off += 8
    view[off] = frame.flags & 0xFF
    off += 1
    view[off] = tlen
    off += 1
    if tlen:
        view[off : off + tlen] = target_bytes
        off += tlen
    struct.pack_into(">I", view, off, plen)
    off += 4
    if plen:
        view[off : off + plen] = frame.payload
        off += plen

    result = bytes(buf[offset:offset+size])

    if own_buf:
        _put_marshal_buf(buf)

    return result


def unmarshal(data: bytes) -> tuple[Frame, int]:
    if len(data) < SessionIDLen + 8 + 1 + 1 + 4:
        raise ValueError("frame: short header")

    off = 0
    session_id = data[off : off + SessionIDLen]
    off += SessionIDLen

    seq = struct.unpack_from(">Q", data, off)[0]
    off += 8

    flags = data[off]
    off += 1

    tlen = data[off]
    off += 1

    if len(data) < off + tlen + 4:
        raise ValueError("frame: short target/len")

    target = ""
    if tlen:
        target = data[off : off + tlen].decode("utf-8")
    off += tlen

    plen = struct.unpack_from(">I", data, off)[0]
    off += 4

    if plen > maxPayloadSize:
        raise ValueError(f"frame: payload too large: {plen}")

    if len(data) < off + plen:
        raise ValueError("frame: short payload")

    payload = data[off : off + plen]
    off += plen

    return Frame(
        session_id=session_id,
        seq=seq,
        flags=flags,
        target=target,
        payload=payload,
    ), off


def marshal_batch(frames: Sequence[Frame]) -> bytes:
    total = 0
    entries: list[tuple[int, bytes, bytes]] = []
    for f in frames:
        tb = f.target.encode("utf-8")
        flen = SessionIDLen + 8 + 1 + 1 + len(tb) + 4 + len(f.payload)
        entries.append((flen, tb, f.payload))
        total += 4 + flen

    buf = bytearray(total)
    off = 0
    for (flen, tb, _), f in zip(entries, frames):
        struct.pack_into(">I", buf, off, flen)
        off += 4
        buf[off : off + SessionIDLen] = f.session_id
        off += SessionIDLen
        struct.pack_into(">Q", buf, off, f.seq)
        off += 8
        buf[off] = f.flags & 0xFF
        off += 1
        buf[off] = len(tb)
        off += 1
        if tb:
            buf[off : off + len(tb)] = tb
            off += len(tb)
        struct.pack_into(">I", buf, off, len(f.payload))
        off += 4
        if f.payload:
            buf[off : off + len(f.payload)] = f.payload
            off += len(f.payload)

    return bytes(buf)
