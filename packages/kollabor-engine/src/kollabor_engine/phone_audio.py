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
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Protocol

logger = logging.getLogger(__name__)

MIC_BYTES = 6400  # 200 ms of PCM16LE mono at 16 kHz
MAX_AUDIO_BYTES = 9600  # 200 ms of PCM16LE mono at 24 kHz
QUEUE_BYTES = 28800  # 600 ms of PCM16LE mono at 24 kHz
MAX_CALL_MESSAGE_BYTES = 32 * 1024  # one call message before outer encryption
MAX_PENDING_TICKS = 3
COMPLETED_TICKS = 3
IDLE_SECONDS = 3.0
PUBLISH_TIMEOUT_S = 5.0  # accepted work cannot hold a call open indefinitely
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
    """One reserved call-open shared by all identical open RPC waiters."""

    phone: str
    call_id: str
    session: object
    voice_session_id: str
    generation: int
    future: asyncio.Future
    cancelled: bool = False
    started: bool = False
    cancel_waiters_on_abort: bool = False
    task: asyncio.Task | None = None  # owns bridge creation and registration
    waiters: set[asyncio.Task] = field(default_factory=set)


@dataclass(eq=False)
class _PendingTick:
    n: int
    mic: bytes
    future: asyncio.Future
    deadline: float


@dataclass
class _Call:
    id: str
    phone: str
    session: object
    voice_session_id: str
    generation: int
    bridge: PhoneBridge
    next_n: int = 0
    pending: dict[int, _PendingTick] = field(default_factory=dict)
    completed: OrderedDict[int, tuple[bytes, dict]] = field(default_factory=OrderedDict)
    publisher: asyncio.Task | None = None
    ended: bool = False
    bridge_close: asyncio.Task | None = None
    out: bytearray = field(default_factory=bytearray)
    dropped: int = 0
    bridge_drops_seen: int = 0  # the bridge's out_dropped_bytes already counted into dropped
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
        pending = next((p for p in self._pending if p.phone == phone), None)
        if pending is not None:
            if pending.call_id != call_id:
                raise PhoneCallError("call_busy")
            if (
                pending.session is not session
                or pending.voice_session_id != voice_session_id
                or pending.generation != generation
            ):
                raise PhoneCallError("call_stale")
            return await self._wait_open(pending)

        owner = self._voice_owner.get(voice_session_id)
        if owner is not None and owner != phone:
            raise PhoneCallError("call_busy")  # one phone per voice session
        self._voice_owner[voice_session_id] = phone
        pending = _Opening(
            phone,
            call_id,
            session,
            voice_session_id,
            generation,
            asyncio.get_running_loop().create_future(),
        )
        self._pending.add(pending)
        pending.task = asyncio.create_task(self._open_reserved(pending))
        return await self._wait_open(pending)

    async def _wait_open(self, pending: _Opening) -> dict:
        waiter = asyncio.current_task()
        if waiter is not None:
            pending.waiters.add(waiter)
        try:
            return await asyncio.shield(pending.future)
        except PhoneCallError:
            if pending.cancel_waiters_on_abort:
                raise asyncio.CancelledError
            raise
        except asyncio.CancelledError:
            pending.waiters.discard(waiter)
            if (
                not pending.waiters
                and not pending.future.done()
                and not pending.cancelled
            ):
                openers = []
                self._cancel_opening(pending, openers)
                await wait_capped(openers)
            raise
        finally:
            pending.waiters.discard(waiter)

    async def _open_reserved(self, pending: _Opening) -> None:
        pending.started = True
        bridge: PhoneBridge | None = None
        failure: PhoneCallError | None = None
        try:
            try:
                bridge = await self._open_bridge(
                    pending.voice_session_id, pending.generation
                )
            except PhoneCallError as exc:
                _settle(pending.future, error=exc)
                return
            except Exception:
                logger.warning("phone call bridge failed to open", exc_info=True)
                _settle(
                    pending.future, error=PhoneCallError("call_unavailable")
                )
                return
            if pending.cancelled or self._shutting_down:
                # Revoked, closed or shut down while the bridge was opening.
                failure = PhoneCallError("call_unavailable")
                return
            if pending.phone in self._calls:
                failure = PhoneCallError("call_busy")
                return
            call = _Call(
                id=pending.call_id,
                phone=pending.phone,
                session=pending.session,
                voice_session_id=pending.voice_session_id,
                generation=pending.generation,
                bridge=bridge,
            )
            call.watchdog = asyncio.create_task(self._watch(call))
            self._calls[pending.phone] = call
            bridge = None  # the registered call owns it now
            _settle(pending.future, result=self._open_result(call))
        except asyncio.CancelledError:
            pending.cancel_waiters_on_abort = True
            failure = PhoneCallError("call_unavailable")
            raise
        finally:
            # Release failed/cancelled reservations synchronously. A successful call
            # remains the voice owner after the pending-opening entry is removed.
            self._pending.discard(pending)
            self._release_voice(pending.phone, pending.voice_session_id)
            if bridge is not None:
                await asyncio.shield(self._close_bridge(bridge))
            if failure is not None:
                _settle(pending.future, error=failure)

    async def tick(
        self, phone: str, session: object, call_id: str, n: int, mic: bytes
    ) -> dict:
        call = self._owned(phone, session, call_id)
        if not _is_safe_uint(n) or type(mic) is not bytes or len(mic) != MIC_BYTES:
            raise PhoneCallError("call_invalid")

        pending = call.pending.get(n)
        if pending is not None:
            if pending.mic != mic:
                await self._end(call, cause="retry_mismatch")
                raise PhoneCallError("call_sequence")
            return await self._await_tick(call, pending)

        cached = call.completed.get(n)
        if cached is not None:
            original_mic, response = cached
            if original_mic != mic:
                await self._end(call, cause="retry_mismatch")
                raise PhoneCallError("call_sequence")
            return response

        if n != call.next_n:
            await self._end(call, cause="sequence")
            raise PhoneCallError("call_sequence")

        # Admission is bounded independently of the publisher. A rejected tick does
        # not advance next_n, so the same contiguous n can be retried after capacity
        # becomes available.
        if len(call.pending) >= MAX_PENDING_TICKS:
            raise PhoneCallError("call_overloaded")
        if call.next_n > MAX_SAFE_INT:
            await self._end(call, cause="sequence_exhausted")
            raise PhoneCallError("call_sequence")

        future = asyncio.get_running_loop().create_future()
        pending = _PendingTick(
            n=n,
            mic=mic,
            future=future,
            deadline=time.monotonic() + PUBLISH_TIMEOUT_S,
        )
        call.pending[n] = pending
        call.next_n += 1
        call.last_tick = time.monotonic()  # accepted work is activity, even before publish
        if call.publisher is None or call.publisher.done():
            call.publisher = asyncio.create_task(self._publish(call))
        return await self._await_tick(call, pending)

    async def _await_tick(self, call: _Call, pending: _PendingTick) -> dict:
        # A disconnected/timed-out RPC waiter does not own accepted work. Keep the
        # shared future and publisher alive so a retry can retrieve the exact result.
        return await asyncio.shield(pending.future)

    async def _publish(self, call: _Call) -> None:
        """Serialize accepted mic frames through the bridge in sequence order."""
        current = asyncio.current_task()
        try:
            while call.pending and self._calls.get(call.phone) is call:
                n = min(call.pending)
                pending = call.pending[n]
                try:
                    remaining = max(0.0, pending.deadline - time.monotonic())
                    if remaining == 0.0:
                        raise TimeoutError("phone call publish deadline expired")
                    await asyncio.wait_for(
                        call.bridge.send_mic(n, pending.mic), timeout=remaining
                    )
                    call.out.extend(call.bridge.take_output())
                    # Bytes dropped upstream count even when the phone queue is empty.
                    upstream = getattr(call.bridge, "out_dropped_bytes", 0)
                    call.dropped += (upstream - call.bridge_drops_seen) // 2
                    call.bridge_drops_seen = upstream
                    overflow = max(0, len(call.out) - QUEUE_BYTES)
                    if overflow:
                        del call.out[:overflow]
                        call.dropped += overflow // 2
                    take = min(len(call.out), MAX_AUDIO_BYTES) & ~1
                    audio = bytes(call.out[:take])
                    del call.out[:take]
                    if getattr(call.bridge, "bridge_errors", 0):
                        logger.warning(
                            "phone call bridge reported an error: %s",
                            getattr(call.bridge, "bridge_last_error", None),
                        )
                        raise PhoneCallError("call_unavailable")
                    if self._calls.get(call.phone) is not call:
                        raise PhoneCallError("call_unavailable")
                except asyncio.CancelledError:
                    raise
                except PhoneCallError as exc:
                    await self._end(call, cause="bridge_error")
                    _settle(pending.future, error=exc)
                    return
                except Exception:
                    logger.warning("phone call tick failed", exc_info=True)
                    failure = PhoneCallError("call_unavailable")
                    await self._end(call, cause="bridge_failed")
                    _settle(pending.future, error=failure)
                    return

                response = {
                    "ok": True,
                    "id": call.id,
                    "n": n,
                    "audio": base64.b64encode(audio).decode(),
                    "dropped": call.dropped,
                }
                call.pending.pop(n, None)
                call.completed[n] = (pending.mic, response)
                while len(call.completed) > COMPLETED_TICKS:
                    call.completed.popitem(last=False)
                call.last_tick = time.monotonic()
                _settle(pending.future, result=response)
        finally:
            if call.publisher is current:
                call.publisher = None

    async def close(self, phone: str, session: object, call_id: str) -> None:
        openers = []
        for pending in list(self._pending):
            if pending.phone == phone and pending.call_id == call_id:
                if pending.session is not session:
                    raise PhoneCallError("call_forbidden")
                self._cancel_opening(pending, openers)
        await wait_capped(openers)
        call = self._calls.get(phone)
        if call is None or call.id != call_id:
            return  # idempotent for the owner
        if call.session is not session:
            raise PhoneCallError("call_forbidden")
        await self._end(call, cause="closed")

    async def invalidate(self, phone: str, cause: str = "invalidated") -> None:
        openers = []
        for pending in list(self._pending):
            if pending.phone == phone:
                self._cancel_opening(pending, openers)
        await wait_capped(openers)
        call = self._calls.get(phone)
        if call is not None:
            await self._end(call, cause=cause)

    async def shutdown(self) -> None:
        self._shutting_down = True
        openers = []
        for pending in list(self._pending):
            self._cancel_opening(pending, openers)
        await wait_capped(openers)
        for call in list(self._calls.values()):
            await self._end(call, cause="shutdown")
        # Every bridge close still running, including ones a cancel left behind. One capped
        # wait, no loop: a finished task never yields, so it would spin here, and its
        # discard callback may not have run yet, so prune done tasks ourselves.
        await wait_capped({t for t in self._closing if not t.done()})
        self._closing -= {t for t in self._closing if t.done()}

    def _cancel_opening(self, pending: _Opening, openers: list[asyncio.Task]) -> None:
        first_cancel = not pending.cancelled
        pending.cancelled = True
        if not pending.started:
            # A task cancelled before its first event-loop step never runs its
            # coroutine finally block, so release its reservation here.
            self._pending.discard(pending)
            self._release_voice(pending.phone, pending.voice_session_id)
            pending.cancel_waiters_on_abort = True
            _settle(pending.future, error=PhoneCallError("call_unavailable"))
        if pending.task is not None and pending.task is not asyncio.current_task():
            # The shared task owns bridge startup and cleanup; cancel it once so the
            # factory can reap any child spawned before its ready handshake.
            if first_cancel and not pending.task.cancelling():
                pending.task.cancel()
            openers.append(pending.task)

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
            if not call.pending and time.monotonic() - call.last_tick > IDLE_SECONDS:
                await self._end(call, cause="timeout", from_watchdog=True)
                return

    async def _end(self, call: _Call, *, cause: str, from_watchdog: bool = False) -> None:
        if call.ended:
            current = asyncio.current_task()
            tasks = {call.bridge_close} if call.bridge_close is not None else set()
            if call.publisher is not None and call.publisher is not current:
                tasks.add(call.publisher)
            await wait_capped(tasks)
            return
        call.ended = True
        if self._calls.get(call.phone) is call:
            del self._calls[call.phone]
        self._release_voice(call.phone, call.voice_session_id)
        for pending in call.pending.values():
            _settle(pending.future, error=PhoneCallError("call_unavailable"))
        call.pending.clear()
        call.out.clear()
        if call.watchdog and not from_watchdog:
            call.watchdog.cancel()
        publisher = call.publisher
        current = asyncio.current_task()
        if publisher is not None and publisher is not current and not publisher.done():
            publisher.cancel()
        if call.bridge_close is None:
            call.bridge_close = self._close_bridge(call.bridge)
        tasks = {call.bridge_close}
        if publisher is not None and publisher is not current:
            tasks.add(publisher)
        await wait_capped(tasks)
        logger.info("phone call %s ended: %s", call.id[:8], cause)

    def _close_bridge(self, bridge: PhoneBridge) -> asyncio.Task:
        """Start a bridge close that outlives a cancelled caller; shutdown awaits it."""
        task = asyncio.get_running_loop().create_task(_close_quietly(bridge))
        self._closing.add(task)
        task.add_done_callback(self._closing.discard)
        return task


async def wait_capped(tasks) -> bool:
    """Wait up to SHUTDOWN_CAP_S; return False if any cleanup task remains."""
    tasks = list(tasks)
    if not tasks:
        return True
    _, still_running = await asyncio.wait(tasks, timeout=SHUTDOWN_CAP_S)
    if still_running:
        logger.warning(
            "%d phone call task(s) still running after %.0f s cleanup cap",
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
