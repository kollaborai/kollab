"""LiveKit speech worker: one process per voice session.

Run by the Engine as `<voice runtime python> -m kollabor_voice.livekit_worker`
with KOLLAB_VOICE_WORKER_CONFIG (JSON: livekit_url, worker_token, room,
voice_session_id, generation, engine_url, engine_token). It joins the room,
sends the browser mic to the service's Parakeet owner, posts final transcripts
and events to the Engine, and plays assistant speech (VibeVoice) back as an
audio track. It never calls an LLM or a tool.

Control server (127.0.0.1, bearer engine_token): POST /speak, POST /stop.
Stdout carries one line, `WORKER_READY <control_url>`, once joined.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import os
import time
import urllib.request
import uuid
from dataclasses import dataclass

from .speech_adapter import (
    AdapterError,
    AudioFrame,
    RoomBinding,
    SpeechAdapter,
)

log = logging.getLogger(__name__)

STT_RATE = 16000
STT_BATCH = 1600  # 100 ms, the KVA1 frame ceiling
PLAY_RATE = 24000
MAX_BODY = 32 * 1024
READINESS_POLL_S = 1.0
READY_TIMEOUT_S = 1200


@dataclass(frozen=True)
class WorkerConfig:
    livekit_url: str
    worker_token: str
    room: str
    voice_session_id: str
    generation: int
    engine_url: str
    engine_token: str

    @classmethod
    def from_env(cls, env=os.environ) -> "WorkerConfig":
        raw = json.loads(env["KOLLAB_VOICE_WORKER_CONFIG"])
        return cls(**{k: raw[k] for k in cls.__dataclass_fields__})


def resample(samples, source: int, target: int):
    import numpy as np

    if source == target or len(samples) == 0:
        return samples
    count = max(1, round(len(samples) * target / source))
    return np.interp(
        np.linspace(0, len(samples) - 1, count), np.arange(len(samples)), samples
    ).astype(np.float32)


async def rebatch(frames, size: int = STT_BATCH):
    """Group room frames into 100 ms STT frames."""
    import numpy as np

    pending, sequence = np.empty(0, dtype=np.float32), 0
    async for frame in frames:
        pending = np.concatenate((pending, frame.samples))
        while len(pending) >= size:
            sequence += 1
            yield AudioFrame(sequence, STT_RATE, pending[:size])
            pending = pending[size:]
    if len(pending):
        yield AudioFrame(sequence + 1, STT_RATE, pending)


class EnginePoster:
    """Bearer HTTP posts to the Engine; failures are logged, never raised."""

    def __init__(self, config: WorkerConfig):
        self.base = config.engine_url.rstrip("/")
        self.session = config.voice_session_id
        self.headers = {
            "Authorization": f"Bearer {config.engine_token}",
            "Content-Type": "application/json",
        }

    def _post(self, path: str, body: dict) -> bool:
        request = urllib.request.Request(
            f"{self.base}/voice/sessions/{self.session}/{path}",
            json.dumps(body).encode(),
            self.headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=5):
                return True
        except Exception as exc:  # status only: the URL and headers hold secrets
            log.warning("Engine post %s failed: %s", path, type(exc).__name__)
            return False

    async def post(self, path: str, body: dict) -> bool:
        return await asyncio.to_thread(self._post, path, body)


class LiveKitRoom:
    """The only code that touches the `livekit` package."""

    def __init__(self):
        from livekit import rtc

        self.rtc = rtc
        self.room = rtc.Room()
        self.source = None
        self.mic = asyncio.Future()
        self.closed = asyncio.Event()

    async def connect(self, url: str, token: str) -> None:
        rtc = self.rtc

        @self.room.on("track_subscribed")
        def subscribed(track, publication, participant):
            if track.kind == rtc.TrackKind.KIND_AUDIO and not self.mic.done():
                self.mic.set_result(track)

        @self.room.on("disconnected")
        def disconnected(*_):
            self.closed.set()

        await self.room.connect(url, token, rtc.RoomOptions(auto_subscribe=True))
        self.source = rtc.AudioSource(PLAY_RATE, 1)
        track = rtc.LocalAudioTrack.create_audio_track("kollab-voice", self.source)
        await self.room.local_participant.publish_track(
            track,
            rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE),
        )

    async def mic_frames(self):
        import numpy as np

        track = await self.mic
        stream = self.rtc.AudioStream(track, sample_rate=STT_RATE, num_channels=1)
        sequence = 0
        async for event in stream:
            pcm = np.frombuffer(event.frame.data, dtype=np.int16)
            sequence += 1
            yield AudioFrame(sequence, STT_RATE, pcm.astype(np.float32) / 32768.0)

    async def play(self, frame: AudioFrame) -> None:
        import numpy as np

        audio = resample(frame.samples, frame.sample_rate_hz, PLAY_RATE)
        pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
        step = PLAY_RATE // 100  # 10 ms
        for start in range(0, len(pcm), step):
            chunk = pcm[start : start + step]
            await self.source.capture_frame(
                self.rtc.AudioFrame(chunk.tobytes(), PLAY_RATE, 1, len(chunk))
            )

    async def flush(self) -> None:
        if self.source:
            self.source.clear_queue()

    async def close(self) -> None:
        with contextlib.suppress(Exception):
            if self.source:
                await self.source.aclose()
            await self.room.disconnect()


class Worker:
    def __init__(self, config: WorkerConfig, room, adapter: SpeechAdapter, poster):
        self.config, self.room, self.adapter, self.poster = (
            config,
            room,
            adapter,
            poster,
        )
        self.generation = config.generation
        self.binding = RoomBinding(
            config.voice_session_id, config.voice_session_id, config.generation
        )
        self.stopping = asyncio.Event()
        self.speech: asyncio.Queue = asyncio.Queue()
        self.playing: asyncio.Event | None = None  # set -> cancel current playback
        self.server = None
        self.tasks: list[asyncio.Task] = []

    # -- events -------------------------------------------------------------

    async def event(self, event: str, payload: dict, turn_id: str | None = None):
        await self.poster.post(
            "events",
            {
                "event": event,
                "generation": self.generation,
                "turn_id": turn_id,
                "payload": payload,
            },
        )

    async def state(self, state: str, detail: str = "", **extra):
        await self.event("voice.state", {"state": state, "detail": detail, **extra})

    async def metric(self, stage: str, ms, turn_id: str | None = None):
        if ms is not None:
            await self.event(
                "voice.metrics", {"stage": stage, "ms": round(ms)}, turn_id
            )

    # -- startup ------------------------------------------------------------

    async def wait_ready(self) -> None:
        """Mirror the service's one readiness snapshot until models are ready."""
        last = None
        deadline = time.monotonic() + READY_TIMEOUT_S
        while time.monotonic() < deadline and not self.stopping.is_set():
            ready = await self.adapter.readiness(self.binding.owner_id)
            if ready.state == "ready":
                return
            if ready.state == "error":
                await self.event(
                    "voice.error",
                    {
                        "code": "models_not_ready",
                        "message": ready.detail,
                        "retryable": ready.retryable,
                    },
                )
                raise AdapterError("models_not_ready", ready.detail, ready.retryable)
            key = (ready.state, ready.bytes_done)
            if key != last:
                last = key
                if ready.state == "downloading":
                    await self.state(
                        "downloading",
                        ready.detail,
                        model=ready.model,
                        bytes_done=ready.bytes_done,
                        bytes_total=ready.bytes_total,
                    )
                else:
                    await self.state("starting", ready.detail)
            await asyncio.sleep(READINESS_POLL_S)
        raise AdapterError(
            "models_not_ready", "Voice models did not become ready", True
        )

    async def run(self) -> None:
        cfg = self.config
        await self.state("starting", "Preparing voice models")
        await self.wait_ready()
        await self.adapter.acquire(cfg.voice_session_id)
        await self.room.connect(cfg.livekit_url, cfg.worker_token)
        self.server = await asyncio.start_server(self.http, "127.0.0.1", 0)
        port = self.server.sockets[0].getsockname()[1]
        self.tasks = [
            asyncio.create_task(self.listen(), name="room-listen"),
            asyncio.create_task(self.speaker(), name="room-speak"),
        ]
        print(f"WORKER_READY http://127.0.0.1:{port}", flush=True)
        await self.state("listening")
        closed = getattr(self.room, "closed", None)
        waits = [asyncio.create_task(self.stopping.wait())]
        if closed is not None:
            waits.append(asyncio.create_task(closed.wait()))
        waits += self.tasks
        await asyncio.wait(waits, return_when=asyncio.FIRST_COMPLETED)
        for task in waits:
            task.cancel()
        await asyncio.gather(*waits, return_exceptions=True)
        failed = [
            t for t in self.tasks if t.done() and not t.cancelled() and t.exception()
        ]
        await self.shutdown()
        if failed:
            raise failed[0].exception()

    # -- mic -> STT -> engine ---------------------------------------------------

    async def listen(self) -> None:
        frames = rebatch(self.room.mic_frames())
        try:
            async for event in self.adapter.transcribe(self.binding, frames):
                if event.kind == "speech_start":
                    await self.state("hearing")
                    await self.barge_in()
                elif event.text:
                    await self.final_transcript(event)
        except AdapterError as exc:
            if self.stopping.is_set():
                return
            await self.event(
                "voice.error",
                {"code": exc.code, "message": str(exc), "retryable": exc.retryable},
            )
            raise

    async def final_transcript(self, event) -> None:
        turn_id = uuid.uuid4().hex
        await self.metric("stt", event.stt_ms, turn_id)
        await self.poster.post(
            "transcripts",
            {
                "transcript_id": uuid.uuid4().hex,
                "voice_session_id": self.config.voice_session_id,
                "turn_id": turn_id,
                "generation": self.generation,
                "speaker": "user",
                "revision": 1,
                "final": True,
                "display_text": event.text,
                "spoken_text": None,
                "language": event.language,
                "observed_at_utc_ms": int(time.time() * 1000),
            },
        )
        await self.state("thinking", turn_id=turn_id)

    async def barge_in(self) -> None:
        """User speech over playback: stop it now, drop whatever is queued."""
        if self.playing is None and self.speech.empty():
            return
        while not self.speech.empty():
            self.speech.get_nowait()
        if self.playing is not None:
            self.playing.set()
        await self.room.flush()
        await self.event("voice.state", {"state": "listening", "barge_in": True})

    # -- engine -> TTS -> room ----------------------------------------------------

    async def speaker(self) -> None:
        while True:
            turn_id, text, received = await self.speech.get()
            cancel = self.playing = asyncio.Event()
            try:
                await self.state("speaking", turn_id=turn_id)
                first = None
                async for frame in self.adapter.synthesize(self.binding, text, cancel):
                    if cancel.is_set():
                        break
                    if first is None:
                        first = (time.monotonic() - received) * 1000
                        await self.metric("first_audio", first, turn_id)
                    await self.room.play(frame)
                if not cancel.is_set():
                    await self.metric(
                        "speech_total", (time.monotonic() - received) * 1000, turn_id
                    )
                    await self.state("listening")
            except AdapterError as exc:
                # TTS failure changes state only; the display transcript stays.
                await self.event(
                    "voice.error",
                    {"code": exc.code, "message": str(exc), "retryable": exc.retryable},
                    turn_id,
                )
                await self.state("listening")
            finally:
                self.playing = None

    # -- control server ---------------------------------------------------------

    async def http(self, reader, writer) -> None:
        status, body = 400, {"code": "invalid_request"}
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
            lines = head.decode("latin-1").split("\r\n")
            method, path, *_ = lines[0].split(" ")
            headers = {
                k.strip().lower(): v.strip()
                for k, v in (line.split(":", 1) for line in lines[1:] if ":" in line)
            }
            length = int(headers.get("content-length", "0"))
            if length > MAX_BODY:
                status, body = 413, {"code": "transcript_too_large"}
            else:
                raw = await asyncio.wait_for(reader.readexactly(length), 5)
                token = headers.get("authorization", "")
                if not hmac.compare_digest(token, f"Bearer {self.config.engine_token}"):
                    status, body = 401, {"code": "unauthenticated"}
                else:
                    status, body = await self.route(
                        method, path, json.loads(raw or b"{}")
                    )
        except Exception:
            log.warning("Control request failed", exc_info=False)
        data = json.dumps(body).encode()
        writer.write(
            f"HTTP/1.1 {status} X\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(data)}\r\nConnection: close\r\n\r\n".encode() + data
        )
        with contextlib.suppress(Exception):
            await writer.drain()
        writer.close()

    async def route(self, method: str, path: str, body: dict):
        if method != "POST" or path not in {"/speak", "/stop"}:
            return 404, {"code": "not_found"}
        if body.get("generation", self.generation) != self.generation:
            return 409, {"code": "stale_generation"}
        if path == "/stop":
            # Fence first: audio from this generation never plays after this.
            self.generation += 1
            self.binding = RoomBinding(
                self.binding.owner_id, self.binding.voice_session_id, self.generation
            )
            await self.adapter.fence(self.binding)
            while not self.speech.empty():
                self.speech.get_nowait()
            if self.playing is not None:
                self.playing.set()
            self.stopping.set()
            return 200, {"stopping": True}
        text = body.get("spoken_text")
        if not isinstance(text, str) or not text.strip():
            return 422, {"code": "invalid_request", "message": "spoken_text required"}
        self.speech.put_nowait((str(body.get("turn_id") or ""), text, time.monotonic()))
        return 202, {"queued": True}

    async def shutdown(self) -> None:
        if self.server:
            self.server.close()
        await self.adapter.cancel(self.binding)
        await self.adapter.release()
        await self.room.close()


async def amain() -> None:
    logging.basicConfig(level=logging.INFO)
    config = WorkerConfig.from_env()
    worker = Worker(config, LiveKitRoom(), SpeechAdapter(), EnginePoster(config))
    await worker.run()


def main() -> None:
    try:
        asyncio.run(amain())
    except AdapterError:
        raise SystemExit(2)  # already reported to the Engine as voice.error


if __name__ == "__main__":
    main()
