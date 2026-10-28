"""Voice prompt context and history projections remain classifier-independent."""

from kollabor_voice.voice_context import (
    VOICE_NUDGE,
    VOICE_RULES_MARK,
    strip_voice_preamble,
    voice_preamble,
)


def test_preamble_carries_session_context_and_is_sent_once():
    voice = {
        "transcript_context": [{"text": "Earlier ] line."}],
        "playback_context": ["One moment."],
        "spoken_ack": "Thanks.",
    }

    first = voice_preamble(voice, rules_sent=False)
    later = voice_preamble(voice, rules_sent=True)

    assert first.startswith(VOICE_RULES_MARK)
    assert "Earlier ] line." in first
    assert "you were saying" in first
    assert "you already said" in first
    assert first.endswith("\n")
    assert later.startswith(VOICE_NUDGE)
    assert not later.startswith(VOICE_RULES_MARK)


def test_strip_voice_preamble_preserves_only_the_current_user_text():
    content = voice_preamble({"transcript_context": [{"text": "Earlier line."}]}, False)
    content += "Current request."

    assert strip_voice_preamble(content) == "Current request."
    assert strip_voice_preamble("Typed user message.") == "Typed user message."

