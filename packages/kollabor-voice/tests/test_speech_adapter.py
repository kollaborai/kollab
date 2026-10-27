"""KVA1, the speech adapter and the service audio socket, with no network or audio device."""

import asyncio
import concurrent.futures
import shutil
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from kollabor_voice import kva
from kollabor_voice.control import VoiceError
from kollabor_voice.service import VoiceService
from kollabor_voice.speech_adapter import (
    AdapterError,
    AudioFrame,
    RoomBinding,
    SpeechAdapter,
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
    with patch("kollabor_voice.speech_settings.apple_silicon", return_value=True):
        refused = map_readiness(whisper)  # never claim Parakeet
    assert refused.state == "error" and refused.retryable
    assert "needs Parakeet v3 to hear you" in refused.detail  # voice-room.tsx keys on it
    with patch("kollabor_voice.speech_settings.apple_silicon", return_value=False):
        intel = map_readiness(whisper)
    assert intel.state == "error" and "Apple silicon" in intel.detail
    assert "to hear you" not in intel.detail  # no Install button where it cannot run
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


def test_a_newer_room_sees_ready_while_an_older_room_holds_the_lease(service):
    # A room whose browser left keeps the lease. The next room's worker waits for
    # ready before it claims: "starting" kept it out for 20 minutes, no room at all.
    async def scenario():
        await service.dispatch(
            {"op": "claim", "client_id": "livekit:old", "claim_id": "c1", "activated_at": 1, "capture": False}
        )
        return await service.dispatch({"op": "status"}), await SpeechAdapter(DirectClient(service)).readiness()

    status, readiness = run(scenario())
    assert status["owner"] == "livekit:old" and status["state"] == "ready"
    assert readiness.state == "ready"


def test_a_cancelled_synthesis_task_still_drains_the_generator(service):
    service.speech = FakeSpeech(chunks=500)

    async def scenario(adapter):
        seen = []

        async def consume():
            async for frame in adapter.synthesize(BINDING, "hi there", asyncio.Event()):
                seen.append(frame)

        task = asyncio.create_task(consume())
        while len(seen) < 3:
            await asyncio.sleep(0.01)
        task.cancel()  # what LiveKit does to the TTS when the user talks over it
        await asyncio.gather(task, return_exceptions=True)
        for _ in range(100):
            if service.speech.closed:
                break
            await asyncio.sleep(0.01)

    run(with_room(service, scenario))
    assert service.speech.closed


class LockedSpeech(FakeSpeech):
    """Holds an RLock across its yields, as SpeechEngine.stream does."""

    def __init__(self, chunks):
        super().__init__(chunks)
        self.lock = threading.RLock()

    def stream(self, text):
        with self.lock:
            yield from super().stream(text)


class Alternating(concurrent.futures.ThreadPoolExecutor):
    """A default executor that never runs two calls in a row on the same thread."""

    def __init__(self):
        super().__init__(1)  # asyncio insists on the type; the work goes to the pools
        self.pools = [concurrent.futures.ThreadPoolExecutor(1) for _ in range(2)]
        self.turn = 0

    def submit(self, fn, /, *args, **kwargs):
        self.turn ^= 1
        return self.pools[self.turn].submit(fn, *args, **kwargs)

    def shutdown(self, wait=True, **kwargs):
        for pool in self.pools:
            pool.shutdown(wait=wait)
        super().shutdown(wait=wait)


def lock_is_free(lock):
    """True when another thread can take the lock: nothing was left holding it."""
    got = []

    def take():
        if lock.acquire(timeout=1):
            lock.release()
            got.append(True)

    worker = threading.Thread(target=take)
    worker.start()
    worker.join()
    return got == [True]


@pytest.mark.parametrize("talk_over", [False, True])
def test_the_speech_lock_is_released_by_the_thread_that_took_it(service, caplog, talk_over):
    service.speech = LockedSpeech(chunks=500 if talk_over else 3)

    async def scenario(adapter):
        asyncio.get_running_loop().set_default_executor(Alternating())
        cancel, frames = asyncio.Event(), []
        async for frame in adapter.synthesize(BINDING, "hi there", cancel):
            frames.append(frame)
            if talk_over and len(frames) == 3:
                cancel.set()  # the listener talked over the reply
        for _ in range(100):
            if service.speech.closed:
                break
            await asyncio.sleep(0.01)

    run(with_room(service, scenario))
    assert service.speech.closed
    assert "Audio stream failed" not in caplog.text
    assert lock_is_free(service.speech.lock)
