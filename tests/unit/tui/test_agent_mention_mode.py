"""Tests for the TUI Hub agent mention menu and direct submission path."""

import asyncio

from kollabor_events.models import CommandMode, EventType
from kollabor_tui.input.command_mode_handler import CommandModeHandler
from kollabor_tui.input.key_press_handler import KeyPressHandler


class BufferManager:
    def __init__(self, content: str = ""):
        self.content = content
        self.history = []

    @property
    def is_empty(self):
        return not self.content

    def insert_char(self, char: str):
        self.content += char

    def delete_char(self):
        self.content = self.content[:-1]

    def clear(self):
        self.content = ""

    def validate_content(self):
        return []

    def get_content_and_clear(self):
        content = self.content
        self.clear()
        return content

    def add_to_history(self, message: str):
        self.history.append(message)


class AgentStateService:
    async def list_hub_agents(self):
        return [
            {
                "identity": "lapis",
                "status": "offline",
                "description": "calm mediator",
            },
            {
                "identity": "zicron",
                "status": "online",
                "state": "waiting",
            },
        ]


class EventBus:
    def __init__(self):
        self.events = []
        self.state_service = AgentStateService()

    def get_service(self, name):
        return self.state_service if name == "state_service" else None

    async def emit_with_hooks(self, event_type, data, source):
        self.events.append((event_type, data, source))
        return {}


class MenuRenderer:
    def __init__(self):
        self.menu_items = []
        self.hidden = False

    def show_command_menu(self, commands, _filter_text):
        self.menu_items = [dict(command, is_subcommand=False) for command in commands]

    def filter_commands(self, commands, _filter_text):
        self.menu_items = [dict(command, is_subcommand=False) for command in commands]

    def set_selected_index(self, _index):
        pass

    def get_selected_command(self):
        return self.menu_items[0] if self.menu_items else None

    def hide_menu(self):
        self.hidden = True


def test_at_menu_filters_and_leaves_selected_target_in_input():
    async def scenario():
        buffer = BufferManager()
        menu = MenuRenderer()
        handler = CommandModeHandler(
            buffer_manager=buffer,
            renderer=None,
            event_bus=EventBus(),
            command_registry=None,
            command_executor=None,
            command_menu_renderer=menu,
            slash_parser=None,
        )

        await handler.enter_agent_mention_mode()
        assert buffer.content == "@"
        assert {item["name"] for item in menu.menu_items} == {"lapis", "zicron"}

        for char in "zicron":
            await handler.handle_agent_mention_input(char)
        assert [item["name"] for item in menu.menu_items] == ["zicron"]

        await handler.handle_agent_mention_input(" ")
        assert buffer.content == "@zicron "
        assert handler.command_mode == CommandMode.NORMAL
        assert handler.agent_mention_active is False
        assert menu.hidden is True

    asyncio.run(scenario())


def test_leading_at_opens_agent_menu_callback_before_normal_character_handling():
    async def scenario():
        opened = False

        async def open_menu():
            nonlocal opened
            opened = True

        handler = KeyPressHandler(
            buffer_manager=BufferManager(),
            key_parser=None,
            event_bus=EventBus(),
            error_handler=ErrorHandler(),
            display_controller=DisplayController(),
            paste_processor=PasteProcessor(),
            renderer=Renderer(),
        )
        handler.set_callbacks(enter_agent_mention_mode=open_menu)

        await handler.process_character("@")

        assert opened is True
        assert handler.buffer_manager.content == ""

    asyncio.run(scenario())


class StateService:
    def __init__(self):
        self.sent = []

    async def send_hub_user_message(self, target, content):
        self.sent.append((target, content))
        return "sent to zicron as malmazan from koordinator"


class SubmitEventBus:
    def __init__(self):
        self.state_service = StateService()
        self.events = []

    def get_service(self, name):
        return self.state_service if name == "state_service" else None

    async def emit_with_hooks(self, event_type, data, source):
        self.events.append((event_type, data, source))
        return {}


class PasteProcessor:
    paste_detection_enabled = False
    paste_bucket = {}

    def expand_paste_placeholders(self, message):
        return message


class Renderer:
    input_buffer = ""

    def clear_active_area(self):
        pass


class DisplayController:
    async def update_display(self, **_kwargs):
        pass


class ErrorHandler:
    async def handle_error(self, *_args, **_kwargs):
        raise AssertionError("unexpected input error")


def test_direct_at_message_uses_state_service_and_skips_user_input_event():
    async def scenario():
        event_bus = SubmitEventBus()
        handler = KeyPressHandler(
            buffer_manager=BufferManager("@zicron Please work on x."),
            key_parser=None,
            event_bus=event_bus,
            error_handler=ErrorHandler(),
            display_controller=DisplayController(),
            paste_processor=PasteProcessor(),
            renderer=Renderer(),
        )

        await handler._handle_enter()

        assert event_bus.state_service.sent == [("zicron", "Please work on x.")]
        assert any(
            event_type == EventType.COMMAND_OUTPUT_DISPLAY
            for event_type, _, _ in event_bus.events
        )
        assert not any(
            event_type == EventType.USER_INPUT for event_type, _, _ in event_bus.events
        )

    asyncio.run(scenario())
