"""Prior microphone lines inform decisions without becoming fresh user requests."""

import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from kollabor_voice.speech_settings import load_speech_preferences, save_speech_preferences
from kollabor_voice.store import utc

from kollabor.commands.system_commands.handlers.voicemode import VoiceModeCommandHandler
from plugins.voice_plugin import VoicePlugin


def record(number, text, epoch="owner"):
    return {
        "event_id": str(number),
        "seq": number,
        "text": text,
        "owner_epoch": epoch,
        "stream_id": "stream",
        "started_at": utc(time.time() - 25 + number * 5),
        "ended_at": utc(time.time() - 24 + number * 5),
    }


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    monkeypatch.setenv("KOLLAB_VOICE_ROOT", str(tmp_path))
    value = VoicePlugin(
        event_bus=SimpleNamespace(get_service=lambda _: None), config={}
    )
    value._lease = {"epoch": "owner", "token": "token", "stream_id": "stream"}
    value._client = SimpleNamespace(call=AsyncMock())
    return value


def test_context_window_excludes_candidates_future_and_other_capture(plugin):
    past = [record(i, f"Line {i}.") for i in range(1, 14)]
    current = record(14, "Hello.")
    plugin._recent_transcripts = [
        *past,
        current,
        record(15, "Future line."),
        record(13, "Another owner.", epoch="different"),
        {**record(13, "Another stream."), "stream_id": "different"},
    ]
    assert plugin._transcript_context_for([current]) == past[-10:]
    plugin.set_context_lines(3)
    assert plugin._transcript_context_for([current]) == past[-3:]
    assert load_speech_preferences()["context_lines"] == 3


@pytest.mark.asyncio
async def test_stop_clears_transcript_context_but_a_settings_change_preserves_it(plugin):
    plugin._recent_transcripts = [record(1, "The TV is on.")]
    await plugin.configure_voice({"context_lines": 20})
    assert plugin._recent_transcripts
    await plugin.stop_voice()
    assert not plugin._recent_transcripts


def _heard(text, *spoken):
    return {
        "text": text,
        "playback_overlap": [{"reply_id": "r", "text": s} for s in spoken],
    }


@pytest.mark.parametrize(
    "text, spoken",
    [
        ("Yes, I can hear you.", ["Yes, I can hear you."]),
        ("yes i can hear you", ["Yes, I can hear you."]),
        ("I can hear you", ["Yes, I can hear you."]),  # a chunk of the reply
        ("Yes I can here you", ["Yes, I can hear you."]),  # one transcription slip
        (
            "Yes, I can hear you. Anything else?",
            ["Yes, I can hear you.", "Anything else?"],
        ),
        ("Okay.", ["Okay."]),
        ("I'll handle it here.", ["I’ll handle it here."]),  # curly apostrophe
    ],
)
def test_own_playback_picked_up_by_the_mic_is_echo(text, spoken):
    from kollabor_voice.observer import is_playback_echo

    assert is_playback_echo(_heard(text, *spoken))


@pytest.mark.parametrize(
    "text, spoken",
    [
        ("Hello, can you hear me?", ["Yes, I can hear you."]),  # the human, same room
        ("Stop.", ["I will stop the music now."]),  # barge-in on a word the reply says
        ("Run the unit tests please", ["Do you want me to check the build?"]),
        ("Hello, can you hear me?", []),  # nothing was playing
    ],
)
def test_the_humans_words_over_playback_are_not_echo(text, spoken):
    from kollabor_voice.observer import is_playback_echo

    assert not is_playback_echo(_heard(text, *spoken))


@pytest.mark.asyncio
async def test_context_command_defaults_validation_persistence_and_no_activation(
    plugin, tmp_path
):
    handler = VoiceModeCommandHandler(
        None, SimpleNamespace(get_service=lambda _: plugin)
    )
    assert (
        "10"
        in (await handler.handle_voicemode(SimpleNamespace(args=["context"]))).message
    )
    save_speech_preferences({"kokoro_voice": "bf_emma"})
    for argument in ("0", "51", "invalid"):
        result = await handler.handle_voicemode(
            SimpleNamespace(args=["context", argument])
        )
        assert not result.success
    result = await handler.handle_voicemode(SimpleNamespace(args=["context", "20"]))
    assert result.success and not plugin.requested
    assert load_speech_preferences()["context_lines"] == 20
    assert (
        json.loads((tmp_path / "preferences.json").read_text())["kokoro_voice"]
        == "bf_emma"
    )
    for invalid in (True, None, 0, 51):
        with pytest.raises(ValueError):
            save_speech_preferences({"context_lines": invalid})


def test_talking_over_playback_keeps_only_the_human_words():
    from kollabor_voice.observer import strip_playback

    overlap = [{"text": "Bear with me, still looking into it.", "reply_id": "r"}]
    record = {
        "text": "Bear with me still looking into- Hello, can you hear me?",
        "playback_overlap": overlap,
    }
    assert strip_playback(record) == "Hello, can you hear me?"
    # Two shared words are not an echo run: "can hear" stays the human's.
    mine = {
        "text": "Can you hear me?",
        "playback_overlap": [{"text": "I can hear you."}],
    }
    assert strip_playback(mine) == "Can you hear me?"
    assert strip_playback({"text": "Run the tests."}) == "Run the tests."
