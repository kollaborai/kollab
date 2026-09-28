"""Tests for slash command execution from the popup menu."""

from kollabor.commands.executor import SlashCommandExecutor
from kollabor.commands.registry import SlashCommandRegistry
from kollabor.commands.system_commands.plugin import SystemCommandsPlugin
from kollabor_events.models import (
    CommandCategory,
    CommandDefinition,
    CommandMode,
    CommandResult,
)
from kollabor_tui.input.command_mode_handler import CommandModeHandler
from kollabor_tui.input.paste_processor import PasteProcessor
from kollabor_tui.key_parser import KeyParser, KeyPress, KeyType


class BufferManager:
    def __init__(self, content: str, buffer_limit: int = 1000):
        self.content = content
        self.buffer_limit = buffer_limit
        self.history = []

    @property
    def is_empty(self):
        return not self.content

    def clear(self):
        self.content = ""

    def insert_char(self, char: str):
        self.content += char

    async def handle_paste(self, paste_content: str):
        normalized = " ".join(
            paste_content.replace("\r\n", " ")
            .replace("\r", " ")
            .replace("\n", " ")
            .split()
        )
        self.content += normalized
        return True

    def add_to_history(self, command: str):
        self.history.append(command)

    def get_stats(self):
        return {"buffer_limit": self.buffer_limit}


class EventBus:
    def get_service(self, name):
        return None

    async def emit_with_hooks(self, *args, **kwargs):
        return {}


class CommandMenuRenderer:
    def __init__(self, selected):
        self.selected = selected
        self.hidden = False
        self.menu_items = []

    def get_selected_command(self):
        return self.selected

    def hide_menu(self):
        self.hidden = True

    def show_command_menu(self, commands, filter_text):
        self.menu_items = commands

    def set_selected_index(self, index):
        pass

    def filter_commands(self, commands, filter_text):
        self.menu_items = commands


class CommandExecutor:
    def __init__(self):
        self.command = None

    async def execute_command(self, command, event_bus):
        self.command = command
        return CommandResult(success=True, message="")


class CommandRegistry:
    def get_command(self, name):
        return object() if name in {"connect", "echo", "mode"} else None

    def list_commands(self):
        return []

    def search_commands(self, query):
        return []


class SlashParser:
    def parse_command(self, command_string: str):
        from kollabor.commands.parser import SlashCommandParser

        return SlashCommandParser().parse_command(command_string)


def _make_menu_with_paste(typed_prefix, pasted_text, selected_command):
    import asyncio
    from types import SimpleNamespace

    from kollabor_tui.input.input_loop_manager import InputLoopManager

    buffer_manager = BufferManager("")
    paste_processor = PasteProcessor(buffer_manager)
    executor = CommandExecutor()
    handler = CommandModeHandler(
        buffer_manager=buffer_manager,
        renderer=None,
        event_bus=EventBus(),
        command_registry=CommandRegistry(),
        command_executor=executor,
        command_menu_renderer=CommandMenuRenderer(selected_command),
        slash_parser=SlashParser(),
        expand_paste_placeholders=paste_processor.expand_paste_placeholders,
    )

    async def open_menu_type_prefix_and_paste():
        await handler.enter_command_mode()
        for char in typed_prefix:
            key_press = KeyPress(
                name=char,
                code=ord(char),
                char=char,
                type=KeyType.PRINTABLE,
            )
            await handler.handle_command_mode_keypress(key_press)

        input_loop = InputLoopManager(
            renderer=None,
            key_parser=KeyParser(),
            error_handler=None,
            paste_processor=paste_processor,
            config=SimpleNamespace(get=lambda key, default: default),
        )
        input_loop._get_command_mode_callback = lambda: handler.command_mode
        assert len(pasted_text) > 10
        assert input_loop._is_in_modal_mode() is False
        await input_loop._handle_paste_chunk(pasted_text)

    asyncio.run(open_menu_type_prefix_and_paste())
    return handler, executor, paste_processor


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

    asyncio.run(
        handler.handle_menu_popup_keypress(
            KeyPress(name="Enter", code=13, type=KeyType.CONTROL)
        )
    )

    assert executor.command is not None
    assert executor.command.name == "mode"
    assert executor.command.args == ["light"]


def test_raw_menu_input_submits_for_lf_and_cr():
    """The raw-character menu entry point treats LF and CR as Enter."""
    import asyncio

    for enter_char in ("\n", "\r"):
        executor = CommandExecutor()
        handler = CommandModeHandler(
            buffer_manager=BufferManager("/connect status"),
            renderer=None,
            event_bus=EventBus(),
            command_registry=CommandRegistry(),
            command_executor=executor,
            command_menu_renderer=CommandMenuRenderer({"name": "connect"}),
            slash_parser=SlashParser(),
        )
        handler.command_mode = CommandMode.MENU_POPUP
        handler.command_menu_active = True

        handled = asyncio.run(handler.handle_menu_popup_input(enter_char))

        assert handled is True
        assert executor.command is not None
        assert executor.command.name == "connect"
        assert executor.command.args == ["status"]


def test_mode_exact_search_does_not_include_model():
    """Exact /mode filter should not also match /model."""
    registry = SlashCommandRegistry()
    SystemCommandsPlugin(
        command_registry=registry, event_bus=EventBus()
    ).register_commands()

    assert [command.name for command in registry.search_commands("mode")] == ["mode"]


def test_ctrl_u_exits_command_mode_and_clears_menu():
    """Clearing a slash menu should not leave hidden command-mode state behind."""
    import asyncio

    menu = CommandMenuRenderer({"name": "mcp"})
    handler = CommandModeHandler(
        buffer_manager=BufferManager("/mcp"),
        renderer=None,
        event_bus=EventBus(),
        command_registry=CommandRegistry(),
        command_executor=CommandExecutor(),
        command_menu_renderer=menu,
        slash_parser=SlashParser(),
    )
    handler.command_mode = CommandMode.MENU_POPUP
    handler.command_menu_active = True

    handled = asyncio.run(
        handler.handle_menu_popup_keypress(
            KeyPress(
                name="Ctrl+U",
                code=21,
                type=KeyType.CONTROL,
                modifiers={"ctrl": True},
            )
        )
    )

    assert handled
    assert handler.command_mode == CommandMode.NORMAL
    assert handler.command_menu_active is False
    assert handler.buffer_manager.content == ""
    assert menu.hidden is True


def test_menu_expands_pasted_arguments_before_parsing_quoted_and_multiline_args():
    """Pasting args into an open slash menu preserves shell-style arguments."""
    import asyncio

    handler, executor, paste_processor = _make_menu_with_paste(
        "echo ",
        "alpha 'two words' escaped\\ value\r\nlast",
        {"name": "echo"},
    )
    buffer = handler.buffer_manager

    assert executor.command is None
    asyncio.run(
        handler.handle_menu_popup_keypress(
            KeyPress(name="Enter", code=13, type=KeyType.CONTROL)
        )
    )

    assert executor.command is not None
    assert executor.command.name == "echo"
    assert executor.command.args == ["alpha", "two words", "escaped value", "last"]
    assert paste_processor.paste_bucket == {}
    assert buffer.history == ["/echo alpha 'two words' escaped\\ value\nlast"]


def test_menu_preserves_connect_status_after_slash_then_burst_paste():
    """Typing '/' then pasting `connect status` must retain the status arg."""
    import asyncio

    handler, executor, paste_processor = _make_menu_with_paste(
        "",
        "connect status",
        {"name": "connect"},
    )
    buffer = handler.buffer_manager

    assert executor.command is None
    asyncio.run(
        handler.handle_menu_popup_keypress(
            KeyPress(name="Enter", code=13, type=KeyType.CONTROL)
        )
    )

    assert executor.command is not None
    assert executor.command.name == "connect"
    assert executor.command.args == ["status"]
    assert paste_processor.paste_bucket == {}
    assert buffer.history == ["/connect status"]


def test_menu_expands_a_full_command_pasted_after_the_slash_trigger():
    """Typing '/' then pasting the command body still dispatches that command."""
    import asyncio

    handler, executor, paste_processor = _make_menu_with_paste(
        "",
        "echo alpha 'two words'",
        {"name": "mode"},
    )
    buffer = handler.buffer_manager

    assert executor.command is None
    asyncio.run(
        handler.handle_menu_popup_keypress(
            KeyPress(name="Enter", code=13, type=KeyType.CONTROL)
        )
    )

    assert executor.command is not None
    assert executor.command.name == "echo"
    assert executor.command.args == ["alpha", "two words"]
    assert paste_processor.paste_bucket == {}
    assert buffer.history == ["/echo alpha 'two words'"]


def test_bracketed_multiline_paste_waits_for_explicit_enter_and_consumes_controls():
    """Pasted CRLF and control bytes are input data, not command actions."""
    import asyncio

    buffer = BufferManager("/echo ")
    executor = CommandExecutor()
    handler = CommandModeHandler(
        buffer_manager=buffer,
        renderer=None,
        event_bus=EventBus(),
        command_registry=CommandRegistry(),
        command_executor=executor,
        command_menu_renderer=CommandMenuRenderer({"name": "echo"}),
        slash_parser=SlashParser(),
    )
    handler.command_mode = CommandMode.MENU_POPUP
    handler.command_menu_active = True

    async def paste_bracketed_text():
        parser = KeyParser()
        sequence = "\x1b[200~alpha\r\nbeta\tgamma\x03\x16\x1b[201~"
        for char in sequence:
            key_press = parser.parse_char(char)
            if key_press is not None:
                await handler.handle_command_mode_keypress(key_press)

    asyncio.run(paste_bracketed_text())

    assert executor.command is None
    assert buffer.content == "/echo alpha beta gamma"

    asyncio.run(
        handler.handle_command_mode_keypress(
            KeyPress(name="Enter", code=13, type=KeyType.CONTROL)
        )
    )

    assert executor.command is not None
    assert executor.command.name == "echo"
    assert executor.command.args == ["alpha", "beta", "gamma"]


def test_aborted_bracketed_paste_does_not_poison_the_next_menu_session():
    """A fresh slash menu resets paste state after normal teardown."""
    import asyncio

    executor = CommandExecutor()
    handler = CommandModeHandler(
        buffer_manager=BufferManager(""),
        renderer=None,
        event_bus=EventBus(),
        command_registry=CommandRegistry(),
        command_executor=executor,
        command_menu_renderer=CommandMenuRenderer({"name": "connect"}),
        slash_parser=SlashParser(),
    )

    async def run_sessions():
        await handler.enter_command_mode()
        await handler.handle_menu_popup_keypress(
            KeyPress(name="BracketedPasteStart", code="ESC[200~", type=KeyType.EXTENDED)
        )
        await handler.handle_menu_popup_keypress(
            KeyPress(name="x", code=ord("x"), char="x", type=KeyType.PRINTABLE)
        )
        assert handler._bracketed_paste_active is True

        await handler.exit_command_mode()
        assert handler._bracketed_paste_active is False
        assert handler._bracketed_paste_buffer == []
        await handler.enter_command_mode()
        for char in "connect status":
            await handler.handle_menu_popup_keypress(
                KeyPress(
                    name=char,
                    code=ord(char),
                    char=char,
                    type=KeyType.PRINTABLE,
                )
            )
        await handler.handle_menu_popup_keypress(
            KeyPress(name="Enter", code=13, type=KeyType.CONTROL)
        )

    asyncio.run(run_sessions())

    assert handler._bracketed_paste_active is False
    assert executor.command is not None
    assert executor.command.name == "connect"
    assert executor.command.args == ["status"]


def test_oversized_bracketed_paste_is_rejected_without_partial_menu_input():
    """Bracketed-paste staging stays within the remaining input-buffer limit."""
    import asyncio

    buffer = BufferManager("/echo ", buffer_limit=10)
    executor = CommandExecutor()
    handler = CommandModeHandler(
        buffer_manager=buffer,
        renderer=None,
        event_bus=EventBus(),
        command_registry=CommandRegistry(),
        command_executor=executor,
        command_menu_renderer=CommandMenuRenderer({"name": "echo"}),
        slash_parser=SlashParser(),
    )
    handler.command_mode = CommandMode.MENU_POPUP
    handler.command_menu_active = True

    async def paste_too_much_text():
        parser = KeyParser()
        for char in "\x1b[200~too much text\x1b[201~":
            key_press = parser.parse_char(char)
            if key_press is not None:
                await handler.handle_command_mode_keypress(key_press)

    asyncio.run(paste_too_much_text())

    assert buffer.content == "/echo "
    assert handler._bracketed_paste_buffer == []
    assert executor.command is None


def test_keypress_pipeline_routes_lf_connect_status_to_registered_handler():
    """LF Enter must keep pasted menu args on the registered attach handler."""
    import asyncio
    from types import SimpleNamespace

    from kollabor_tui.input.input_loop_manager import InputLoopManager
    from kollabor_tui.input.key_press_handler import KeyPressHandler
    from plugins.hub.plugin import HubPlugin

    class AttachedState:
        def __init__(self):
            self.connect_values = []

        async def hub_connect(self, value):
            self.connect_values.append(value)
            return "status routed"

    class AttachedEventBus(EventBus):
        def __init__(self, state):
            self.state = state

        def get_service(self, name):
            return self.state if name == "state_service" else None

    class DisplayController:
        async def update_display(self, **kwargs):
            pass

    state = AttachedState()
    event_bus = AttachedEventBus(state)
    plugin = HubPlugin(event_bus=event_bus)
    plugin._cli_args = SimpleNamespace(attach="relay-proof-mac")

    # Register the bound method before creating the executor, matching plugin
    # startup and avoiding a post-registration instrumentation false negative.
    registry = SlashCommandRegistry()
    assert registry.register_command(
        CommandDefinition(
            name="connect",
            description="Connect to agents",
            handler=plugin._handle_connect_command,
            plugin_name="hub",
            mode=CommandMode.INSTANT,
            category=CommandCategory.CUSTOM,
        )
    )

    buffer = BufferManager("")
    paste_processor = PasteProcessor(buffer)
    menu = CommandMenuRenderer({"name": "connect"})
    command_handler = CommandModeHandler(
        buffer_manager=buffer,
        renderer=None,
        event_bus=event_bus,
        command_registry=registry,
        command_executor=SlashCommandExecutor(registry),
        command_menu_renderer=menu,
        slash_parser=SlashParser(),
        expand_paste_placeholders=paste_processor.expand_paste_placeholders,
    )
    key_handler = KeyPressHandler(
        buffer_manager=buffer,
        key_parser=KeyParser(),
        event_bus=event_bus,
        error_handler=None,
        display_controller=DisplayController(),
        paste_processor=paste_processor,
        renderer=None,
        command_mode_handler=command_handler,
    )
    key_handler.set_callbacks(
        enter_command_mode=command_handler.enter_command_mode,
        handle_command_mode_keypress=command_handler.handle_command_mode_keypress,
        expand_paste_placeholders=paste_processor.expand_paste_placeholders,
    )

    input_loop = InputLoopManager(
        renderer=None,
        key_parser=key_handler.key_parser,
        error_handler=None,
        paste_processor=paste_processor,
        config=SimpleNamespace(get=lambda key, default: default),
    )
    input_loop._get_command_mode_callback = lambda: command_handler.command_mode

    generic_enter_calls = 0
    original_handle_enter = key_handler._handle_enter

    async def track_generic_enter():
        nonlocal generic_enter_calls
        generic_enter_calls += 1
        await original_handle_enter()

    key_handler._handle_enter = track_generic_enter

    async def submit_pasted_command():
        await key_handler.process_character("/")
        await input_loop._handle_paste_chunk("connect status")
        placeholder_buffer = buffer.content
        # KeyParser maps LF (0x0a) to Ctrl+J, unlike CR (0x0d), which it maps
        # to Enter. Both are valid terminal submit sequences for this menu.
        await key_handler.process_character("\n")
        return placeholder_buffer

    placeholder_buffer = asyncio.run(submit_pasted_command())

    assert placeholder_buffer == "/[Pasted #1 1 lines, 14 chars]"
    assert state.connect_values == ["status"]
    assert buffer.history == ["/connect status"]
    assert paste_processor.paste_bucket == {}
    assert command_handler.command_mode == CommandMode.NORMAL
    assert generic_enter_calls == 0


def test_menu_submit_declined_by_callback_does_not_fall_through_to_normal_input():
    """A menu submit key cannot fall through to KeyPressHandler's generic Enter."""
    import asyncio
    from types import SimpleNamespace

    from kollabor_tui.input.key_press_handler import KeyPressHandler

    class DisplayController:
        async def update_display(self, **kwargs):
            pass

    buffer = BufferManager("/connect status")
    paste_processor = PasteProcessor(buffer)
    key_handler = KeyPressHandler(
        buffer_manager=buffer,
        key_parser=KeyParser(),
        event_bus=EventBus(),
        error_handler=None,
        display_controller=DisplayController(),
        paste_processor=paste_processor,
        renderer=None,
        command_mode_handler=SimpleNamespace(command_mode=CommandMode.MENU_POPUP),
    )
    generic_keypresses = []

    async def decline_menu_key(_key_press):
        return False

    async def record_generic_keypress(key_press):
        generic_keypresses.append(key_press.name)

    key_handler.set_callbacks(handle_command_mode_keypress=decline_menu_key)
    key_handler._handle_key_press = record_generic_keypress

    async def send_submit_keys():
        await key_handler.process_character("\r")
        await key_handler.process_character("\n")

    asyncio.run(send_submit_keys())

    assert generic_keypresses == []
    assert buffer.content == "/connect status"
