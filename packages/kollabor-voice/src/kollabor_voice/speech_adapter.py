"""Client side of the voice service for the LiveKit worker.

One status/retry mapping, one lease, KVA1 streams to the single model owner.
Nothing here loads Parakeet or VibeVoice.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from . import kva
from .audio_ipc import STT_RATE, audio_socket_path
from .client import VoiceClient
from .control import VoiceError

LEASE_BEAT_S = 1.0
OPEN_TIMEOUT_S = 10


class AdapterError(RuntimeError):
    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code, self.retryable = code, retryable


@dataclass(frozen=True)
class RoomBinding:
    owner_id: str
    voice_session_id: str
    generation: int


@dataclass(frozen=True)
class AudioFrame:
    sequence: int
    sample_rate_hz: int
    samples: object  # float32 numpy, mono


@dataclass(frozen=True)
class SpeechReadiness:
    state: str  # idle | starting | downloading | ready | error
    detail: str = ""
    model: str | None = None
    bytes_done: int | None = None
    bytes_total: int | None = None
    retryable: bool = False


@dataclass(frozen=True)
class SttEvent:
    kind: str  # speech_start | transcript
    text: str = ""
    language: str | None = None
    final: bool = True
    stt_ms: int | None = None


@dataclass
class SpeechStats:
    first_audio_ms: int | None = None
    total_ms: int | None = None


def map_readiness(status: dict) -> SpeechReadiness:
    """The single service-state to room-state mapping."""
    stt, tts = status.get("transcription") or {}, status.get("speech") or {}
    setup = status.get("state")
    # Download progress can sit on the setup record or on the STT engine.
    for source in (status, stt, tts):
        if source.get("state") == "downloading" or (
            source is not status and source.get("total") and source.get("downloaded")
        ):
            detail = str(source.get("detail", ""))
            return SpeechReadiness(
                "downloading",
                detail,
                model=detail.split(" / ")[0] or None,
                bytes_done=source.get("downloaded"),
                bytes_total=source.get("total"),
            )
    if setup in {"starting", "loading", "warming", "installing", "checking"}:
        return SpeechReadiness("starting", str(status.get("detail", "")))
    if setup == "error" or status.get("error"):
        return SpeechReadiness(
            "error",
            str(status.get("error") or status.get("detail", "")),
            retryable=True,
        )
    if stt.get("engine") != "parakeet":
        # Never claim Parakeet when Whisper would run.
        if stt.get("state") == "loading":
            return SpeechReadiness("starting", str(stt.get("detail", "")))
        return SpeechReadiness(
            "error",
            "Voice in the web UI transcribes with Parakeet v3. Choose it as the "
            "Transcription Engine in Settings > Voice, then turn voice on again.",
            retryable=True,
        )
    if stt.get("state") == "error" or tts.get("state") == "error":
        return SpeechReadiness(
            "error", str(stt.get("detail") or tts.get("detail", "")), retryable=True
        )
    if stt.get("state") == "ready" and tts.get("state") == "ready":
        return SpeechReadiness("ready")
    if setup in {"ready", "off", "listening"}:
        return SpeechReadiness("starting", "Preparing transcription and speech")
    return SpeechReadiness("idle")


def _raise(frame_body: dict):
    raise AdapterError(
        frame_body.get("code", "provider_error"),
        frame_body.get("message", "Audio stream failed"),
        retryable=frame_body.get("code") in {"models_not_ready", "backpressure"},
    )


@dataclass
class SpeechAdapter:
    client: VoiceClient = field(default_factory=VoiceClient)
    lease: dict | None = None
    streams: dict = field(default_factory=dict)
    beat: asyncio.Task | None = None
    stats: SpeechStats = field(default_factory=SpeechStats)

    # -- readiness ---------------------------------------------------------

    async def readiness(self, owner_id: str = "") -> SpeechReadiness:
        try:
            return map_readiness(await self.client.call("status"))
        except (VoiceError, OSError, TimeoutError, ValueError) as exc:
            return SpeechReadiness(
                "error", f"Voice service unavailable: {exc}", retryable=True
            )

    async def retry(self, owner_id: str = "") -> SpeechReadiness:
        try:
            return map_readiness(await self.client.call("retry"))
        except (VoiceError, OSError, TimeoutError, ValueError) as exc:
            return SpeechReadiness(
                "error", f"Voice retry failed: {exc}", retryable=True
            )

    # -- lease: a room holds the service lease but never opens the local mic ---

    async def acquire(self, voice_session_id: str) -> None:
        claim = uuid.uuid4().hex
        result = await self.client.call(
            "claim",
            client_id=f"livekit:{voice_session_id}",
            claim_id=claim,
            activated_at=time.time(),
            capture=False,
        )
        self.lease = {"token": result["token"], "epoch": result["epoch"]}
        self.beat = asyncio.create_task(self._heartbeat(), name="voice-room-lease")

    async def _heartbeat(self):
        while True:
            await asyncio.sleep(LEASE_BEAT_S)
            with contextlib.suppress(VoiceError, OSError, TimeoutError):
                await self.client.call("heartbeat", **self.lease)

    async def release(self) -> None:
        if self.beat:
            self.beat.cancel()
            await asyncio.gather(self.beat, return_exceptions=True)
            self.beat = None
        if self.lease:
            with contextlib.suppress(VoiceError, OSError, TimeoutError):
                await self.client.call("release", **self.lease)
            self.lease = None

    async def fence(self, binding: RoomBinding) -> None:
        """Stop first: every older stream for this room ends before teardown."""
        if self.lease:
            with contextlib.suppress(VoiceError, OSError, TimeoutError):
                await self.client.call(
                    "audio_stop",
                    voice_session_id=binding.voice_session_id,
                    generation=binding.generation,
                    **self.lease,
                )

    # -- streams -----------------------------------------------------------

    async def _open(self, binding: RoomBinding, direction: str):
        if not self.lease:
            raise AdapterError("models_not_ready", "Voice lease is not held", True)
        try:
            grant = await self.client.call(
                "audio_capability",
                owner_id=binding.owner_id,
                voice_session_id=binding.voice_session_id,
                generation=binding.generation,
                direction=direction,
                **self.lease,
            )
        except VoiceError as exc:
            raise AdapterError(exc.code, str(exc)) from exc
        reader, writer = await asyncio.open_unix_connection(
            str(audio_socket_path(self.client.root))
        )
        stream_id = uuid.uuid4().hex
        writer.write(
            kva.control(
                kva.OPEN,
                0,
                stream_id=stream_id,
                owner_id=binding.owner_id,
                voice_session_id=binding.voice_session_id,
                generation=binding.generation,
                service_lease_epoch=self.lease["epoch"],
                direction=direction,
                one_time_capability=grant["capability"],
            )
        )
        await writer.drain()
        frame = await asyncio.wait_for(kva.read_frame(reader), OPEN_TIMEOUT_S)
        body = kva.parse_control(frame[3]) if frame else {}
        if not frame or frame[0] == kva.ERROR:
            writer.close()
            _raise(body)
        self.streams[stream_id] = writer
        return stream_id, reader, writer

    async def cancel(self, binding: RoomBinding, stream_id: str | None = None) -> None:
        for key in [stream_id] if stream_id else list(self.streams):
            writer = self.streams.pop(key, None)
            if writer:
                with contextlib.suppress(Exception):
                    writer.write(kva.control(kva.CANCEL, 0))
                    writer.close()

    async def transcribe(
        self, binding: RoomBinding, frames: AsyncIterator[AudioFrame]
    ) -> AsyncIterator[SttEvent]:
        stream_id, reader, writer = await self._open(binding, "stt_input")

        async def send():
            sequence = 0
            step = int(STT_RATE * kva.MAX_FRAME_SECONDS)
            async for frame in frames:
                if frame.sample_rate_hz != STT_RATE:
                    raise AdapterError("invalid_audio", "STT input must be 16 kHz mono")
                for start in range(0, len(frame.samples), step):
                    sequence += 1
                    writer.write(
                        kva.encode(
                            kva.PCM,
                            sequence,
                            kva.pcm_to_bytes(frame.samples[start : start + step]),
                        )
                    )
                    await writer.drain()
            writer.write(kva.control(kva.END, 0))
            await writer.drain()

        sender = asyncio.create_task(send())
        try:
            while True:
                if sender.done() and sender.exception():
                    raise sender.exception()
                frame = await kva.read_frame(reader)
                if frame is None:
                    return
                body = (
                    kva.parse_control(frame[3]) if frame[0] in kva.CONTROL_KINDS else {}
                )
                if frame[0] == kva.ERROR:
                    _raise(body)
                event = body.get("event")
                if event == "speech_start":
                    yield SttEvent("speech_start")
                elif event == "transcript":
                    yield SttEvent(
                        "transcript",
                        body["text"],
                        body.get("language"),
                        bool(body.get("final", True)),
                        body.get("stt_ms"),
                    )
                elif event == "closed":
                    return
        finally:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)
            self.streams.pop(stream_id, None)
            writer.close()

    async def synthesize(
        self, binding: RoomBinding, text: str, cancel: asyncio.Event
    ) -> AsyncIterator[AudioFrame]:
        self.stats = SpeechStats()
        stream_id, reader, writer = await self._open(binding, "tts_output")
        writer.write(kva.control(kva.SPEAK, 0, text=text))
        await writer.drain()
        rate = None
        try:
            while not cancel.is_set():
                read = asyncio.create_task(kva.read_frame(reader))
                stop = asyncio.create_task(cancel.wait())
                await asyncio.wait({read, stop}, return_when=asyncio.FIRST_COMPLETED)
                stop.cancel()
                if not read.done():
                    read.cancel()
                    break
                frame = read.result()
                if frame is None:
                    break
                kind, _, sequence, payload = frame
                if kind == kva.PCM:
                    if rate is None:
                        raise AdapterError("invalid_audio", "PCM before format")
                    yield AudioFrame(sequence, rate, kva.pcm_from_bytes(payload))
                    continue
                body = kva.parse_control(payload)
                if kind == kva.ERROR:
                    _raise(body)
                if body.get("event") == "format":
                    rate = int(body["sample_rate_hz"])
                elif body.get("event") == "done":
                    self.stats = SpeechStats(
                        body.get("first_audio_ms"), body.get("total_ms")
                    )
                    break
        finally:
            if cancel.is_set():
                await self.cancel(binding, stream_id)
            self.streams.pop(stream_id, None)
            writer.close()
