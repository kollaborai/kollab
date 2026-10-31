"""Phone call audio broker: bounded PCM ticks between a phone and a voice bridge.

The phone sends 200 ms of mic PCM per tick and gets back up to 200 ms of agent
audio. Each call is bound to one approved phone, one PhoneSession object, one
voice session and one generation. Audio is never logged.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Protocol

logger = logging.getLogger(__name__)

MIC_BYTES = 6400  # 200 ms of PCM16LE mono at 16 kHz
MAX_AUDIO_BYTES = 9600  # 200 ms of PCM16LE mono at 24 kHz
QUEUE_BYTES = 28800  # 600 ms of PCM16LE mono at 24 kHz
IDLE_SECONDS = 3.0
MAX_SAFE_INT = 2**53 - 1

CALL_ID = re.compile(r"[0-9a-f]{32}")
VOICE_SESSION_ID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


class PhoneCallError(ValueError):
    """A call failure with a stable, non-secret code (no audio in the message)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class PhoneBridge(Protocol):
    """A joined voice participant for one call (see kollabor_engine/voice_livekit)."""

    async def send_mic(self, n: int, pcm: bytes) -> None: ...

    def take_output(self) -> bytes: ...

    async def close(self) -> None: ...


BridgeOpener = Callable[[str, int], Awaitable[PhoneBridge]]


def _is_safe_uint(value: object) -> bool:
    return type(value) is int and 0 <= value <= MAX_SAFE_INT


@dataclass
class _Call:
    id: str
    phone: str
    session: object
    voice_session_id: str
    generation: int
    bridge: PhoneBridge
    last_n: int = -1
    last_response: dict | None = None
    out: bytearray = field(default_factory=bytearray)
    dropped: int = 0
    busy: bool = False
    last_tick: float = field(default_factory=time.monotonic)
    watchdog: asyncio.Task | None = None


class PhoneAudioManager:
    """One active call per phone; the engine owns every bridge it opens."""

    def __init__(self, open_bridge: BridgeOpener):
        self._open_bridge = open_bridge
        self._calls: dict[str, _Call] = {}

    async def open(
        self,
        phone: str,
        session: object,
        call_id: str,
        voice_session_id: str,
        generation: int,
    ) -> dict:
        if not isinstance(call_id, str) or not CALL_ID.fullmatch(call_id):
            raise PhoneCallError("call_invalid")
        if not isinstance(voice_session_id, str) or not VOICE_SESSION_ID.fullmatch(
            voice_session_id
        ):
            raise PhoneCallError("call_invalid")
        if not _is_safe_uint(generation) or session is None:
            raise PhoneCallError("call_invalid")
        existing = self._calls.get(phone)
        if existing:
            if existing.id != call_id:
                raise PhoneCallError("call_busy")
            if existing.session is not session or existing.voice_session_id != (
                voice_session_id
            ) or existing.generation != generation:
                raise PhoneCallError("call_stale")
            return self._open_result(existing)
        try:
            bridge = await self._open_bridge(voice_session_id, generation)
        except PhoneCallError:
            raise
        except Exception:
            logger.warning("phone call bridge failed to open", exc_info=True)
            raise PhoneCallError("call_unavailable") from None
        call = _Call(
            id=call_id,
            phone=phone,
            session=session,
            voice_session_id=voice_session_id,
            generation=generation,
            bridge=bridge,
        )
        call.watchdog = asyncio.create_task(self._watch(call))
        self._calls[phone] = call
        return self._open_result(call)

    async def tick(
        self, phone: str, session: object, call_id: str, n: int, mic: bytes
    ) -> dict:
        call = self._owned(phone, session, call_id)
        if not _is_safe_uint(n) or type(mic) is not bytes or len(mic) != MIC_BYTES:
            raise PhoneCallError("call_invalid")
        if call.busy:
            raise PhoneCallError("call_overloaded")
        if n == call.last_n and call.last_response is not None:
            return call.last_response  # lost response: resend without re-publishing
        if n != call.last_n + 1:
            await self._end(call, cause="sequence")
            raise PhoneCallError("call_sequence")
        call.busy = True
        try:
            await call.bridge.send_mic(n, mic)
            call.out.extend(call.bridge.take_output())
            overflow = max(0, len(call.out) - QUEUE_BYTES)
            if overflow:
                del call.out[:overflow]
                call.dropped += overflow // 2
            take = min(len(call.out), MAX_AUDIO_BYTES) & ~1
            audio = bytes(call.out[:take])
            del call.out[:take]
        except PhoneCallError:
            raise
        except Exception:
            logger.warning("phone call tick failed", exc_info=True)
            await self._end(call, cause="bridge_failed")
            raise PhoneCallError("call_unavailable") from None
        finally:
            call.busy = False
        call.last_n = n
        call.last_tick = time.monotonic()
        call.last_response = {
            "ok": True,
            "id": call.id,
            "n": n,
            "audio": base64.b64encode(audio).decode(),
            "dropped": call.dropped,
        }
        return call.last_response

    async def close(self, phone: str, session: object, call_id: str) -> None:
        call = self._calls.get(phone)
        if call is None or call.id != call_id:
            return  # idempotent for the owner
        if call.session is not session:
            raise PhoneCallError("call_forbidden")
        await self._end(call, cause="closed")

    async def invalidate(self, phone: str, cause: str = "invalidated") -> None:
        call = self._calls.get(phone)
        if call is not None:
            await self._end(call, cause=cause)

    async def shutdown(self) -> None:
        for call in list(self._calls.values()):
            await self._end(call, cause="shutdown")

    def _open_result(self, call: _Call) -> dict:
        return {
            "ok": True,
            "id": call.id,
            "input_rate": 16000,
            "output_rate": 24000,
            "frame_ms": 200,
        }

    def _owned(self, phone: str, session: object, call_id: str) -> _Call:
        call = self._calls.get(phone)
        if call is None:
            raise PhoneCallError("call_not_ready")
        if call.id != call_id:
            raise PhoneCallError("call_invalid")
        if call.session is not session:
            raise PhoneCallError("call_stale")
        return call

    async def _watch(self, call: _Call) -> None:
        while self._calls.get(call.phone) is call:
            await asyncio.sleep(0.5)
            if time.monotonic() - call.last_tick > IDLE_SECONDS:
                await self._end(call, cause="timeout", from_watchdog=True)
                return

    async def _end(self, call: _Call, *, cause: str, from_watchdog: bool = False) -> None:
        if self._calls.get(call.phone) is call:
            del self._calls[call.phone]
        call.out.clear()
        if call.watchdog and not from_watchdog:
            call.watchdog.cancel()
        try:
            await call.bridge.close()
        except Exception:
            logger.warning("phone call bridge close failed", exc_info=True)
        logger.info("phone call %s ended: %s", call.id[:8], cause)
