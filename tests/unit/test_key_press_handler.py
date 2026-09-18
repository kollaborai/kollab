"""Focused lifecycle tests for KeyPressHandler background tasks."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from kollabor.commands.executor import SlashCommandExecutor
from kollabor.commands.parser import SlashCommandParser
from kollabor.commands.registry import SlashCommandRegistry
from kollabor.commands.system_commands.plugin import SystemCommandsPlugin
from kollabor_events.models import EventType
from kollabor_tui.buffer_manager import BufferManager
from kollabor_tui.input.key_press_handler import KeyPressHandler
from kollabor_tui.input.paste_processor import PasteProcessor
from kollabor_tui.key_parser import KeyParser

PNG_DATA_URL = "data:image/png;base64,cG5n"


def make_handler() -> KeyPressHandler:
    """Build a handler with dependency-only test doubles."""
    return KeyPressHandler(
        buffer_manager=object(),
        key_parser=object(),
        event_bus=object(),
        error_handler=object(),
        display_controller=object(),
        paste_processor=object(),
        renderer=object(),
    )


@pytest.mark.asyncio
async def test_background_task_failure_is_observed():
    handler = make_handler()

    async def fail():
        raise RuntimeError("hook failed")

    with patch(
        "kollabor_tui.input.key_press_handler.logger.exception"
    ) as log_exception:
        handler._create_background_task(fail())
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    assert not handler._background_tasks
    log_exception.assert_called_once_with("Key press background task failed")


@pytest.mark.asyncio
async def test_cleanup_cancels_pending_background_tasks():
    handler = make_handler()
    started = asyncio.Event()
    release = asyncio.Event()

    async def wait_for_release():
        started.set()
        await release.wait()

    handler._create_background_task(wait_for_release())
    await started.wait()
    assert len(handler._background_tasks) == 1

    await handler.cleanup()

    assert not handler._background_tasks


class _EventBus:
    def __init__(self):
        self.events = []

    async def emit_with_hooks(self, event_type, data, source):
        self.events.append((event_type, data, source))
        return {}


class _Renderer:
    input_buffer = ""

    def clear_active_area(self):
        pass


class _ErrorHandler:
    async def handle_error(self, *_args, **_kwargs):
        raise AssertionError("unexpected input error")


@pytest.mark.asyncio
async def test_ctrl_v_delegates_to_clipboard_paste_and_refreshes_display():
    paste_processor = PasteProcessor(BufferManager())
    paste_processor.handle_clipboard_paste = AsyncMock(return_value=True)
    display_controller = AsyncMock()
    event_bus = _EventBus()
    handler = KeyPressHandler(
        buffer_manager=paste_processor.buffer_manager,
        key_parser=KeyParser(),
        event_bus=event_bus,
        error_handler=_ErrorHandler(),
        display_controller=display_controller,
        paste_processor=paste_processor,
        renderer=_Renderer(),
    )

    await handler._handle_key_press(KeyParser().parse_char("\x16"))
    await handler.cleanup()

    paste_processor.handle_clipboard_paste.assert_awaited_once_with()
    display_controller.update_display.assert_awaited_once_with(force_render=True)


@pytest.mark.asyncio
async def test_enter_emits_cli_image_as_ordered_structured_message_parts():
    buffer = BufferManager()
    for char in "inspect ":
        assert buffer.insert_char(char)
    paste_processor = PasteProcessor(buffer)
    await paste_processor.add_image_attachment(PNG_DATA_URL)
    event_bus = _EventBus()
    handler = KeyPressHandler(
        buffer_manager=buffer,
        key_parser=KeyParser(),
        event_bus=event_bus,
        error_handler=_ErrorHandler(),
        display_controller=AsyncMock(),
        paste_processor=paste_processor,
        renderer=_Renderer(),
    )

    await handler._handle_enter()

    user_events = [
        data
        for event_type, data, _source in event_bus.events
        if event_type == EventType.USER_INPUT
    ]
    assert user_events == [
        {
            "message": [
                {"type": "text", "text": "inspect "},
                {"type": "image", "image": PNG_DATA_URL},
            ],
            "validation_errors": [],
        }
    ]
    assert buffer.content == ""
    assert paste_processor.has_image_attachments is False
    assert buffer.navigate_history("up")
    assert buffer.content == "inspect [image attachment omitted]"


@pytest.mark.asyncio
async def test_pasted_slash_command_is_dispatched_after_paste_expansion():
    """Pasted local commands must not become ordinary model turns."""
    buffer = BufferManager()
    paste_processor = PasteProcessor(buffer)
    paste_processor._paste_bucket["PASTE_1"] = "/artifact open img_1234567890abcdef"
    await paste_processor.create_paste_placeholder("PASTE_1")

    class ArtifactApi:
        def __init__(self):
            self.opened_media_id = None

        def open_generated_artifact(self, media_id):
            self.opened_media_id = media_id
            return True

    api_service = ArtifactApi()

    class CommandEventBus(_EventBus):
        def get_service(self, name):
            if name == "llm_service":
                return SimpleNamespace(api_service=api_service)
            return None

    event_bus = CommandEventBus()
    registry = SlashCommandRegistry()
    SystemCommandsPlugin(
        command_registry=registry, event_bus=event_bus
    ).register_commands()
    command_mode_handler = SimpleNamespace(
        slash_parser=SlashCommandParser(),
        command_executor=SlashCommandExecutor(registry),
    )
    handler = KeyPressHandler(
        buffer_manager=buffer,
        key_parser=KeyParser(),
        event_bus=event_bus,
        error_handler=_ErrorHandler(),
        display_controller=AsyncMock(),
        paste_processor=paste_processor,
        renderer=_Renderer(),
        command_mode_handler=command_mode_handler,
    )

    await handler._handle_enter()

    assert api_service.opened_media_id == "img_1234567890abcdef"
    assert paste_processor.paste_bucket == {}
    assert not any(
        event_type == EventType.USER_INPUT for event_type, _, _ in event_bus.events
    )
