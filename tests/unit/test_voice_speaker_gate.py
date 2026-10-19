"""Speaker gate and echo cancellation in the chat: what reaches the agent, and the screens."""

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from kollabor_voice.classifiers import load_preferences
from kollabor_voice.store import utc

from kollabor.commands.system_commands.handlers.voicemode import VoiceModeCommandHandler
from kollabor.panels.voice import VoicePanel, voice_sections, voiceprint_text
from plugins.altview.voice_classifier_altview import _word, detail_lines
from plugins.voice_plugin import VoicePlugin


def line(number, text, speaker=None, **fields):
    record = {
        "event_id": str(number),
        "seq": number,
        "text": text,
        "owner_epoch": "owner",
        "stream_id": "stream",
        "started_at": utc(time.time() - 25 + number * 5),
        "ended_at": utc(time.time() - 24 + number * 5),
        **fields,
    }
    if speaker:
        record["speaker"] = speaker
    return record


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    monkeypatch.setenv("KOLLAB_VOICE_ROOT", str(tmp_path))
    value = VoicePlugin(
        event_bus=SimpleNamespace(get_service=lambda _: None), config={}
    )
    value._lease = {"epoch": "owner", "token": "token", "stream_id": "stream"}
    value._client = SimpleNamespace(call=AsyncMock(return_value={}))
    return value


@pytest.mark.asyncio
async def test_gated_lines_never_reach_the_agent(plugin):
    tv = line(1, "Breaking news tonight.", "other_speaker", speaker_score=0.12)
    reading = line(2, "On quiet mornings I make coffee.", "enrolling")
    request = line(3, "Can you check the tests?", "user", speaker_score=0.81)
    observed, delivered = [], []
    plugin.classifier_name = "provider"
    plugin.requested = True
    plugin._pending = [tv, reading, request]

    async def observe(records, transcript_context):
        observed.append((records, list(transcript_context)))
        return records

    async def deliver(data):
        delivered.append(data)
        plugin.requested = False
        return {"status": "queued"}

    plugin._observe_with_provider = observe
    plugin._deliver = deliver
    async with asyncio.timeout(2):
        await plugin._observe_loop()
    # Only the user's words are judged; the TV line is context, the reading is not.
    assert observed == [([request], [tv])]
    assert [d["message"] for d in delivered] == [request["text"]]
    assert plugin._recent_transcripts == [tv, request]
    words = {_word(entry): entry["records"] for entry in plugin.classifier_log()}
    assert words["not you"] == [tv] and words["enroll"] == [reading]


def test_classifier_screen_explains_the_gate():
    entry = {
        "classifier": "laya",
        "records": [
            line(1, "Breaking news tonight.", "other_speaker", speaker_score=0.12)
        ],
        "other_speaker": True,
    }
    rows = detail_lines(entry)
    assert ("warn", "  voice: another speaker (0.12)") in rows
    assert ("dim", "  The speaker gate heard another voice: kept as context.") in rows
    assert rows[-1] == ("dim", "  nothing")  # sent to the agent
    heard = line(2, "Can you check the tests?", "user", speaker_score=0.81)
    heard["echo_cancellation"] = True
    rows = detail_lines({"classifier": "laya", "records": [heard], "decision": None})
    assert ("good", "  voice: yours (0.81) · echo cancelled") in rows


@pytest.mark.asyncio
async def test_settings_are_off_by_default_and_saved_for_the_device(plugin, tmp_path):
    fields = {f["path"]: f for s in voice_sections(plugin) for f in s["fields"]}
    assert not fields["echo_cancellation"]["value"]
    assert not fields["speaker_gate"]["value"]
    assert fields["speaker_threshold"]["value"] == 0.45
    assert (
        fields["voiceprint"]["value"] == "None yet"
        and not fields["voiceprint"]["editable"]
    )
    state = SimpleNamespace(_event_bus=SimpleNamespace(get_service=lambda _: plugin))
    result = await VoicePanel().act(
        state,
        "save",
        {
            "changes": {"speaker_gate": True, "speaker_threshold": 0.42},
            "target": "device",
        },
    )
    assert result["ok"]
    saved = load_preferences()
    assert (saved["speaker_gate"], saved["speaker_threshold"]) == (True, 0.42)
    bad = await VoicePanel().act(
        state, "save", {"changes": {"speaker_threshold": 1.5}, "target": "device"}
    )
    assert not bad["ok"] and load_preferences()["speaker_threshold"] == 0.42
    (tmp_path / "voiceprint.json").write_text(json.dumps({"seconds": 31}))
    assert voiceprint_text({}) == "Saved"
    assert voiceprint_text({"voiceprint": {"seconds": 31.4}}) == (
        "Saved from 31 seconds of speech"
    )
    assert voiceprint_text({"enrollment": {"seconds": 12, "target": 30}}) == (
        "Enrolling: 12 of 30 seconds"
    )


@pytest.mark.asyncio
async def test_voice_enroll_needs_voice_on_and_forget_works_without_it(
    plugin, tmp_path
):
    handler = VoiceModeCommandHandler(
        SimpleNamespace(register_command=lambda _: None),
        SimpleNamespace(get_service=lambda _: plugin),
    )
    plugin.requested = False
    result = await handler.handle_voicemode(SimpleNamespace(args=["enroll"]))
    assert not result.success and "/voice on" in result.message
    plugin.requested = True
    result = await handler.handle_voicemode(SimpleNamespace(args=["enroll"]))
    assert result.success and "30 seconds" in result.message
    assert "Kollab" not in result.message.split("\n\n")[1]  # the passage
    assert plugin._client.call.await_args.args == ("speaker_enroll",)
    plugin._client = None  # no voice service: the file is removed directly
    (tmp_path / "voiceprint.json").write_text("{}")
    result = await handler.handle_voicemode(SimpleNamespace(args=["enroll", "forget"]))
    assert result.success and result.message == "Voiceprint deleted"
    assert not (tmp_path / "voiceprint.json").exists()


def test_status_row_counts_enrollment_then_says_saved(plugin):
    listening = {"state": "listening", "level": 0.02}
    enrolling = {"enrollment": {"seconds": 12.4, "target": 30.0, "dropped": 0}}
    stage = plugin._activity({**listening, "speaker": enrolling})["stage"]
    assert stage == "enrolling 12/30s"
    saved = {"enrollment": None, "saved_at": time.time() - 3}
    assert (
        plugin._activity({**listening, "speaker": saved})["stage"] == "voiceprint saved"
    )
    saved["saved_at"] -= 20
    assert plugin._activity({**listening, "speaker": saved})["stage"] == "listening"
