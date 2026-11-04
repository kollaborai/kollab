"""LiveKit media bridge for phone calls (engine <-> room over stdio pipes).

The Engine runs this module with the prepared voice interpreter and Kollab's
exact ``kollabor_voice`` package path; argv never carries a token. stdin lines
are JSON:
``{"type": "start", "url": ..., "token": ..., "rate_in": 16000,
"rate_out": 24000}`` exactly once, then ``{"type": "mic", "n": int,
"pcm": base64}`` and ``{"type": "close"}``. stdout lines are
``{"type": "ready"}``, ``{"type": "audio", "pcm": base64}`` (chunks of at
most 9600 bytes), ``{"type": "audio_dropped", "bytes": int}`` (cumulative
deltas for frames this child dropped from its outbound queue), or
``{"type": "error", "code": str}``. stderr carries diagnostics only. Every
line is bounded at 32 KiB. Outbound audio queues at 3 frames: a slow reader
drops the oldest frame, logs a diagnostic, and reports the dropped bytes.

The bridge joins one room with the supplied token, publishes a 16 kHz mono
``AudioSource`` for mic audio, subscribes ONLY to the voice worker's track
(identity prefix ``worker-``) and republishes it as 24 kHz mono PCM16
chunks. No sound device is ever opened.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import inspect
import json
import os
import sys
from typing import Any

MAX_LINE_BYTES = 32 * 1024
MAX_AUDIO_CHUNK_BYTES = 9600
FRAME_MS = 20
OUT_QUEUE_FRAMES = 3
WORKER_IDENTITY_PREFIX = "worker-"
READY_TIMEOUT_S = 15.0


def _dbg(msg: str) -> None:
    if os.environ.get("KOLLAB_BRIDGE_DEBUG"):
        print("phone-audio-bridge: " + msg, file=sys.stderr, flush=True)


def _new_room() -> Any:
    import livekit.rtc as rtc

    return rtc.Room()


def _room_options() -> Any:
    import livekit.rtc as rtc

    return rtc.RoomOptions(auto_subscribe=False, dynacast=False)


def _audio_source(rate_in: int, channels: int = 1) -> Any:
    import livekit.rtc as rtc

    return rtc.AudioSource(rate_in, channels)


def _track_stream(track: Any, sample_rate: int, capacity: int) -> Any:
    import livekit.rtc as rtc

    return rtc.AudioStream(
        track, sample_rate=sample_rate, num_channels=1, capacity=capacity
    )


def _new_frame(pcm: bytes, sample_rate: int, samples_per_channel: int) -> Any:
    import livekit.rtc as rtc

    return rtc.AudioFrame(pcm, sample_rate, 1, samples_per_channel)


def _local_audio_track(name: str, source: Any) -> Any:
    import livekit.rtc as rtc

    return rtc.LocalAudioTrack.create_audio_track(name, source)


def _mic_publish_options() -> Any:
    import livekit.rtc as rtc

    # RoomIO's speech input selects SOURCE_MICROPHONE. The SDK's default is
    # SOURCE_UNKNOWN, which lets a room play replies while ignoring this mic.
    return rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE)


class _Outbound:
    """Bounded audio queue: at most ``limit`` items, drop-oldest on overflow."""

    def __init__(self, limit: int) -> None:
        self._items: list[bytes] = []
        self._limit = limit
        self.dropped = 0
        # Bytes of dropped-oldest items, reported to the engine over IPC so
        # the phone shows EVERY dropped sample, not only the handle's own.
        self.dropped_bytes = 0

    def put(self, item: bytes) -> None:
        if len(self._items) >= self._limit:
            oldest = self._items.pop(0)
            self.dropped += 1
            self.dropped_bytes += len(oldest)
            print(
                f"phone-audio-bridge: dropped {self.dropped} queued audio frames",
                file=sys.stderr,
            )
        self._items.append(item)

    def drain(self) -> list[bytes]:
        items, self._items = self._items, []
        return items


class _BridgeSession:
    def __init__(self, reader: Any, writer: Any) -> None:
        self._reader = reader
        self._writer = writer
        self._room: Any = None
        self._source: Any = None
        self._pump: asyncio.Task[None] | None = None
        self.out = _Outbound(OUT_QUEUE_FRAMES)
        self._drops_reported = 0
        self._mic_leftover = b""
        self._rate_in = 0
        self._started = False
        self._closing = False

    async def _emit(self, payload: dict[str, Any]) -> None:
        line = json.dumps(payload, separators=(",", ":"))
        if len(line.encode()) > MAX_LINE_BYTES:  # pragma: no cover - sized by contract
            raise ValueError("bridge_line_too_large")
        self._writer.write(line + "\n")
        flush = getattr(self._writer, "flush", None)
        if flush is not None:
            try:
                flush()
            except Exception:
                pass

    async def run(self) -> int:
        code = 0
        try:
            while True:
                raw = await self._readline()
                if raw is None:
                    break
                if len(raw) > MAX_LINE_BYTES:
                    await self._emit_error("bridge_bad_line")
                    code = 1
                    break
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    await self._emit_error("bridge_bad_line")
                    code = 1
                    break
                kind = message.get("type")
                if kind == "start":
                    if self._started:
                        await self._emit_error("bridge_already_started")
                        code = 1
                        break
                    stop = await self._start(message)
                    if stop:
                        code = 1
                        break
                elif kind == "mic":
                    if not self._started:
                        await self._emit_error("bridge_mic_before_start")
                        code = 1
                        break
                    if not await self._mic(message):
                        code = 1
                        break
                elif kind == "close":
                    break
                else:
                    await self._emit_error("bridge_bad_line")
                    code = 1
                    break
        finally:
            await self._teardown()
        return code

    async def _readline(self) -> str | None:
        loop = asyncio.get_running_loop()
        read = getattr(self._reader, "readline", None)
        if read is None:
            read = self._reader.readline  # type: ignore[attr-defined]
        if inspect.iscoroutinefunction(read):
            raw = await read()
        else:
            raw = await loop.run_in_executor(None, read)
        if raw in (b"", None):
            return None
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return str(raw)

    async def _emit_error(self, code: str) -> None:
        await self._emit({"type": "error", "code": code})

    async def _start(self, message: dict[str, Any]) -> bool:
        url = message.get("url")
        token = message.get("token")
        self._rate_in = int(message.get("rate_in", 16000))
        if not isinstance(url, str) or not isinstance(token, str):
            await self._emit_error("bridge_bad_start")
            return True
        room = _new_room()

        def is_worker(participant: Any) -> bool:
            identity = str(getattr(participant, "identity", "") or "")
            return identity.startswith(WORKER_IDENTITY_PREFIX)

        def subscribe_publication(publication: Any) -> None:
            subscribe = getattr(publication, "set_subscribed", None)
            if subscribe is None:
                return
            try:
                result = subscribe(True)
                _dbg("subscribe requested on %r" % (getattr(publication, "sid", ""),))
            except Exception as exc:
                _dbg("subscribe failed: %r" % exc)
                return
            if asyncio.iscoroutine(result):
                asyncio.ensure_future(result)

        def on_track_subscribed(track: Any, _publication: Any, participant: Any) -> None:
            identity = str(getattr(participant, "identity", "") or "")
            _dbg("track_subscribed from %r" % identity)
            if not identity.startswith(WORKER_IDENTITY_PREFIX):
                return
            old_pump = self._pump
            if old_pump is not None and not old_pump.done():
                # A re-publish/reconnect window can subscribe a second worker
                # track; orphaning the first pump would feed both into the
                # same queue (duplicated audio + leaked task).
                old_pump.cancel()
            self._pump = asyncio.ensure_future(self._pump_worker(track))

        def on_track_published(publication: Any, participant: Any) -> None:
            _dbg("track_published from %r" % getattr(participant, "identity", ""))
            if is_worker(participant):
                subscribe_publication(publication)

        def on_participant_connected(participant: Any) -> None:
            _dbg("peer joined %r" % getattr(participant, "identity", ""))
            if not is_worker(participant):
                return
            publications = getattr(participant, "track_publications", {}) or {}
            for publication in list(publications.values()):
                subscribe_publication(publication)

        room.on("track_subscribed", on_track_subscribed)
        room.on("track_published", on_track_published)
        room.on("participant_connected", on_participant_connected)
        try:
            await room.connect(url, token, _room_options())
            for participant in list(
                (getattr(room, "remote_participants", {}) or {}).values()
            ):
                on_participant_connected(participant)
            self._source = _audio_source(self._rate_in)
            track = _local_audio_track("kollab-mic", self._source)
            await room.local_participant.publish_track(track, _mic_publish_options())
        except Exception as exc:
            print(f"phone-audio-bridge: connect failed: {exc!r}", file=sys.stderr)
            await self._emit({"type": "error", "code": "bridge_connect"})
            return True
        self._room = room
        self._started = True
        await self._emit({"type": "ready"})
        return False

    async def _report_dropped_audio(self) -> None:
        """Emit the delta of the outbound queue's dropped bytes over IPC.

        The baseline lives on the session, not the pump worker: the pump can
        be replaced mid-session (track re-subscription) and a fresh worker
        must not re-report drops an earlier pump already sent.
        """
        total = self.out.dropped_bytes
        if total > self._drops_reported:
            await self._emit(
                {"type": "audio_dropped", "bytes": total - self._drops_reported}
            )
            self._drops_reported = total

    async def _pump_worker(self, track: Any) -> None:
        stream = _track_stream(track, 24000, OUT_QUEUE_FRAMES)
        buffer = b""
        try:
            async for event in stream:
                buffer += bytes(event.frame.data)
                while len(buffer) >= MAX_AUDIO_CHUNK_BYTES:
                    self.out.put(buffer[:MAX_AUDIO_CHUNK_BYTES])
                    buffer = buffer[MAX_AUDIO_CHUNK_BYTES:]
                await self._report_dropped_audio()
        except Exception as exc:
            print(f"phone-audio-bridge: pump failed: {exc!r}", file=sys.stderr)
            await self._emit_error("bridge_audio")
            return
        if buffer:
            self.out.put(buffer)
            await self._report_dropped_audio()

    async def _mic(self, message: dict[str, Any]) -> bool:
        try:
            pcm = base64.b64decode(str(message.get("pcm", "")), validate=True)
        except (binascii.Error, ValueError):
            await self._emit({"type": "error", "code": "bridge_pcm"})
            return False
        samples_per_frame = self._rate_in * FRAME_MS // 1000
        width = 2  # PCM16 mono
        chunk = samples_per_frame * width
        data = self._mic_leftover + pcm
        offset = 0
        while offset + chunk <= len(data):
            frame = _new_frame(data[offset : offset + chunk], self._rate_in, samples_per_frame)
            result = self._source.capture_frame(frame)
            if inspect.isawaitable(result):
                await result
            offset += chunk
        self._mic_leftover = data[offset:]
        return True

    async def _teardown(self) -> None:
        self._closing = True
        if self._pump is not None:
            self._pump.cancel()
        if self._room is not None:
            try:
                await self._room.disconnect()
            except Exception:
                pass
        flush = getattr(self._writer, "flush", None)
        if flush is not None:
            try:
                flush()
            except Exception:
                pass


async def run_bridge(reader: Any, writer: Any) -> int:
    """Speak the line protocol on ``reader``/``writer`` until EOF or close."""
    session = _BridgeSession(reader, writer)

    async def flush_outbound() -> None:
        while True:
            pending = session.out.drain()
            for chunk in pending:
                await session._emit({"type": "audio", "pcm": base64.b64encode(chunk).decode()})
            await asyncio.sleep(0.01)

    flusher = asyncio.ensure_future(flush_outbound())
    try:
        return await session.run()
    finally:
        flusher.cancel()


def main() -> None:
    """Entry point: stdin/stdout pipes from the engine."""
    out = sys.stdout
    writer = _Writer(out)
    exit_code = asyncio.run(run_bridge(sys.stdin, writer))
    raise SystemExit(exit_code)


class _Writer:
    def __init__(self, stream: Any) -> None:
        self._stream = stream

    def write(self, text: str) -> None:
        self._stream.write(text)

    def flush(self) -> None:
        self._stream.flush()


if __name__ == "__main__":
    main()
