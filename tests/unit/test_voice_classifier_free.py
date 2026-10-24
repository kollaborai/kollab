"""Voice never reaches the classifier; the classifier stays for other features (#301).

Marco, 2026-10-23: "not to completely remove it but just to revamp the voice
feature so the classifier shouldn't show up on there anymore."
"""

import asyncio
import importlib
import importlib.util
import json
import re
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from plugins.voice_plugin import VoicePlugin
from tests.unit.test_voice_plugin_lifecycle import FakeBus

ROOT = Path(__file__).resolve().parents[2]
VOICE = ROOT / "packages" / "kollabor-voice" / "src" / "kollabor_voice"
MODULES = (
    "classifiers",
    "laya",
    "qwen",
    "laya_voice_model",
    "laya_worker",
    "qwen_worker",
)
MODELS = ("voice-intent-v1.safetensors", "spoken-style-v1.safetensors")
# Voice screens and the voice path: none of these may touch a classifier module.
VOICE_SOURCES = (
    "plugins/voice_plugin.py",
    "kollabor/panels/voice.py",
    "kollabor/commands/system_commands/handlers/voicemode.py",
    "kollabor/state/snapshots.py",
    "packages/kollabor-tui/src/kollabor_tui/status/core_widgets.py",
    "packages/kollabor-tui/src/kollabor_tui/status/layout_renderer.py",
    *(
        f"packages/kollabor-voice/src/kollabor_voice/{name}.py"
        for name in (
            "client",
            "observer",
            "acks",
            "speech_review",
            "speech_cache",
            "speech_settings",
            "voice_context",
            "speech_adapter",
            "livekit_worker",
            "audio_ipc",
        )
    ),
)
CLASSIFIER_IMPORT = re.compile(
    r"^\s*(?:from|import)\s+(?:kollabor_voice)?\.?(?:%s)\b" % "|".join(MODULES), re.M
)


def test_classifier_modules_models_workers_and_manifest_entries_stay(tmp_path):
    for name in MODULES:
        assert (VOICE / f"{name}.py").is_file(), name
        importlib.import_module(f"kollabor_voice.{name}")
    for name in MODELS:
        assert (VOICE / name).stat().st_size > 0, name
    spec = json.loads((VOICE / "manifest.json").read_text())["classifiers"]
    assert {"laya", "qwen"} <= set(spec)

    from kollabor_voice.laya import LayaWorker
    from kollabor_voice.qwen import QwenWorker

    for worker in (LayaWorker(tmp_path), QwenWorker(tmp_path)):
        assert worker.status()["state"] == "off" and worker.process is None


def test_voice_sources_do_not_import_the_classifier_modules():
    for path in VOICE_SOURCES:
        source = ROOT / path
        if source.exists():  # a lane may not have added every module yet
            assert not CLASSIFIER_IMPORT.search(source.read_text()), path


def test_importing_the_voice_screens_loads_no_classifier_module():
    code = (
        "import sys\n"
        "import plugins.voice_plugin, kollabor.panels.voice\n"
        "import kollabor.commands.system_commands.handlers.voicemode\n"
        "import kollabor_tui.status.core_widgets, kollabor_tui.status.layout_renderer\n"
        "import kollabor_voice.client, kollabor_voice.voice_context\n"
        "import kollabor_voice.speech_settings, kollabor_voice.observer\n"
        f"loaded = [m for m in {MODULES!r} if f'kollabor_voice.{{m}}' in sys.modules]\n"
        "assert not loaded, loaded\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr[-800:]


@pytest.fixture
def classifier_calls(monkeypatch):
    """Every way to run a classifier now fails the test and counts the call."""
    from kollabor_voice import classifiers, laya, qwen
    from kollabor_voice.client import VoiceClient

    calls = Mock(side_effect=AssertionError("voice reached the classifier"))
    for cls, names in (
        (classifiers.LocalClassifier, ("decide", "answer")),
        (classifiers.ProviderClassifier, ("decide",)),
        (laya.LayaWorker, ("prepare", "decide", "review_speech")),
        (qwen.QwenWorker, ("prepare", "decide", "answer")),
        (VoiceClient, ("review_speech",)),
    ):
        for name in names:
            monkeypatch.setattr(cls, name, calls)
    return calls


@pytest.mark.asyncio
async def test_a_tui_voice_turn_goes_to_the_agent_with_zero_classifier_calls(
    tmp_path, monkeypatch, classifier_calls
):
    from kollabor_voice.store import utc

    monkeypatch.setenv("KOLLAB_VOICE_ROOT", str(tmp_path))
    plugin = VoicePlugin(event_bus=FakeBus(), config={})
    plugin.requested = True
    plugin._lease = {"epoch": "e", "token": "t", "stream_id": "s"}
    plugin._client = SimpleNamespace(call=AsyncMock())
    now = time.time()
    plugin._pending = [
        {
            "event_id": "a",
            "text": "the build is red again",
            "started_at": utc(now - 2),
            "ended_at": utc(now - 1),
        }
    ]
    delivered = []

    async def deliver(data):
        delivered.append(data)
        plugin.requested = False
        return {"status": "queued"}

    plugin._deliver = deliver
    await asyncio.wait_for(plugin._deliver_loop(), 1)

    assert [d["message"] for d in delivered] == ["the build is red again"]
    # Only the ownership heartbeat went to the voice service: no prepare, no decision.
    assert [c.args[0] for c in plugin._client.call.await_args_list] == ["heartbeat"]
    assert classifier_calls.call_count == 0


@pytest.mark.asyncio
async def test_a_spoken_reply_is_gated_by_format_rules_alone(
    tmp_path, classifier_calls
):
    from kollabor_voice.client import VoiceClient

    client = VoiceClient(tmp_path)
    client.call = AsyncMock(return_value={"ids": ["x"]})
    assert await client.voice_out("Done.", "r", "epoch", "agent", "token") == {
        "ids": ["x"]
    }
    code = await client.voice_out("See `main.py`.", "r2", "epoch", "agent", "token")
    assert code["status"] == "rewrite"
    assert [c.args[0] for c in client.call.await_args_list] == ["voice_out"]
    assert classifier_calls.call_count == 0


@pytest.mark.asyncio
async def test_a_livekit_final_transcript_reaches_the_session_with_zero_classifier_calls(
    classifier_calls,
):
    # The engine's own router fixtures: a real runtime host, a fake Engine session.
    path = ROOT / "packages/kollabor-engine/tests/test_voice_session_router.py"
    spec = importlib.util.spec_from_file_location("engine_voice_router_fixtures", path)
    fixtures = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fixtures)

    _, _, router, binding, session = await fixtures.make_router()
    message = fixtures.transcript(binding)
    receipt = await router.admit_final(binding, message)

    assert receipt.accepted and receipt.queued
    assert [sent["message"] for sent in session.sent] == [message.display_text]
    assert classifier_calls.call_count == 0


def test_the_voice_prompt_drops_classifier_wording_but_keeps_the_lone_period_rule():
    from kollabor_voice.voice_context import VOICE_INSTRUCTIONS

    assert "classif" not in VOICE_INSTRUCTIONS.lower()
    # The model may still stay silent on echo or background speech.
    assert "echo, background speech, or not a request for you" in VOICE_INSTRUCTIONS
    assert "return exactly one period (.)" in VOICE_INSTRUCTIONS
    assert "A lone period is silent." in VOICE_INSTRUCTIONS


def test_status_hides_the_classifier_an_older_voice_service_still_reports():
    plugin = VoicePlugin(event_bus=FakeBus(), config={})
    plugin._state = {
        "state": "listening",
        "classifier": {"state": "ready", "name": "laya"},
    }
    assert "classifier" not in plugin.status()
