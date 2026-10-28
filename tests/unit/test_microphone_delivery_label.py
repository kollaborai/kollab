"""The mic widget shows whether heard words reach the agent."""

from types import SimpleNamespace

import pytest

from kollabor_tui.status.core_widgets import (
    WidgetContext,
    render_microphone,
    voice_delivery_label,
)
from kollabor_tui.status.utils import strip_ansi


@pytest.mark.parametrize(
    "state, label",
    [
        ({}, ""),
        ({"pending": 2}, "sending 2 pending"),
        ({"observer_error": "AI connection lost", "pending": 5}, "stopped 5 pending"),
        ({"observer_error": "AI connection lost"}, "stopped"),
    ],
)
def test_voice_delivery_label(state, label):
    assert voice_delivery_label(state) == label


def _ctx(state):
    bus = SimpleNamespace(get_service=lambda name: SimpleNamespace(status=lambda: state))
    return WidgetContext(event_bus=bus)


def test_microphone_widget_shows_stopped_delivery_before_the_transcript():
    state = {
        "state": "listening",
        "observer_error": "AI connection lost",
        "pending": 1,
        "last_transcript": {"text": "Hello, can you hear me?"},
    }
    text = strip_ansi(render_microphone(80, _ctx(state)))
    assert text == "mic listening · stopped 1 pending: Hello, can you hear me?"


def test_microphone_widget_off_hides_delivery():
    assert strip_ansi(render_microphone(80, _ctx({"state": "off", "pending": 3}))) == "mic off"
