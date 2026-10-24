"""LiveKit worker, KVA1 and the service audio socket, with no network or audio device."""

import asyncio
import json
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from kollabor_voice import kva
from kollabor_voice.control import VoiceError
from kollabor_voice.livekit_worker import (
    EnginePoster,
    Worker,
    WorkerConfig,
    rebatch,
    resample,
)
from kollabor_voice.service import VoiceService
from kollabor_voice.speech_adapter import (
    AdapterError,
    AudioFrame,
    RoomBinding,
    SpeechAdapter,
    SpeechReadiness,
    SttEvent,
    map_readiness,
)

np = pytest.importorskip("numpy")  # the voice runtime has it; the host venv may not

BINDING = RoomBinding("owner", "room-1", 1)


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 10))


# -- KVA1 -----------------------------------------------------------------


def test_kva_round_trip_and_limits():
    samples = np.linspace(-1, 1, 1600, dtype=np.float32)
    frame = kva.encode(kva.PCM, 7, kva.pcm_to_bytes(samples), kva.FLAG_FRAME_END)
    assert frame[:4] == b"KVA1" and len(frame) == 20 + 6400

    async def read(data):
        reader = asyncio.StreamReader()
        reader.feed_data(data)
        reader.feed_eof()
        return await kva.read_frame(reader)

    kind, flags, seq, payload = run(read(frame))
    assert (kind, flags, seq) == (kva.PCM, 1, 7)
    assert np.array_equal(kva.pcm_from_bytes(payload), samples)
    assert run(read(b"")) is None
    with pytest.raises(kva.FrameError):
        run(read(b"XXXX" + frame[4:]))
    with pytest.raises(kva.FrameError):
        kva.pcm_to_bytes(np.array([2.0], dtype=np.float32))
    with pytest.raises(kva.FrameError):
        kva.pcm_to_bytes(np.array([np.nan], dtype=np.float32))
    assert all(
        len(p) <= 100 * 24 * 4
        for p in kva.split_pcm(np.zeros(24000, np.float32), 24000)
    )


# -- readiness mapping -------------------------------------------------------


def test_readiness_maps_download_progress_and_refuses_whisper():
    ready = {
        "state": "off",
        "transcription": {"state": "ready", "engine": "parakeet"},
        "speech": {"state": "ready", "engine": "vibevoice"},
    }
    assert map_readiness(ready).state == "ready"
    whisper = {**ready, "transcription": {"state": "ready", "engine": "whisper"}}
    assert map_readiness(whisper).state == "error"  # never claim Parakeet
    downloading = {
        "state": "downloading",
        "detail": "parakeet / model.safetensors",
        "downloaded": 5,
        "total": 20,
    }
    got = map_readiness(downloading)
    assert (got.state, got.model, got.bytes_done, got.bytes_total) == (
        "downloading",
        "parakeet",
        5,
        20,
    )
    assert map_readiness({"state": "loading"}).state == "starting"
    assert map_readiness({"state": "error", "detail": "boom"}).retryable


# -- service audio socket -------------------------------------------------------


class FakeSegmenter:
    """Emits one finished segment per 3 frames, active between."""

    def __init__(self):
        self.count, self.active = 0, False

    def feed(self, audio, started):
        self.count += 1
        self.active = self.count % 3 != 0
        if self.count % 3 == 0:
            return [SimpleNamespace(audio=audio, final=True)]
        return []

    def flush(self):
        return None


class FakeStt:
    def __init__(self, engine="parakeet"):
        self.engine = engine

    def status(self):
        return {"state": "ready", "engine": self.engine, "detail": "x"}

    def transcribe(self, audio, names=()):
        return "hello room", None


class FakeSpeech:
    def __init__(self, chunks=3):
        self.chunks, self.closed = chunks, False

    def status(self):
        return {"state": "ready", "engine": "vibevoice"}

    def stream(self, text):
        try:
            for _ in range(self.chunks):
                yield np.full(4800, 0.1, dtype=np.float32), 24000
        finally:
            self.closed = True


class DirectClient:
    """VoiceClient stand-in: same ops, no control socket."""

    def __init__(self, service):
        self.service, self.root = service, service.root

    async def call(self, op, **params):
        try:
            return await self.service.dispatch({"op": op, **params})
        except Exception as exc:  # the socket reports errors as VoiceError
            raise VoiceError(getattr(exc, "code", "service_error"), str(exc)) from exc


@pytest.fixture
def service():
    root = Path(tempfile.mkdtemp(dir="/tmp", prefix="kv"))
    svc = VoiceService(root)
    svc.stt, svc.speech = FakeStt(), FakeSpeech()
    svc.segmenter_factory = FakeSegmenter
    svc.setup = {"state": "ready"}
    svc.transcript = SimpleNamespace(probe=lambda: None, seq=0, path="t.jsonl")
    yield svc
    shutil.rmtree(root, ignore_errors=True)


async def with_room(service, scenario):
    await service.audio_ipc.start()
    adapter = SpeechAdapter(DirectClient(service))
    await adapter.acquire("room-1")
    service.audio_ipc.fence["room-1"] = 0
    try:
        return await scenario(adapter)
    finally:
        await adapter.release()
        await service.audio_ipc.close()


async def mic(count):
    for i in range(count):
        yield AudioFrame(i + 1, 16000, np.full(1600, 0.01, dtype=np.float32))


def test_room_audio_reaches_the_single_stt_owner(service):
    async def scenario(adapter):
        return [e async for e in adapter.transcribe(BINDING, mic(6))]

    events = run(with_room(service, scenario))
    kinds = [e.kind for e in events]
    assert kinds.count("transcript") == 2 and "speech_start" in kinds
    assert events[-1].text == "hello room"


def test_whisper_is_never_substituted_in_a_room(service):
    service.stt = FakeStt("whisper")

    async def scenario(adapter):
        return [e async for e in adapter.transcribe(BINDING, mic(3))]

    with pytest.raises(AdapterError) as err:
        run(with_room(service, scenario))
    assert err.value.code == "models_not_ready"


def test_capability_is_one_time_and_generation_fenced(service):
    async def scenario(adapter):
        grant = await adapter.client.call(
            "audio_capability",
            owner_id="owner",
            voice_session_id="room-1",
            generation=2,
            direction="stt_input",
            **adapter.lease,
        )
        assert grant["capability"]
        # an older generation can no longer mint, nor can a stale room stream
        with pytest.raises(AdapterError) as err:
            await adapter._open(RoomBinding("owner", "room-1", 1), "stt_input")
        assert err.value.code == "stale_generation"
        _, _, writer = await adapter._open(
            RoomBinding("owner", "room-1", 2), "stt_input"
        )
        writer.close()

    run(with_room(service, scenario))


def test_stop_fences_open_streams_before_teardown(service):
    async def scenario(adapter):
        async def slow():
            async for frame in mic(40):
                yield frame
                await asyncio.sleep(0.01)
                if frame.sequence == 3:
                    await adapter.fence(RoomBinding("owner", "room-1", 1))

        with pytest.raises(AdapterError) as err:
            async for _ in adapter.transcribe(BINDING, slow()):
                pass
        assert err.value.code == "stale_generation"

    run(with_room(service, scenario))


def test_speech_streams_back_and_cancel_drains_the_generator(service):
    service.speech = FakeSpeech(chunks=500)

    async def scenario(adapter):
        cancel, frames = asyncio.Event(), []
        async for frame in adapter.synthesize(BINDING, "hi there", cancel):
            frames.append(frame)
            if len(frames) == 3:
                cancel.set()
        for _ in range(100):  # the service drains its generator after the CANCEL
            if service.speech.closed:
                break
            await asyncio.sleep(0.01)
        return frames

    frames = run(with_room(service, scenario))
    assert frames[0].sample_rate_hz == 24000 and len(frames) < 100
    assert service.speech.closed  # worker drain semantics preserved


def test_room_lease_does_not_open_the_local_microphone(service):
    opened = []
    service.recorder_factory = lambda: opened.append(1)

    async def scenario():
        await service.dispatch(
            {
                "op": "claim",
                "client_id": "livekit:x",
                "claim_id": "c",
                "activated_at": 1,
                "capture": False,
            }
        )
        await service.start_capture()
        return service.recorder

    assert run(scenario()) is None and not opened


# -- worker ---------------------------------------------------------------------


CONFIG = WorkerConfig("ws://x", "jwt", "room", "room-1", 1, "http://engine", "tok")


class FakeRoom:
    def __init__(self):
        self.played, self.flushes = [], 0
        self.closed = asyncio.Event()

    async def connect(self, url, token):
        self.url = url

    async def mic_frames(self):
        yield AudioFrame(1, 16000, np.zeros(10, dtype=np.float32))

    async def play(self, frame):
        self.played.append(frame)

    async def flush(self):
        self.flushes += 1

    async def close(self):
        pass


class FakeAdapter:
    def __init__(self, script=(), tts_frames=50):
        self.script, self.tts_frames = list(script), tts_frames
        self.stopped = self.released = False
        self.gate = asyncio.Event()
        self.stats = SimpleNamespace(first_audio_ms=5, total_ms=9)

    async def readiness(self, owner_id=""):
        return SpeechReadiness("ready")

    async def acquire(self, sid):
        pass

    async def release(self):
        self.released = True

    async def fence(self, binding):
        self.fenced = binding.generation

    async def cancel(self, binding, stream_id=None):
        pass

    async def transcribe(self, binding, frames):
        async for _ in frames:
            pass
        await self.gate.wait()
        for event in self.script:
            yield event
            await asyncio.sleep(0.05)
        await asyncio.sleep(5)

    async def synthesize(self, binding, text, cancel):
        for i in range(self.tts_frames):
            if cancel.is_set():
                return
            yield AudioFrame(i + 1, 24000, np.zeros(240, dtype=np.float32))
            await asyncio.sleep(0.01)


class Recorder:
    def __init__(self):
        self.posts = []

    async def post(self, path, body):
        self.posts.append((path, body))
        return True


def make(script=(), tts_frames=50):
    poster, adapter = Recorder(), FakeAdapter(script, tts_frames)
    return Worker(CONFIG, FakeRoom(), adapter, poster), poster, adapter


def events(poster, name=None):
    return [
        b
        for p, b in poster.posts
        if p == "events" and (name is None or b["event"] == name)
    ]


def test_final_transcript_goes_to_the_engine_with_stt_latency():
    worker, poster, adapter = make(
        [SttEvent("transcript", "run the tests", "en", True, 123)]
    )

    async def scenario():
        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.05)
        adapter.gate.set()
        await asyncio.sleep(0.2)
        worker.stopping.set()
        await task

    run(scenario())
    [(path, body)] = [(p, b) for p, b in poster.posts if p == "transcripts"]
    assert (
        body["speaker"] == "user"
        and body["final"]
        and body["display_text"] == "run the tests"
    )
    assert body["generation"] == 1 and body["voice_session_id"] == "room-1"
    assert {"stage": "stt", "ms": 123} == {
        k: events(poster, "voice.metrics")[0]["payload"][k] for k in ("stage", "ms")
    }
    assert adapter.released


def test_user_speech_interrupts_playback_and_posts_barge_in():
    worker, poster, adapter = make([SttEvent("speech_start")], tts_frames=500)

    async def scenario():
        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.05)
        status, _ = await worker.route(
            "POST", "/speak", {"spoken_text": "a long answer", "turn_id": "t1"}
        )
        assert status == 202
        await asyncio.sleep(0.15)
        spoken = len(worker.room.played)
        adapter.gate.set()  # user starts talking
        await asyncio.sleep(0.3)
        after = len(worker.room.played)
        worker.stopping.set()
        await task
        return spoken, after

    spoken, after = run(scenario())
    assert spoken > 0 and after - spoken < 10  # playback stopped
    assert worker.room.flushes >= 1
    assert any(e["payload"].get("barge_in") for e in events(poster, "voice.state"))
    assert [e["payload"]["stage"] for e in events(poster, "voice.metrics")][:1] == [
        "first_audio"
    ]


def test_stale_speak_and_stop_are_fenced():
    worker, poster, adapter = make()

    async def scenario():
        assert (
            await worker.route("POST", "/speak", {"generation": 0, "spoken_text": "x"})
        )[0] == 409
        assert (await worker.route("POST", "/speak", {"spoken_text": " "}))[0] == 422
        assert (await worker.route("POST", "/stop", {"generation": 1}))[0] == 200
        assert (
            worker.generation == 2 and adapter.fenced == 2 and worker.stopping.is_set()
        )
        # the old generation can no longer queue speech
        assert (
            await worker.route(
                "POST", "/speak", {"generation": 1, "spoken_text": "late"}
            )
        )[0] == 409

    run(scenario())


def test_control_server_requires_the_engine_bearer(capsys):
    worker, _, _ = make()

    async def request(port, auth):
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        body = b"{}"
        writer.write(
            b"POST /stop HTTP/1.1\r\nAuthorization: "
            + auth.encode()
            + b"\r\nContent-Length: 2\r\n\r\n"
            + body
        )
        await writer.drain()
        return (await reader.read()).split(b"\r\n")[0]

    async def scenario():
        task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.1)
        line = capsys.readouterr().out.strip()
        assert line.startswith("WORKER_READY http://127.0.0.1:")
        port = int(line.rsplit(":", 1)[1])
        assert b"401" in await request(port, "Bearer nope")
        assert not worker.stopping.is_set()
        assert b"200" in await request(port, "Bearer tok")
        await task

    run(scenario())


def test_download_progress_is_posted_while_models_load():
    worker, poster, adapter = make()
    states = iter(
        [
            SpeechReadiness("downloading", "parakeet / m", "parakeet", 1, 10),
            SpeechReadiness("downloading", "parakeet / m", "parakeet", 6, 10),
            SpeechReadiness("ready"),
        ]
    )

    async def readiness(owner_id=""):
        return next(states)

    adapter.readiness = readiness
    import kollabor_voice.livekit_worker as mod

    mod.READINESS_POLL_S, old = 0, mod.READINESS_POLL_S
    try:
        run(worker.wait_ready())
    finally:
        mod.READINESS_POLL_S = old
    progress = [e["payload"] for e in events(poster, "voice.state")]
    assert [(p["model"], p["bytes_done"], p["bytes_total"]) for p in progress] == [
        ("parakeet", 1, 10),
        ("parakeet", 6, 10),
    ]


def test_helpers_rebatch_and_resample():
    async def frames():
        for _ in range(5):
            yield AudioFrame(1, 16000, np.zeros(700, dtype=np.float32))

    async def collect():
        return [len(f.samples) async for f in rebatch(frames())]

    assert run(collect()) == [1600, 1600, 300]
    assert len(resample(np.zeros(16000, np.float32), 16000, 24000)) == 24000


def test_config_loads_from_env_and_poster_never_raises():
    raw = {k: getattr(CONFIG, k) for k in WorkerConfig.__dataclass_fields__}
    assert (
        WorkerConfig.from_env({"KOLLAB_VOICE_WORKER_CONFIG": json.dumps(raw)}) == CONFIG
    )
    unreachable = WorkerConfig("ws://x", "j", "r", "s", 1, "http://127.0.0.1:9", "t")
    assert run(EnginePoster(unreachable).post("events", {})) is False
