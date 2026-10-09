"""A minimal-exit row only skips the input redraw when its hook returns output."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from kollabor_tui.input.modal_controller import ModalController


def _press(name, command, final_data):
    renderer = SimpleNamespace(
        _search_active=False,
        _handle_widget_navigation=lambda key: False,
        _handle_widget_input=lambda key: True,
        was_command_selected=lambda: True,
        get_selected_command=lambda: dict(command),
    )
    event_bus = MagicMock()
    event_bus.emit_with_hooks = AsyncMock(return_value={"main": {"final_data": final_data}})
    controller = ModalController(
        renderer=MagicMock(),
        event_bus=event_bus,
        config=MagicMock(),
        status_modal_renderer=MagicMock(),
        update_display_callback=AsyncMock(),
        exit_command_mode_callback=AsyncMock(),
    )
    controller.modal_renderer = renderer
    controller._exit_modal_mode = AsyncMock()
    controller._exit_modal_mode_minimal = AsyncMock()
    asyncio.run(controller._handle_modal_keypress(SimpleNamespace(name=name, char="")))
    return controller


RESUME_ROW = {"name": "a chat", "session_id": "s1", "action": "resume_session", "exit_mode": "minimal"}


def test_escape_on_a_resume_row_redraws_the_input_box():
    controller = _press("Escape", {**RESUME_ROW, "action": "cancel"}, {})
    controller._exit_modal_mode.assert_awaited_once()
    controller._exit_modal_mode_minimal.assert_not_awaited()


def test_a_resume_that_loaded_nothing_redraws_the_input_box():
    controller = _press("Enter", RESUME_ROW, {})
    controller._exit_modal_mode.assert_awaited_once()
    controller._exit_modal_mode_minimal.assert_not_awaited()


def test_a_resume_with_output_keeps_the_minimal_exit():
    controller = _press("Enter", RESUME_ROW, {"display_messages": [("system", "resumed", {})]})
    controller._exit_modal_mode_minimal.assert_awaited_once()
    controller._exit_modal_mode.assert_not_awaited()
