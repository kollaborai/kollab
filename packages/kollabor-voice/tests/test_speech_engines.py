"""Selectable speech preserves the device queue's waveform and cancellation contract."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

np = pytest.importorskip("numpy", reason="Run audio backend checks with the isolated audio runtime")

from kollabor_voice.output import SpeechQueue  # noqa: E402
from kollabor_voice.speech import SpeechEngine  # noqa: E402
from kollabor_voice.speech_settings import (  # noqa: E402
    load_speech_preferences as load_preferences,
)
from kollabor_voice.speech_settings import (  # noqa: E402
    save_speech_preferences as save_preferences,
)
from kollabor_voice.speech_settings import speech_key  # noqa: E402


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("KOLLAB_VOICE_ROOT", str(tmp_path))
    return tmp_path


class FakeVibe:
    def __init__(self, root, preferences, progress):
        self.preferences = preferences
        self.closed = False
        self.process = SimpleNamespace(pid=42, poll=lambda: None)
        self.ready = {"device": "mps", "rate": 24000}

    def stream(self, text):
        yield np.full(2400, .1, dtype=np.float32)

    def close(self):
        self.closed = True


def say(engine, text):
    """One sentence's audio, as the speech queue plays it."""
    chunks = list(engine.stream(text))
    return np.concatenate([samples for samples, _ in chunks]), chunks[0][1]


def test_kokoro_preferences_reach_synthesis(root):
    synth = Mock(return_value=(np.ones(10), 24000))
    engine = SpeechEngine(root, synth)
    say(engine, "Hello there.")
    synth.assert_called_once_with("Hello there.")
    save_preferences({"kokoro_voice": "bf_emma", "kokoro_speed": 1.2}, root)
    say(engine, "Hello again.")
    synth.assert_called_with("Hello again.", voice="bf_emma", speed=1.2)


def test_switching_engines_closes_only_owned_worker_and_preserves_other_preferences(
    root, monkeypatch
):
    monkeypatch.setattr("kollabor_voice.speech.VibeVoiceWorker", FakeVibe)
    save_preferences({"tts_engine": "vibevoice", "vibevoice_voice": "Emma"}, root)
    save_preferences({"context_lines": 23}, root)
    engine = SpeechEngine(root, Mock(return_value=(np.ones(10), 24000)))
    audio, rate = say(engine, "Use the selected voice.")
    assert rate == 24000 and len(audio) == 2400
    worker = engine.worker
    assert worker.preferences["vibevoice_voice"] == "Emma"
    assert engine.status()["engine"] == "vibevoice"
    save_preferences({"tts_engine": "kokoro"}, root)
    say(engine, "Switch back to Kokoro.")
    assert worker.closed and engine.worker is None
    assert load_preferences(root)["context_lines"] == 23


@pytest.mark.parametrize("changes", [
    {"tts_engine": "unknown"}, {"kokoro_voice": "none"}, {"kokoro_speed": float("nan")},
    {"vibevoice_steps": 1.5}, {"vibevoice_cfg": True},
    {"tts_engine": "vibevoice", "vibevoice_prompt": "/no/such/prompt.pt"},
    {"tts_engine": "vibevoice", "vibevoice_model": "/no/such/model"},
])
def test_invalid_speech_update_is_atomic(root, changes):
    save_preferences({"kokoro_voice": "af_bella"}, root)
    before = (root / "preferences.json").read_bytes()
    with pytest.raises(ValueError):
        save_preferences(changes, root)
    assert (root / "preferences.json").read_bytes() == before


def test_backend_failure_stays_visible_without_silent_fallback(root, monkeypatch):
    def fail(*args):
        raise RuntimeError("model cannot load")

    monkeypatch.setattr("kollabor_voice.speech.VibeVoiceWorker", fail)
    save_preferences({"tts_engine": "vibevoice"}, root)
    kokoro = Mock()
    engine = SpeechEngine(root, kokoro)
    with pytest.raises(RuntimeError, match="model cannot load"):
        say(engine, "A spoken response.")
    assert engine.status()["state"] == "error"
    kokoro.assert_not_called()
    # Once the engine can load, the next sentence brings it back on its own.
    monkeypatch.setattr("kollabor_voice.speech.VibeVoiceWorker", FakeVibe)
    say(engine, "The next response.")
    assert engine.status()["state"] == "ready"
    kokoro.assert_not_called()


def test_a_dead_worker_starts_again_on_the_next_sentence(root, monkeypatch):
    monkeypatch.setattr("kollabor_voice.speech.VibeVoiceWorker", FakeVibe)
    save_preferences({"tts_engine": "vibevoice"}, root)
    engine = SpeechEngine(root, Mock())
    say(engine, "First.")
    dead = engine.worker
    dead.process.poll = lambda: 1  # the worker process exited
    assert engine.status()["state"] == "error"
    audio, _ = say(engine, "Second.")
    assert dead.closed and engine.worker is not dead
    assert engine.status()["state"] == "ready" and len(audio) == 2400


def test_inactive_engine_changes_do_not_reload_the_active_worker(root, monkeypatch):
    monkeypatch.setattr("kollabor_voice.speech.VibeVoiceWorker", FakeVibe)
    save_preferences({"tts_engine": "vibevoice"}, root)
    engine = SpeechEngine(root, Mock())
    engine.configure()
    worker = engine.worker
    key = speech_key(load_preferences(root))
    save_preferences({"kokoro_speed": 1.2}, root)
    engine.configure()
    assert engine.worker is worker and not worker.closed
    assert speech_key(load_preferences(root)) == key


@pytest.mark.asyncio
async def test_cancelled_vibevoice_generation_never_reaches_playback(root, monkeypatch):
    started, finish = threading.Event(), threading.Event()

    class SlowVibe(FakeVibe):
        def stream(self, text):
            started.set()
            finish.wait(3)
            yield from super().stream(text)

    monkeypatch.setattr("kollabor_voice.speech.VibeVoiceWorker", SlowVibe)
    save_preferences({"tts_engine": "vibevoice"}, root)
    engine = SpeechEngine(root, Mock())
    play = Mock(return_value=True)
    queue = SpeechQueue(root, engine.stream, play)
    ids = queue.admit("This is a short response.", "reply", "epoch", "producer")["ids"]
    task = asyncio.create_task(queue.run())
    try:
        await asyncio.wait_for(asyncio.to_thread(started.wait, 2), 3)
        queue.cancel("epoch")
        finish.set()
        await asyncio.wait_for(queue.queue.join(), 3)
        assert queue.jobs[ids[0]]["receipt"]["state"] == "cancelled"
        play.assert_not_called()
    finally:
        finish.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        engine.close()
