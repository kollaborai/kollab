"""ACP uses the same atomic terminal display boundary as native responses."""

import asyncio
from types import SimpleNamespace

import pytest

from kollabor.llm.acp_orchestrator import ACPAdmission, ACPError, ACPOrchestrator
from kollabor_tui.display_tap import DisplayTap
from kollabor_tui.message_coordinator import MessageDisplayCoordinator
from kollabor_tui.message_display_service import MessageDisplayService


@pytest.fixture
def projection(tmp_path, monkeypatch):
    monkeypatch.setattr("kollabor_config.config_utils.get_project_data_dir", lambda: tmp_path)
    tap = DisplayTap()
    raw = []
    messages = []
    thinking = []
    terminal = SimpleNamespace(
        pipe_mode=False, writing_messages=False, input_line_written=False, last_line_count=0,
        terminal_state=SimpleNamespace(write_raw=raw.append),
        clear_active_area=lambda: None, invalidate_render_cache=lambda: None,
    )
    coordinator = MessageDisplayCoordinator(terminal, renderer=SimpleNamespace())
    coordinator._display_tap = tap
    def render_message(message_type, content, kwargs):
        # The real coordinator must own the terminal before a transcript write.
        assert terminal.writing_messages or terminal.pipe_mode
        messages.append((message_type, content, kwargs))
    coordinator._display_single_message = render_message
    coordinator._request_render_after_display = lambda _reason: None
    terminal.message_coordinator = coordinator
    terminal.update_thinking = lambda active, message="": thinking.append((active, message))
    display = MessageDisplayService(terminal)
    services = {"display_tap": tap, "llm_service": SimpleNamespace(message_display_service=display)}
    bus = SimpleNamespace(get_service=services.get, register_service=services.__setitem__)
    orchestrator = ACPOrchestrator(bus)
    orchestrator._runner = SimpleNamespace(session_id="owned-session", generation=1, cancel_timeout=0.01)
    return SimpleNamespace(orchestrator=orchestrator, terminal=terminal, display=display,
                           tap=tap, messages=messages, thinking=thinking, raw=raw)


def update(projection, kind, *, request="owned-request", turn=1, **fields):
    projection.orchestrator._on_runner_update({
        "kind": kind, "request_id": request, "turn_id": turn,
        "source": "cli", "thread_id": "owned-thread", **fields,
    })


@pytest.mark.parametrize("pipe_mode", [False, True])
def test_text_is_previewed_then_displayed_once_through_real_coordinator(projection, pipe_mode):
    projection.terminal.pipe_mode = pipe_mode
    update(projection, "turn_start", generation=1)
    update(projection, "text", text="First complete line.\n")
    update(projection, "text", text="Last line without a newline.")
    # Tokens still reach Web/ACP subscribers immediately with their exact owner.
    tokens = [event for event in projection.tap.get_snapshot() if event["type"] == "token"]
    assert [event["text"] for event in tokens] == ["First complete line.\n", "Last line without a newline."]
    assert all(event["request_id"] == "owned-request" and event["turn_id"] == 1 for event in tokens)
    assert not any("First complete line." in text for text in projection.raw)
    assert not projection.messages
    if not pipe_mode:
        assert projection.thinking[-1] == (True, "First complete line.\nLast line without a newline.")
    update(projection, "turn_finished")
    update(projection, "turn_finished")  # a late duplicate must not print again
    assert projection.messages == [("assistant", "First complete line.\nLast line without a newline.", {})]
    assert not projection.display.is_streaming_active()
    assert not any(event["type"] == "stream_chunk" for event in projection.tap.get_snapshot())
    assistant_events = [event for event in projection.tap.get_snapshot()
                        if event["type"] == "message" and event["message_type"] == "assistant"]
    assert len(assistant_events) == 1  # also supplies atomic catch-up to an attached TUI


def test_late_finish_does_not_clear_or_mix_the_next_request_preview(projection):
    update(projection, "turn_start", generation=1)
    update(projection, "text", text="Stopped owner's partial.")
    update(projection, "turn_finished")
    update(projection, "turn_start", request="next-request", turn=2, generation=2)
    update(projection, "text", request="next-request", turn=2, text="Next owner's answer.")
    update(projection, "turn_finished")
    assert projection.display.is_streaming_active()
    assert projection.thinking[-1] == (True, "Next owner's answer.")
    update(projection, "turn_finished", request="next-request", turn=2)
    assert [(kind, text) for kind, text, _kwargs in projection.messages] == [
        ("assistant", "Stopped owner's partial."), ("assistant", "Next owner's answer."),
    ]


@pytest.mark.asyncio
async def test_correlated_request_error_is_visible_once_without_raw_stderr(projection):
    future = asyncio.get_running_loop().create_future()
    future.set_exception(ACPError("Owned provider refused the request", code=-32001))
    admission = ACPAdmission("owned-request", "cli", "owned-thread", 1, 1, "owned input", future, {})
    result = await projection.orchestrator._complete(admission)
    assert result["stop_reason"] == "error"
    assert projection.messages == [("error", "Error: Owned provider refused the request", {})]
    error = next(event for event in projection.tap.get_snapshot() if event["type"] == "error")
    assert error["request_id"] == "owned-request" and error["code"] == -32001
