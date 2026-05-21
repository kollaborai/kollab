"""Tests for slash command execution from the popup menu."""

from kollabor.commands.registry import SlashCommandRegistry
from kollabor.commands.system_commands.plugin import SystemCommandsPlugin
from kollabor_events.models import CommandResult
from kollabor_tui.input.command_mode_handler import CommandModeHandler


class BufferManager:
    def __init__(self, content: str):
        self.content = content
        self.history = []

    def clear(self):
        self.content = ""

    def insert_char(self, char: str):
        self.content += char

    def add_to_history(self, command: str):
        self.history.append(command)


class EventBus:
    def get_service(self, name):
        return None

    async def emit_with_hooks(self, *args, **kwargs):
        return {}


class CommandMenuRenderer:
    def __init__(self, selected):
        self.selected = selected
        self.hidden = False

    def get_selected_command(self):
        return self.selected

    def hide_menu(self):
        self.hidden = True


class CommandExecutor:
    def __init__(self):
        self.command = None

    async def execute_command(self, command, event_bus):
        self.command = command
        return CommandResult(success=True, message="")


class CommandRegistry:
    def get_command(self, name):
        return object() if name == "mode" else None


class SlashParser:
    def parse_command(self, command_string: str):
        from kollabor.commands.parser import SlashCommandParser

        return SlashCommandParser().parse_command(command_string)


def test_enter_prefers_exact_typed_command_over_highlighted_prefix_match():
    """If the buffer says /mode light, do not execute highlighted /model."""
    import asyncio

    executor = CommandExecutor()
    handler = CommandModeHandler(
        buffer_manager=BufferManager("/mode light"),
        renderer=None,
        event_bus=EventBus(),
        command_registry=CommandRegistry(),
        command_executor=executor,
        command_menu_renderer=CommandMenuRenderer({"name": "model"}),
        slash_parser=SlashParser(),
    )
    handler.command_menu_active = True

    asyncio.run(handler._execute_selected_command())

    assert executor.command is not None
    assert executor.command.name == "mode"
    assert executor.command.args == ["light"]


def test_mode_exact_search_does_not_include_model():
    """Exact /mode filter should not also match /model."""
    registry = SlashCommandRegistry()
    SystemCommandsPlugin(command_registry=registry, event_bus=EventBus()).register_commands()

    assert [command.name for command in registry.search_commands("mode")] == ["mode"]
