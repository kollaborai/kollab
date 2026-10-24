import json
import os
import subprocess
import sys

import pytest
from kollabor_voice.observer import voice_preamble

from kollabor_ai.response_channels import (
    display_response_text,
    display_stream_text,
    turn_display_texts,
)
from kollabor_ai.response_parser import ResponseParser


def test_display_code_and_example_tools_never_become_speech_or_actions():
    parser = ResponseParser()
    display = (
        'Example:\n```python\nprint("hello")\n```\n<terminal>example only</terminal>'
    )
    raw = (
        f"<display_text>{display}</display_text>"
        "<spoken_text>The example is on screen.</spoken_text><terminal>pwd</terminal>"
    )
    result = parser.parse_response(raw)
    assert result["display_text"] == display
    assert result["spoken_text"] == "The example is on screen."
    assert [t["command"] for t in parser.get_all_tools(result)] == ["pwd"]


@pytest.mark.parametrize("container", ["think", "terminal", "tool_call"])
def test_spoken_tags_inside_thought_or_tools_are_not_narration(container):
    result = ResponseParser().parse_response(
        f"<{container}><spoken_text>private content</spoken_text></{container}>"
    )
    assert result["spoken_text"] is None


def test_code_example_of_channels_remains_display_data():
    text = "```xml\n<spoken_text>Example only</spoken_text>\n```"
    result = ResponseParser().parse_response(text)
    assert result["content"] == text
    assert result["spoken_text"] is None


@pytest.mark.parametrize("spoken", ["", "."])
def test_explicit_silence_does_not_fall_back_to_display(spoken):
    result = ResponseParser().parse_response(
        f"<display_text>Long visible explanation.</display_text><spoken_text>{spoken}</spoken_text>"
    )
    assert result["spoken_text"] == spoken
    assert result["content"] == "Long visible explanation."


def test_incomplete_or_duplicate_narration_is_silent():
    for text in (
        "<display_text>Visible</display_text><spoken_text>unfinished",
        "<spoken_text>One</spoken_text><spoken_text>Two</spoken_text>",
    ):
        assert ResponseParser().parse_response(text)["spoken_text"] == ""


def test_complete_replay_hides_narration_and_preserves_comparisons_and_examples():
    raw = (
        "<display_text>```xml\n<spoken_text>Example</spoken_text>\n```</display_text>"
        "<spoken_text>Hidden narration.</spoken_text>"
    )
    assert display_response_text(raw) == "```xml\n<spoken_text>Example</spoken_text>\n```"
    assert display_response_text("Check whether x < y") == "Check whether x < y"


def test_streaming_every_character_keeps_channels_separate_without_tag_flicker():
    raw = (
        "<display_text>Example:\n```xml\n<spoken_text>literal</spoken_text>\n```\n</display_text>"
        "<spoken_text>Audio only.</spoken_text>"
    )
    previous = ""
    for size in range(1, len(raw) + 1):
        shown = display_stream_text(raw[:size])
        assert shown.startswith(previous), (size, previous, shown)
        assert "Audio only" not in shown
        assert "<display_text>" not in shown
        previous = shown
    assert previous == "Example:\n```xml\n<spoken_text>literal</spoken_text>\n```\n"


def test_turn_display_texts_projects_voice_user_and_assistant_channels():
    voice = {"event_ids": ["turn-1"]}
    messages = [
        {
            "role": "user",
            "content": voice_preamble(voice, rules_sent=False) + "Check the tests.",
            "metadata": {"voice": voice},
        },
        {
            "role": "assistant",
            "content": "<display_text>Visible answer.</display_text>"
            "<spoken_text>Spoken answer.</spoken_text>",
            "metadata": {},
        },
        {"role": "assistant", "content": ".", "metadata": {}},
        {"role": "user", "content": "Typed request.", "metadata": {}},
        {"role": "assistant", "content": "Plain answer.", "metadata": {}},
    ]

    assert turn_display_texts(messages) == [
        "Check the tests.",
        "Visible answer.",
        "",
        None,
        None,
    ]


# Runs in a fresh interpreter where kollabor_voice cannot be imported at all, so
# no earlier import in this process can hide a dependency on it.
_NO_VOICE_PROBE = """
import importlib.abc, json, sys

class _NoVoice(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name == "kollabor_voice" or name.startswith("kollabor_voice."):
            raise ImportError("kollabor_voice is not installed")

sys.meta_path.insert(0, _NoVoice())
from kollabor_ai.response_channels import turn_display_texts

assert "kollabor_voice" not in sys.modules
print(json.dumps(turn_display_texts(json.loads(sys.argv[1]))))
"""


def _turn_display_texts_without_voice(messages):
    result = subprocess.run(
        [sys.executable, "-c", _NO_VOICE_PROBE, json.dumps(messages)],
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_turn_display_texts_without_voice_renders_plain_turns():
    messages = [
        {"role": "user", "content": "Typed request.", "metadata": {}},
        {
            "role": "assistant",
            "content": "<display_text>Visible.</display_text><spoken_text>Spoken.</spoken_text>",
            "metadata": {},
        },
        {"role": "assistant", "content": "Plain.", "metadata": {}},
    ]

    assert _turn_display_texts_without_voice(messages) == [None, "Visible.", None]


def test_turn_display_texts_without_voice_shows_a_voice_turn_as_stored():
    voice = {"event_ids": ["turn-1"]}
    stored = voice_preamble(voice, rules_sent=False) + "Check the tests."
    messages = [{"role": "user", "content": stored, "metadata": {"voice": voice}}]

    assert _turn_display_texts_without_voice(messages) == [stored]


def test_turn_display_texts_strips_the_preamble_when_voice_is_importable():
    voice = {"event_ids": ["turn-1"]}
    content = voice_preamble(voice, rules_sent=False) + "Check the tests."
    messages = [{"role": "user", "content": content, "metadata": {"voice": voice}}]

    assert turn_display_texts(messages) == ["Check the tests."]
