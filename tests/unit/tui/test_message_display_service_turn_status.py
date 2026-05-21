"""Tests for compact turn timing and provider reasoning rows."""

from unittest.mock import Mock

from kollabor_tui.message_display_service import MessageDisplayService


class DummyRenderer:
    """Minimal renderer shell for MessageDisplayService."""

    def __init__(self, pipe_mode: bool = False):
        self.pipe_mode = pipe_mode
        self.message_coordinator = Mock()


def _messages_from(service: MessageDisplayService) -> list[tuple[str, str, dict]]:
    return service.message_coordinator.display_message_sequence.call_args.args[0]


def test_turn_status_inserts_spacing_without_visible_timing():
    renderer = DummyRenderer()
    service = MessageDisplayService(renderer)

    service.display_complete_response(
        thinking_duration=0.5,
        response="done",
    )

    messages = _messages_from(service)

    assert messages[0] == ("spacer", "", {})
    assert messages[1] == ("assistant", "done", {})


def test_turn_status_hides_reasoning_preview_for_now():
    renderer = DummyRenderer()
    service = MessageDisplayService(renderer)

    service.display_complete_response(
        thinking_duration=4.4,
        response="done",
        thinking_content=["checked queue processor", "found provider reasoning"],
    )

    messages = _messages_from(service)

    assert messages[0] == ("spacer", "", {})


def test_turn_status_suppressed_in_pipe_mode():
    renderer = DummyRenderer(pipe_mode=True)
    service = MessageDisplayService(renderer)

    service.display_complete_response(
        thinking_duration=4.4,
        response="done",
        thinking_content="available",
    )

    messages = _messages_from(service)

    assert messages == [("assistant", "done", {})]
