"""KVA1 framing for the voice service's per-user audio socket.

Header (20 bytes, big endian): magic "KVA1", version u8, kind u8, flags u16,
sequence u64, payload_length u32. Control kinds carry JSON (<= 8 KiB); PCM
carries float32 little endian mono (<= 64 KiB, <= 100 ms). Standard library
only so both ends can import it.
"""

from __future__ import annotations

import json
import struct

MAGIC = b"KVA1"
VERSION = 1
HEADER = struct.Struct(">4sBBHQI")
MAX_CONTROL = 8 * 1024
MAX_PCM = 64 * 1024
MAX_FRAME_SECONDS = 0.1

OPEN, PCM, END, CANCEL, RESULT, ERROR, SPEAK = 1, 2, 3, 4, 5, 6, 7
CONTROL_KINDS = {OPEN, END, CANCEL, RESULT, ERROR, SPEAK}
FLAG_FRAME_END = 1


class FrameError(ValueError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code


def encode(kind: int, sequence: int, payload: bytes = b"", flags: int = 0) -> bytes:
    limit = MAX_CONTROL if kind in CONTROL_KINDS else MAX_PCM
    if kind not in CONTROL_KINDS | {PCM} or len(payload) > limit:
        raise FrameError("invalid_audio", "KVA1 frame kind or size is invalid")
    return HEADER.pack(MAGIC, VERSION, kind, flags, sequence, len(payload)) + payload


def control(kind: int, sequence: int, **body) -> bytes:
    return encode(kind, sequence, json.dumps(body).encode())


async def read_frame(reader):
    """(kind, flags, sequence, payload), or None on a clean close."""
    try:
        head = await reader.readexactly(HEADER.size)
    except Exception as exc:  # IncompleteReadError at EOF
        if getattr(exc, "partial", b"x") == b"":
            return None
        raise FrameError("invalid_audio", "KVA1 stream ended mid header") from exc
    magic, version, kind, flags, sequence, length = HEADER.unpack(head)
    if magic != MAGIC or version != VERSION:
        raise FrameError("invalid_audio", "Not a KVA1 version 1 frame")
    limit = MAX_CONTROL if kind in CONTROL_KINDS else MAX_PCM
    if kind not in CONTROL_KINDS | {PCM} or length > limit:
        raise FrameError("invalid_audio", "KVA1 frame kind or size is invalid")
    return kind, flags, sequence, await reader.readexactly(length)


def parse_control(payload: bytes) -> dict:
    try:
        value = json.loads(payload)
    except ValueError as exc:
        raise FrameError("invalid_audio", "KVA1 control frame is not JSON") from exc
    if not isinstance(value, dict):
        raise FrameError("invalid_audio", "KVA1 control frame must be an object")
    return value


def pcm_to_bytes(samples) -> bytes:
    """float32 numpy mono -> payload; rejects non finite or out of range."""
    import numpy as np

    audio = np.ascontiguousarray(samples, dtype="<f4")
    if (
        audio.ndim != 1
        or not np.isfinite(audio).all()
        or np.abs(audio).max(initial=0.0) > 1.0
    ):
        raise FrameError("invalid_audio", "Audio must be finite mono within [-1, 1]")
    return audio.tobytes()


def pcm_from_bytes(payload: bytes):
    import numpy as np

    if len(payload) % 4:
        raise FrameError("invalid_audio", "PCM payload is not whole float32 samples")
    audio = np.frombuffer(payload, dtype="<f4")
    if not np.isfinite(audio).all() or np.abs(audio).max(initial=0.0) > 1.0:
        raise FrameError("invalid_audio", "Audio must be finite within [-1, 1]")
    return audio


def split_pcm(samples, rate: int):
    """Yield <= 100 ms (and <= 64 KiB) payloads from a float32 array."""
    step = max(1, min(int(rate * MAX_FRAME_SECONDS), MAX_PCM // 4))
    for start in range(0, len(samples), step):
        yield pcm_to_bytes(samples[start : start + step])
