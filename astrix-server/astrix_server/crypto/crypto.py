# Astrix — by UNDEAD (https://github.com/itsund3ad)
# AES-256-GCM batch envelope with Zstandard compression.
# Byte-compatible with GooseRelayVPN internal/frame/crypto.go.
# Optimized: inline marshal (zero intermediate bytes), adaptive compression,
# Zstd compressor/decompressor pool, pre-sized buffer.

import struct
import base64
import logging
from collections.abc import Sequence

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import zstandard as zstd

from astrix_server.frame.frame import (
    Frame,
    SessionIDLen,
    ClientIDLen,
    marshal_into,
    _marshal_size,
)

logger = logging.getLogger("crypto")

batchFlagRaw = 0x00
batchFlagFlate = 0x01
batchFlagZstd = 0x02

compressMinSize = 512
_skipCompressionAfter = 3
_recheckAfter = 10

_zstd_enc_pool: list[zstd.ZstdCompressor] = []
_zstd_dec_pool: list[zstd.ZstdDecompressor] = []
_pool_lock = __import__("threading").Lock()

_adaptive_skip_counter = 0
_adaptive_skip_active = False


def _get_zstd_enc() -> zstd.ZstdCompressor:
    with _pool_lock:
        if _zstd_enc_pool:
            return _zstd_enc_pool.pop()
    return zstd.ZstdCompressor(level=1)


def _put_zstd_enc(enc: zstd.ZstdCompressor):
    with _pool_lock:
        if len(_zstd_enc_pool) < 8:
            _zstd_enc_pool.append(enc)


def _get_zstd_dec() -> zstd.ZstdDecompressor:
    with _pool_lock:
        if _zstd_dec_pool:
            return _zstd_dec_pool.pop()
    return zstd.ZstdDecompressor()


def _put_zstd_dec(dec: zstd.ZstdDecompressor):
    with _pool_lock:
        if len(_zstd_dec_pool) < 8:
            _zstd_dec_pool.append(dec)


class Crypto:
    __slots__ = ("_aead",)

    def __init__(self, key: bytes):
        if len(key) != 32:
            raise ValueError(f"AES key must be 32 bytes, got {len(key)}")
        self._aead = AESGCM(key)

    @classmethod
    def from_hex_key(cls, hex_key: str) -> "Crypto":
        key = bytes.fromhex(hex_key)
        if len(key) != 32:
            raise ValueError(
                f"hex key must decode to 32 bytes (64 hex chars), got {len(key)}"
            )
        return cls(key)

    def seal(self, plaintext: bytes) -> bytes:
        nonce = AESGCM.generate_nonce()
        ct = self._aead.encrypt(nonce, plaintext, None)
        return nonce + ct

    def open(self, envelope: bytes) -> bytes:
        if len(envelope) < 12 + 16:
            raise ValueError("crypto: envelope too short")
        nonce = envelope[:12]
        ct = envelope[12:]
        return self._aead.decrypt(nonce, ct, None)


def encode_batch(
    crypto: Crypto, client_id: bytes, frames: Sequence[Frame]
) -> bytes:
    global _adaptive_skip_counter, _adaptive_skip_active

    nframes = len(frames)
    if nframes > 0xFFFF:
        raise ValueError(f"batch: too many frames: {nframes}")
    if len(client_id) != ClientIDLen:
        raise ValueError(f"client_id must be {ClientIDLen} bytes, got {len(client_id)}")

    header_size = 1 + ClientIDLen + 2
    frames_size = 0
    for f in frames:
        frames_size += 4 + _marshal_size(f)
    plain_size = header_size + frames_size

    buf = bytearray(plain_size)
    off = 0
    buf[off] = 0x00
    off += 1
    buf[off : off + ClientIDLen] = client_id
    off += ClientIDLen
    struct.pack_into(">H", buf, off, nframes)
    off += 2

    for f in frames:
        flen = _marshal_size(f)
        struct.pack_into(">I", buf, off, flen)
        off += 4
        written = marshal_into(buf, off, f)
        off += written
        if f.payload:
            buf[off : off + len(f.payload)] = f.payload
            off += len(f.payload)

    raw_size = len(buf)
    should_compress = raw_size - 1 >= compressMinSize and (
        not _adaptive_skip_active or _adaptive_skip_counter % _recheckAfter == 0
    )

    if should_compress:
        enc = _get_zstd_enc()
        compressed = enc.compress(bytes(buf[1:]))
        _put_zstd_enc(enc)
        compressed_full = b"\x02" + compressed
        if len(compressed_full) < raw_size:
            buf[0] = 0x02
            seal_input = compressed_full
            _adaptive_skip_active = False
            _adaptive_skip_counter = 0
        else:
            buf[0] = batchFlagRaw
            seal_input = bytes(buf)
            _adaptive_skip_counter += 1
            if _adaptive_skip_counter >= _skipCompressionAfter:
                _adaptive_skip_active = True
    else:
        if _adaptive_skip_active:
            _adaptive_skip_counter += 1
        buf[0] = batchFlagRaw
        seal_input = bytes(buf)

    sealed = crypto.seal(seal_input)
    return base64.b64encode(sealed)


def _base64_decode(data: bytes) -> bytes:
    return base64.b64decode(data)


def decode_batch(
    crypto: Crypto, body: bytes
) -> tuple[bytes, list[Frame]]:
    zero_id = b"\x00" * ClientIDLen
    if not body:
        return zero_id, []

    sealed = _base64_decode(body)
    raw_plain = crypto.open(sealed)
    if not raw_plain:
        raise ValueError("batch: empty plaintext")

    flags_byte = raw_plain[0]

    if flags_byte == batchFlagRaw:
        plaintext = raw_plain[1:]
    elif flags_byte == batchFlagFlate:
        import zlib
        plaintext = zlib.decompress(raw_plain[1:], -zlib.MAX_WBITS)
    elif flags_byte == batchFlagZstd:
        dec = _get_zstd_dec()
        plaintext = dec.decompress(raw_plain[1:])
        _put_zstd_dec(dec)
    else:
        raise ValueError(f"batch: unknown flags byte 0x{flags_byte:02x}")

    if len(plaintext) < ClientIDLen + 2:
        raise ValueError("batch: short header")

    client_id = plaintext[:ClientIDLen]
    off = ClientIDLen
    count = struct.unpack_from(">H", plaintext, off)[0]
    off += 2

    frames: list[Frame] = []
    for _ in range(count):
        if len(plaintext) < off + 4:
            raise ValueError("batch: short frame length")
        flen = struct.unpack_from(">I", plaintext, off)[0]
        off += 4
        if len(plaintext) < off + flen:
            raise ValueError("batch: short frame body")
        sid = plaintext[off : off + SessionIDLen]
        seq = struct.unpack_from(">Q", plaintext, off + SessionIDLen)[0]
        flags = plaintext[off + SessionIDLen + 8]
        tlen = plaintext[off + SessionIDLen + 9]
        target_off = off + SessionIDLen + 10
        target = plaintext[target_off : target_off + tlen].decode("utf-8", errors="replace")
        plen_off = target_off + tlen
        plen = struct.unpack_from(">I", plaintext, plen_off)[0]
        payload_off = plen_off + 4
        payload = plaintext[payload_off : payload_off + plen]
        off = payload_off + plen
        frames.append(Frame(session_id=sid, seq=seq, flags=flags, target=target, payload=payload))

    return client_id, frames
