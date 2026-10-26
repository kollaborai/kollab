"""Selectable transcription: Whisper stays the default and covers a Parakeet gap."""

import asyncio
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path
from unittest.mock import Mock

import pytest

np = pytest.importorskip(
    "numpy", reason="Run audio backend checks with the isolated audio runtime"
)

from kollabor_voice import speech_settings, transcription  # noqa: E402
from kollabor_voice.control import manifest  # noqa: E402
from kollabor_voice.speech_settings import (  # noqa: E402
    load_speech_preferences as load_preferences,
)
from kollabor_voice.speech_settings import (  # noqa: E402
    save_speech_preferences as save_preferences,
)
from kollabor_voice.transcription import (  # noqa: E402
    ParakeetWorker,
    TranscriptionEngine,
)

AUDIO = np.zeros(16000, np.float32)


@pytest.fixture
def root(monkeypatch):
    # Short: the service's socket path must fit in sun_path.
    with tempfile.TemporaryDirectory(prefix="kv-", dir="/tmp") as directory:
        monkeypatch.setenv("KOLLAB_VOICE_ROOT", directory)
        monkeypatch.setattr(speech_settings, "apple_silicon", lambda: True)
        yield Path(directory)


class FakeParakeet:
    made = []

    def __init__(self, root, progress):
        progress(
            state="downloading",
            detail="parakeet-v3 / model.safetensors",
            downloaded=1,
            total=4,
        )
        self.closed, self.calls = False, []
        self.ready = {"ready": True, "device": "Device(gpu, 0)", "cold_seconds": 1.0}
        FakeParakeet.made.append(self)

    def transcribe(self, audio):
        self.calls.append(len(audio))
        return "Aquamarine, run the tests."

    def close(self):
        self.closed = True


@pytest.fixture
def parakeet(monkeypatch):
    FakeParakeet.made = []
    monkeypatch.setattr(transcription, "ParakeetWorker", FakeParakeet)
    return FakeParakeet


def test_parakeet_is_the_default_on_apple_silicon(root, monkeypatch):
    monkeypatch.setattr(speech_settings, "apple_silicon", lambda: True)
    assert load_preferences(root)["stt_engine"] == "parakeet"


def test_whisper_is_the_default_elsewhere_and_gets_the_names(root, monkeypatch):
    monkeypatch.setattr(speech_settings, "apple_silicon", lambda: False)
    whisper = Mock(return_value=("Aquamarine, hi.", "en"))
    engine = TranscriptionEngine(root, whisper)
    engine.configure()
    assert load_preferences(root)["stt_engine"] == "whisper"
    assert (
        engine.status()["engine"] == "whisper" and engine.status()["state"] == "ready"
    )
    assert engine.transcribe(AUDIO, ("aquamarine",)) == ("Aquamarine, hi.", "en")
    whisper.assert_called_once_with(AUDIO, ("aquamarine",))


def test_parakeet_transcribes_once_ready_and_reports_no_language(root, parakeet):
    save_preferences({"stt_engine": "parakeet"}, root)
    whisper = Mock(return_value=("whisper", "en"))
    engine = TranscriptionEngine(root, whisper)
    engine.configure()
    status = engine.status()
    assert (status["engine"], status["state"], status["device"]) == (
        "parakeet",
        "ready",
        "Device(gpu, 0)",
    )
    assert engine.transcribe(AUDIO, ("aquamarine",)) == (
        "Aquamarine, run the tests.",
        None,
    )
    whisper.assert_not_called()


def test_progress_is_visible_while_whisper_covers_preparation(root, monkeypatch):
    save_preferences({"stt_engine": "parakeet"}, root)
    seen = []

    class Slow(FakeParakeet):
        def __init__(self, root, progress):
            progress(
                state="downloading",
                detail="parakeet-v3 / model.safetensors",
                downloaded=1,
                total=4,
            )
            seen.append(engine.status())
            seen.append(engine.transcribe(AUDIO))
            super().__init__(root, progress)

    monkeypatch.setattr(transcription, "ParakeetWorker", Slow)
    engine = TranscriptionEngine(root, Mock(return_value=("whisper", "en")))
    engine.configure()
    assert seen[0]["state"] == "downloading" and "25%" in seen[0]["detail"]
    assert "Whisper Base transcribes until it is ready" in seen[0]["detail"]
    assert seen[1] == ("whisper", "en")


def test_failed_setup_stays_visible_and_retries_later(root, monkeypatch):
    save_preferences({"stt_engine": "parakeet"}, root)

    def broken(root, progress):
        raise RuntimeError("Checksum mismatch for model.safetensors")

    monkeypatch.setattr(transcription, "ParakeetWorker", broken)
    engine = TranscriptionEngine(root, Mock(return_value=("whisper", "en")))
    engine.configure()
    status = engine.status()
    assert status["state"] == "error" and "Checksum mismatch" in status["detail"]
    assert "Whisper Base transcribes until it starts again" in status["detail"]
    assert engine.transcribe(AUDIO) == ("whisper", "en")
    assert not engine.retry_due()
    engine.failed_at = time.monotonic() - transcription.RETRY_SECONDS
    assert engine.retry_due()


def test_a_parakeet_failure_mid_session_falls_back_for_that_utterance(root, parakeet):
    save_preferences({"stt_engine": "parakeet"}, root)
    engine = TranscriptionEngine(root, Mock(return_value=("whisper", "en")))
    engine.configure()
    worker = engine.worker
    worker.transcribe = Mock(side_effect=TimeoutError("no answer within 30s"))
    assert engine.transcribe(AUDIO) == ("whisper", "en")
    assert worker.closed and engine.worker is None
    assert engine.status()["state"] == "error"
    assert "no answer within 30s" in engine.status()["detail"]


def test_switching_back_to_whisper_closes_the_worker(root, parakeet):
    save_preferences({"stt_engine": "parakeet"}, root)
    engine = TranscriptionEngine(root, Mock(return_value=("whisper", "en")))
    engine.configure()
    engine.configure()  # unchanged: no second worker
    assert len(parakeet.made) == 1
    save_preferences({"stt_engine": "whisper"}, root)
    engine.configure()
    assert parakeet.made[0].closed and engine.worker is None
    assert engine.transcribe(AUDIO) == ("whisper", "en")


def test_close_during_setup_never_keeps_a_worker(root, monkeypatch):
    save_preferences({"stt_engine": "parakeet"}, root)

    class Late(FakeParakeet):
        def __init__(self, root, progress):
            engine.close()
            super().__init__(root, progress)

    monkeypatch.setattr(transcription, "ParakeetWorker", Late)
    engine = TranscriptionEngine(root, Mock(return_value=("whisper", "en")))
    engine.configure()
    assert engine.worker is None and Late.made[-1].closed


def test_parakeet_needs_apple_silicon(root, monkeypatch):
    save_preferences({"stt_engine": "parakeet"}, root)
    monkeypatch.setattr(speech_settings, "apple_silicon", lambda: False)
    assert load_preferences(root)["stt_engine"] == "whisper"
    with pytest.raises(ValueError, match="Apple silicon"):
        save_preferences({"stt_engine": "parakeet"}, root)
    with pytest.raises(ValueError):
        save_preferences({"stt_engine": "nemo"}, root)


def test_stt_choice_never_reloads_or_rekeys_speech(root):
    from kollabor_voice.speech_settings import speech_key

    before = speech_key(load_preferences(root))
    save_preferences({"stt_engine": "parakeet"}, root)
    assert speech_key(load_preferences(root)) == before


def test_manifest_pins_the_parakeet_runtime_and_weights():
    spec = manifest()["stt"]["parakeet"]
    assert all("==" in pin for pin in spec["runtime"])
    bundle = spec["bundle"]
    assert bundle["license"] == "CC-BY-4.0" and len(bundle["revision"]) == 40
    assert bundle["revision"] in bundle["base_url"]
    assert {f["name"] for f in bundle["files"]} == {"config.json", "model.safetensors"}


WORKER = textwrap.dedent("""
    import json, sys
    print(json.dumps({"ready": True, "device": "gpu"}), flush=True)
    for line in sys.stdin:
        request = json.loads(line)
        if request["audio"] == "":
            continue  # never answers
        identity = "other" if len(request["audio"]) > 100 else request["id"]
        print(json.dumps({"id": identity, "ok": True, "text": "hello"}), flush=True)
    """)


def worker():
    process = subprocess.Popen(
        [sys.executable, "-c", WORKER], stdin=subprocess.PIPE, stdout=subprocess.PIPE
    )
    instance = object.__new__(ParakeetWorker)
    instance.process = process
    instance.ready = instance._receive(None, 10)
    return instance


def test_worker_protocol_rejects_late_and_foreign_replies(monkeypatch):
    instance = worker()
    try:
        assert instance.ready["ready"]
        assert instance.transcribe(np.zeros(8, np.float32)) == "hello"
        with pytest.raises(RuntimeError, match="identity"):
            instance.transcribe(np.zeros(100, np.float32))
        monkeypatch.setattr(transcription.base64, "b64encode", lambda _: b"")
        with pytest.raises(TimeoutError):
            instance.transcribe(AUDIO, timeout=0.2)
    finally:
        instance.close()
    assert instance.process.poll() is not None


@pytest.mark.asyncio
async def test_service_switches_engines_from_preferences_without_restart(
    root, parakeet
):
    from kollabor_voice.service import VoiceService

    class Models:
        def warmup(self):
            pass

        def transcribe(self, audio, names=()):
            return "whisper", "en"

        def synthesize(self, text):
            return np.zeros(16, np.float32), 24000

    save_preferences({"stt_engine": "whisper"}, root)
    service = VoiceService(
        root, load_assets=lambda *args: {}, load_models=lambda _: Models()
    )
    await service.start()
    try:
        async with asyncio.timeout(5):
            while service.status()["transcription"].get("state") != "ready":
                await asyncio.sleep(0.01)
        assert service.status()["transcription"]["engine"] == "whisper"
        assert service.status()["detail"] == "Transcription and speech ready"
        save_preferences({"stt_engine": "parakeet"}, root)
        async with asyncio.timeout(5):
            while (
                service.status()["transcription"].get("engine") != "parakeet"
                or service.status()["transcription"]["state"] != "ready"
            ):
                await asyncio.sleep(0.01)
        assert await asyncio.to_thread(service.stt.transcribe, AUDIO, ()) == (
            "Aquamarine, run the tests.",
            None,
        )
    finally:
        await service.close()
    assert parakeet.made[0].closed
