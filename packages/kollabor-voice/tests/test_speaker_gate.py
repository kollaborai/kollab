"""Speaker gate and echo cancellation checks; doubles stand in for the models."""

import asyncio
import json
import os
import queue
import sys
import tempfile
import threading
import time
import types
from pathlib import Path

import numpy as np
import pytest
import pytest_asyncio
from kollabor_voice.audio import AudioSegment, SpeechSegmenter, StreamPlayer
from kollabor_voice.client import VoiceClient
from kollabor_voice.control import VoiceError, manifest
from kollabor_voice.echo import EchoCanceller, PlaybackReference
from kollabor_voice.recorder import Frame
from kollabor_voice.service import VoiceService
from kollabor_voice.speaker import (
    Enrollment,
    SpeakerGate,
    load_voiceprint,
    model_id,
    save_voiceprint,
    verdict,
)

RATE = 16000
USER, TV = np.array([1.0, 0.0, 0.0], np.float32), np.array([0.0, 1.0, 0.0], np.float32)


@pytest.fixture
def root():
    with tempfile.TemporaryDirectory(prefix="kv-", dir="/tmp") as directory:
        yield Path(directory)


class Voices:
    """Fake speaker model: the first sample of a segment says who is talking."""

    def embed(self, audio):
        vector = (USER if audio[0] > 0 else TV) + np.array([0, 0, 0.1], np.float32)
        return vector / np.linalg.norm(vector)


def speech(seconds, user=True):
    audio = np.zeros(int(seconds * RATE), np.float32)
    audio[0] = 1.0 if user else -1.0
    return audio


# -- playback reference -------------------------------------------------------


def test_reference_returns_playback_at_its_time():
    reference = PlaybackReference()
    tone = np.arange(1, 1601, dtype=np.float32)  # 0.1 s
    assert reference.play(tone, RATE, at=100.0) == pytest.approx(100.1)
    window = reference.window(99.95, 1600)
    assert not window[:800].any()  # before playback: silence
    np.testing.assert_array_equal(window[800:], tone[:800])


def test_reference_resamples_and_clears_old_and_cut_audio():
    reference = PlaybackReference(seconds=1)
    reference.play(np.ones(2400, np.float32), 24000, at=10.0)  # 0.1 s at 24 kHz
    assert reference.window(10.0, 1600).sum() == pytest.approx(1600)
    # Playback long after: the old audio is gone, not wrapped back in.
    reference.play(np.full(1600, 2.0, np.float32), RATE, at=50.0)
    assert not reference.window(10.0, 1600).any()
    assert not reference.window(49.5, 1600).any()
    # Stopped early: nothing after the cut sounds.
    reference.cut(at=50.05)
    window = reference.window(50.0, 1600)
    assert (window[:800] == 2.0).all() and not window[800:].any()
    # Longer than the ring: the end is kept, in its place.
    ramp = np.arange(1, 2 * RATE + 1, dtype=np.float32)  # 2 s into a 1 s ring
    reference.play(ramp, RATE, at=60.0)
    np.testing.assert_array_equal(reference.window(61.5, 800), ramp[-8000:-7200])
    assert not reference.window(60.0, 800).any()  # fell out of the ring


def test_echo_canceller_feeds_aec3_the_playback_ahead_of_the_mic(monkeypatch):
    calls = []

    class Aec3:
        def __init__(self, sample_rate, num_channels):
            assert (sample_rate, num_channels) == (RATE, 1)

        def process(self, near, far):
            calls.append(far.copy())
            return near - far

    monkeypatch.setitem(
        sys.modules, "pywebrtc_audio", types.SimpleNamespace(EchoCanceller=Aec3)
    )
    reference = PlaybackReference()
    reference.play(np.ones(RATE, np.float32), RATE, at=5.0)
    mic = np.full(480, 3.0, np.float32)
    out = EchoCanceller(reference, lead=0.06).process(mic, captured_at=4.93)
    # Paired with playback from 4.99 s: 10 ms before it starts, then sound.
    [far] = calls
    assert not far[:160].any() and far[160:].all()
    np.testing.assert_array_equal(out, mic - far)


class Stream:
    latency, active = 0.05, False

    def start(self):
        self.active = True

    def write(self, audio):
        return False  # no underrun

    def stop(self):
        self.active = False

    abort = stop

    def close(self):
        pass


def test_player_tells_the_reference_when_each_chunk_sounds(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "sounddevice", types.SimpleNamespace(OutputStream=lambda **_: Stream())
    )
    player = StreamPlayer()
    player.reference = PlaybackReference()
    started = time.monotonic()
    assert player.write(np.ones(12000, np.float32), 24000, threading.Event())
    # Back to back from the device latency on: 0.5 s of playback.
    assert player.sounds_until - started == pytest.approx(0.55, abs=0.02)
    assert player.reference.window(started + 0.07, 1600).all()
    player.abort()
    assert player.sounds_until == 0.0
    assert not player.reference.window(started + 0.3, 1600).any()  # cut off


# -- speaker gate -------------------------------------------------------------


def test_verdict_judges_long_segments_and_passes_short_ones():
    model = Voices()
    assert verdict(model, USER, speech(0.6), 0.5) == {"speaker": "unverified"}
    assert verdict(model, USER, speech(2), 0.5)["speaker"] == "user"
    other = verdict(model, USER, speech(2, user=False), 0.5)
    assert other["speaker"] == "other_speaker" and other["speaker_score"] < 0.5


def test_enrollment_leaves_out_a_voice_unlike_the_rest():
    enrollment = Enrollment(Voices(), threshold=0.5, target=30)
    for _ in range(4):
        enrollment.add(speech(6))
    enrollment.add(speech(8, user=False))  # the TV says a line
    enrollment.add(speech(0.5))  # too short to use
    assert (enrollment.seconds, enrollment.dropped) == (24, 1)
    assert not enrollment.done()
    enrollment.add(speech(6))
    assert enrollment.done()
    assert float(enrollment.voiceprint() @ Voices().embed(speech(2))) > 0.99


def test_voiceprint_is_private_and_tied_to_its_model(root):
    save_voiceprint(root, "model-a", USER, 31.0, 6)
    assert os.stat(root / "voiceprint.json").st_mode & 0o777 == 0o600
    embedding, info = load_voiceprint(root, "model-a")
    np.testing.assert_allclose(embedding, USER)
    assert (info["seconds"], info["segments"]) == (31.0, 6)
    assert load_voiceprint(root, "model-b") is None  # another model: enroll again
    (root / "voiceprint.json").write_text("{not json")
    assert load_voiceprint(root, "model-a") is None


def test_gate_enrolls_then_judges(root):
    gate = SpeakerGate(root)
    gate.model, gate.state = Voices(), "ready"
    gate.configure({"speaker_gate": True, "speaker_threshold": 0.5})
    assert gate.judge(speech(2)) == {}  # no voiceprint yet: everything passes
    gate.enroll()
    assert gate.status()["enrollment"] == {"seconds": 0.0, "target": 30.0, "dropped": 0}
    assert gate.judge(speech(8), overlap=True) == {"speaker": "enrolling"}
    assert gate.status()["enrollment"]["seconds"] == 0  # voice was talking
    for _ in range(4):
        assert gate.judge(speech(8)) == {"speaker": "enrolling"}
    status = gate.status()
    assert status["enrollment"] is None and status["voiceprint"]["seconds"] == 32
    assert status["saved_at"]
    saved = json.loads((root / "voiceprint.json").read_text())
    assert saved["model"] == model_id()
    assert gate.judge(speech(2))["speaker"] == "user"
    assert gate.judge(speech(2, user=False))["speaker"] == "other_speaker"
    gate.configure({"speaker_gate": False, "speaker_threshold": 0.5})
    assert gate.judge(speech(2, user=False)) == {}  # off: nothing is judged
    assert gate.forget() and not (root / "voiceprint.json").exists()
    assert gate.status()["voiceprint"] is None and not gate.forget()


def test_manifest_pins_the_speaker_model():
    bundle = manifest()["speaker"]
    assert bundle["license"] and bundle["revision"] in bundle["base_url"]
    [item] = bundle["files"]
    assert len(item["sha256"]) == 64 and item["size"] > 1_000_000
    assert model_id() == f"{bundle['model']}@{item['sha256'][:16]}"


# -- the device service -------------------------------------------------------


class Recorder:
    def __init__(self):
        self.frames, self.events = queue.Queue(), queue.Queue()
        self._last_frame_ts = None
        self.level, self.device_name = 0.02, "test device"

    def start(self):
        pass

    def stop(self):
        pass


class Speaker:
    def write(self, samples, rate, cancelled):
        return True

    def finish(self):
        pass

    def abort(self):
        pass


class Models:
    def warmup(self):
        pass

    def transcribe(self, audio, names=()):
        text = "Kollab, check the tests." if audio[0] > 0 else "Breaking news tonight."
        return text, "en"

    def synthesize(self, text):
        return np.zeros(16, np.float32), 24000


async def until(predicate, seconds=3):
    async with asyncio.timeout(seconds):
        while not predicate():
            await asyncio.sleep(0.01)


@pytest_asyncio.fixture
async def service(root):
    server = VoiceService(
        root,
        load_assets=lambda *args: {},
        load_models=lambda _: Models(),
        recorder_factory=Recorder,
        segmenter_factory=lambda: SpeechSegmenter(lambda _: True),
        player=Speaker(),
    )
    server.gate.model = Voices()  # loaded: the gate never downloads in tests
    await server.start()
    await until(lambda: server.setup["state"] == "ready")
    try:
        yield server
    finally:
        await server.close()


async def claim(client):
    lease = await client.call(
        "claim", client_id="one", claim_id="one", activated_at=time.time()
    )
    return lease, {"token": lease["token"], "epoch": lease["epoch"]}


async def hear(service, audio):
    now = time.time()
    service.submit(AudioSegment(audio, now - len(audio) / RATE, now))
    await service.segments.join()


async def records(client, lease, auth):
    response = await client.call("read", **auth, stream_id=lease["stream_id"], after=0)
    return response["records"]


def prefer(root, **values):
    (root / "preferences.json").write_text(json.dumps(values))


@pytest.mark.asyncio
async def test_another_speaker_is_kept_as_context_not_heard(service, root):
    client = VoiceClient(root)
    lease, auth = await claim(client)
    save_voiceprint(root, model_id(), USER, 31.0, 6)
    prefer(root, speaker_gate=True)
    await until(lambda: service.gate.enabled)
    service.gate.reload()
    await hear(service, speech(2, user=False))
    assert (await client.call("status"))["last_transcript"] is None
    await hear(service, speech(2))
    assert (await client.call("status"))["last_transcript"]["text"] == (
        "Kollab, check the tests."
    )
    tv, user = await records(client, lease, auth)
    assert (tv["text"], tv["speaker"]) == ("Breaking news tonight.", "other_speaker")
    assert user["speaker"] == "user" and tv["speaker_score"] < 0.5 <= user["speaker_score"]
    status = (await client.call("status"))["speaker"]
    assert status["enabled"] and status["voiceprint"]["seconds"] == 31.0


@pytest.mark.asyncio
async def test_enrollment_needs_the_lease_and_ends_with_it(service, root):
    client = VoiceClient(root)
    with pytest.raises(VoiceError):
        await client.call("speaker_enroll", token="x", epoch="y")
    lease, auth = await claim(client)
    assert (await client.call("speaker_enroll", **auth))["enrollment"]["target"] == 30
    await hear(service, speech(2))
    [record] = await records(client, lease, auth)
    assert record["speaker"] == "enrolling"
    await client.call("release", **auth)
    await until(lambda: service.owner is None)
    assert service.gate.status()["enrollment"] is None  # voice off cancels it
    save_voiceprint(root, model_id(), USER, 31.0, 6)
    service.gate.reload()
    result = await client.call("speaker_forget")  # no lease needed
    assert result["forgotten"] and result["speaker"]["voiceprint"] is None
    assert not (root / "voiceprint.json").exists()


@pytest.mark.asyncio
async def test_echo_cancellation_follows_the_preference(service, root, monkeypatch):
    seen = []

    class Aec3:
        def __init__(self, sample_rate, num_channels):
            pass

        def process(self, near, far):
            seen.append(float(far.max()))
            return near

    monkeypatch.setitem(
        sys.modules, "pywebrtc_audio", types.SimpleNamespace(EchoCanceller=Aec3)
    )
    client = VoiceClient(root)
    lease, auth = await claim(client)
    assert service.echo is None
    assert service.output.player.reference is service.reference
    prefer(root, echo_cancellation=True)
    await until(lambda: service.echo is not None)
    assert service.status()["echo_cancellation"] == {
        "enabled": True,
        "active": True,
        "detail": "",
    }
    # The frame captured while voice plays is paired with that playback.
    service.reference.play(np.ones(RATE, np.float32), RATE, at=time.monotonic())
    service.consume_frame(Frame(1, time.monotonic() + 0.1, np.zeros(480, np.float32)))
    assert seen == [1.0]
    await hear(service, speech(2))
    [record] = await records(client, lease, auth)
    assert record["echo_cancellation"] is True
    prefer(root, echo_cancellation=False)
    await until(lambda: service.echo is None)


@pytest.mark.asyncio
async def test_echo_cancellation_missing_library_is_reported(service, root, monkeypatch):
    monkeypatch.setitem(sys.modules, "pywebrtc_audio", None)  # import fails
    client = VoiceClient(root)
    await claim(client)
    prefer(root, echo_cancellation=True)
    await until(lambda: service.status()["echo_cancellation"]["detail"])
    status = service.status()["echo_cancellation"]
    assert status["enabled"] and not status["active"]
    assert status["detail"].startswith("Echo cancellation unavailable")
