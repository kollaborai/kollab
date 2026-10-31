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
MAX_CALL_MESSAGE_BYTES = 32 * 1024  # one call message before outer encryption
IDLE_SECONDS = 3.0
SHUTDOWN_CAP_S = 5.0  # engine shutdown never waits longer than this on a voice task
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


def _settle(future: asyncio.Future | None, *, result=None, error=None) -> None:
    """Resolve an in-flight tick once; retries waiting on it get the same outcome."""
    if future is None or future.done():
        return
    if error is not None:
        future.set_exception(error)
        future.exception()  # mark retrieved when no retry is waiting
    else:
        future.set_result(result)


@dataclass(eq=False)
class _Opening:
    """An open still waiting on its bridge. Revoke, close or shutdown cancels it."""

    phone: str
    call_id: str
    session: object
    voice_session_id: str
    cancelled: bool = False
    task: asyncio.Task | None = None  # the open() running this; shutdown waits on it


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
    inflight_n: int = -1
    inflight: asyncio.Future | None = None
    last_tick: float = field(default_factory=time.monotonic)
    watchdog: asyncio.Task | None = None


class PhoneAudioManager:
    """One active call per phone and per voice session; the engine owns each bridge."""

    def __init__(self, open_bridge: BridgeOpener):
        self._open_bridge = open_bridge
        self._calls: dict[str, _Call] = {}
        self._pending: set[_Opening] = set()
        self._voice_owner: dict[str, str] = {}  # voice_session_id -> phone
        self._closing: set[asyncio.Task] = set()  # bridge closes still running
        self._shutting_down = False  # set by shutdown(): refuse every new open

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
        if self._shutting_down:
            raise PhoneCallError("call_unavailable")
        existing = self._calls.get(phone)
        if existing:
            if existing.id != call_id:
                raise PhoneCallError("call_busy")
            if existing.session is not session or existing.voice_session_id != (
                voice_session_id
            ) or existing.generation != generation:
                raise PhoneCallError("call_stale")
            return self._open_result(existing)
        owner = self._voice_owner.get(voice_session_id)
        if owner is not None and owner != phone:
            raise PhoneCallError("call_busy")  # one phone per voice session
        self._voice_owner[voice_session_id] = phone
        pending = _Opening(phone, call_id, session, voice_session_id)
        pending.task = asyncio.current_task()
        self._pending.add(pending)
        bridge: PhoneBridge | None = None
        try:
            try:
                bridge = await self._open_bridge(voice_session_id, generation)
            except PhoneCallError:
                raise
            except Exception:
                logger.warning("phone call bridge failed to open", exc_info=True)
                raise PhoneCallError("call_unavailable") from None
            if pending.cancelled:
                # Revoked, closed or shut down while the bridge was opening.
                raise PhoneCallError("call_unavailable")
            if phone not in self._calls:
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
                bridge = None  # the call owns it now
                return self._open_result(call)
        finally:
            # Runs on success, failure and cancellation alike. The voice is released
            # synchronously, before any await, so a cancel can never skip it. A bridge
            # this open did not register is closed in a tracked task (shutdown waits
            # on it), and the await only waits for that close.
            self._pending.discard(pending)
            self._release_voice(phone, voice_session_id)
            if bridge is not None:
                await asyncio.shield(self._close_bridge(bridge))
        # A racing open registered while our bridge was opening. Ours is closed and
        # our voice is released above, so the retry starts from a clean slate.
        return await self.open(phone, session, call_id, voice_session_id, generation)

    async def tick(
        self, phone: str, session: object, call_id: str, n: int, mic: bytes
    ) -> dict:
        call = self._owned(phone, session, call_id)
        if not _is_safe_uint(n) or type(mic) is not bytes or len(mic) != MIC_BYTES:
            raise PhoneCallError("call_invalid")
        if call.busy:
            if n == call.inflight_n and call.inflight is not None:
                return await asyncio.shield(call.inflight)  # retry of the tick in flight
            raise PhoneCallError("call_overloaded")
        if n == call.last_n and call.last_response is not None:
            return call.last_response  # lost response: resend without re-publishing
        if n != call.last_n + 1:
            await self._end(call, cause="sequence")
            raise PhoneCallError("call_sequence")
        call.busy = True
        call.inflight_n = n
        call.inflight = asyncio.get_running_loop().create_future()
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
        except PhoneCallError as exc:
            _settle(call.inflight, error=exc)
            raise
        except asyncio.CancelledError:
            # The mic may already be published and the reply lost: fail closed, so a
            # retry can never publish the same frame twice.
            _settle(call.inflight, error=PhoneCallError("call_unavailable"))
            await self._end(call, cause="cancelled")
            raise
        except Exception:
            logger.warning("phone call tick failed", exc_info=True)
            await self._end(call, cause="bridge_failed")
            failure = PhoneCallError("call_unavailable")
            _settle(call.inflight, error=failure)
            raise failure from None
        finally:
            call.busy = False
        if self._calls.get(phone) is not call:
            # Closed or revoked while this tick was in flight: never answer with audio.
            failure = PhoneCallError("call_unavailable")
            _settle(call.inflight, error=failure)
            raise failure
        call.last_n = n
        call.last_tick = time.monotonic()
        call.last_response = {
            "ok": True,
            "id": call.id,
            "n": n,
            "audio": base64.b64encode(audio).decode(),
            "dropped": call.dropped,
        }
        _settle(call.inflight, result=call.last_response)
        return call.last_response

    async def close(self, phone: str, session: object, call_id: str) -> None:
        for pending in list(self._pending):
            if pending.phone == phone and pending.call_id == call_id:
                if pending.session is not session:
                    raise PhoneCallError("call_forbidden")
                pending.cancelled = True  # fence the open still waiting on its bridge
        call = self._calls.get(phone)
        if call is None or call.id != call_id:
            return  # idempotent for the owner
        if call.session is not session:
            raise PhoneCallError("call_forbidden")
        await self._end(call, cause="closed")

    async def invalidate(self, phone: str, cause: str = "invalidated") -> None:
        for pending in list(self._pending):
            if pending.phone == phone:
                pending.cancelled = True
        call = self._calls.get(phone)
        if call is not None:
            await self._end(call, cause=cause)

    async def shutdown(self) -> None:
        self._shutting_down = True
        openers = []
        for pending in list(self._pending):
            pending.cancelled = True
            if pending.task is not None and pending.task is not asyncio.current_task():
                # Cancel the open: its finally closes any bridge it already returned, and
                # start_phone_bridge's except path closes a child spawned before ready.
                pending.task.cancel()
                openers.append(pending.task)
        await wait_capped(openers)
        for call in list(self._calls.values()):
            await self._end(call, cause="shutdown")
        # Every bridge close still running, including ones a cancel left behind. One capped
        # wait, no loop: a finished task never yields, so it would spin here, and its
        # discard callback may not have run yet, so prune done tasks ourselves.
        await wait_capped({t for t in self._closing if not t.done()})
        self._closing -= {t for t in self._closing if t.done()}

    def reopen(self) -> None:
        """Lift the shutdown fence so a restarted link on this manager can open calls."""
        self._shutting_down = False

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

    def _release_voice(self, phone: str, voice_session_id: str) -> None:
        """Drop the voice reservation once no call or pending open still holds it."""
        if self._voice_owner.get(voice_session_id) != phone:
            return
        if any(c.voice_session_id == voice_session_id for c in self._calls.values()):
            return
        if any(p.voice_session_id == voice_session_id for p in self._pending):
            return
        del self._voice_owner[voice_session_id]

    async def _watch(self, call: _Call) -> None:
        while self._calls.get(call.phone) is call:
            await asyncio.sleep(0.5)
            if time.monotonic() - call.last_tick > IDLE_SECONDS:
                await self._end(call, cause="timeout", from_watchdog=True)
                return

    async def _end(self, call: _Call, *, cause: str, from_watchdog: bool = False) -> None:
        if self._calls.get(call.phone) is call:
            del self._calls[call.phone]
        self._release_voice(call.phone, call.voice_session_id)
        _settle(call.inflight, error=PhoneCallError("call_unavailable"))
        call.out.clear()
        if call.watchdog and not from_watchdog:
            call.watchdog.cancel()
        await asyncio.shield(self._close_bridge(call.bridge))
        logger.info("phone call %s ended: %s", call.id[:8], cause)

    def _close_bridge(self, bridge: PhoneBridge) -> asyncio.Task:
        """Start a bridge close that outlives a cancelled caller; shutdown awaits it."""
        task = asyncio.get_running_loop().create_task(_close_quietly(bridge))
        self._closing.add(task)
        task.add_done_callback(self._closing.discard)
        return task


async def wait_capped(tasks) -> bool:
    """Wait up to SHUTDOWN_CAP_S for tasks; True when all finished, else log and False."""
    tasks = list(tasks)
    if not tasks:
        return True
    _, still_running = await asyncio.wait(tasks, timeout=SHUTDOWN_CAP_S)
    if still_running:
        logger.warning(
            "%d phone call task(s) still running %.0f s into shutdown",
            len(still_running),
            SHUTDOWN_CAP_S,
        )
        return False
    return True


async def _close_quietly(bridge: PhoneBridge) -> None:
    try:
        await bridge.close()
    except Exception:
        logger.warning("phone call bridge close failed", exc_info=True)
