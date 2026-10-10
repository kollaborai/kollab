"""The mic widget and the reserved voice lines show what the classifier did."""

from types import SimpleNamespace

import pytest

from kollabor_tui.status.core_widgets import (
    WidgetContext,
    render_microphone,
    voice_classifier_label,
)
from kollabor_tui.status.utils import strip_ansi


@pytest.mark.parametrize(
    "extra, label",
    [
        ({}, "laya ready"),
        ({"last_decision": {"decision": "respond"}}, "laya sent"),
        ({"last_decision": {"decision": "ignore"}}, "laya ignored"),
        ({"last_decision": {"decision": "defer"}, "pending": 2}, "laya unsure 2 pending"),
        ({"observing": True}, "laya deciding"),
        ({"observer_error": "Laya request stopped", "pending": 5}, "laya stopped 5 pending"),
        ({"classifier_name": "provider"}, "ai ready"),
        ({"classifier_name": ""}, ""),
    ],
)
def test_voice_classifier_label(extra, label):
    assert voice_classifier_label({"classifier_name": "laya", **extra}) == label


def _ctx(state):
    bus = SimpleNamespace(get_service=lambda name: SimpleNamespace(status=lambda: state))
    return WidgetContext(event_bus=bus)


def test_microphone_widget_shows_classifier_before_transcript():
    state = {
        "state": "listening",
        "classifier_name": "laya",
        "last_decision": {"decision": "ignore"},
        "last_transcript": {"text": "Hello, can you hear me?"},
    }
    text = strip_ansi(render_microphone(80, _ctx(state)))
    assert text == "mic listening · laya ignored: Hello, can you hear me?"


def test_microphone_widget_off_hides_classifier():
    text = strip_ansi(render_microphone(80, _ctx({"state": "off", "classifier_name": "laya"})))
    assert text == "mic off"
