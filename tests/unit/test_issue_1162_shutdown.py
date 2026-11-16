import asyncio
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from kollabor.application import TerminalLLMChat
from kollabor_agent import BackgroundTaskManager
from kollabor_tui.terminal_renderer import TerminalRenderer
from plugins.hub.messenger import AgentMessenger
from plugins.hub.models import HubMessage
from plugins.hub.plugin import HubPlugin


def test_empty_background_task_exception_logs_type(caplog):
    task_config = SimpleNamespace(
        background_tasks=SimpleNamespace(max_concurrent=5, log_task_errors=True)
    )
    manager = BackgroundTaskManager(task_config, queue_metrics={})

    async def fail_without_message():
        raise RuntimeError()

    loop = asyncio.new_event_loop()
    try:
        task = loop.create_task(fail_without_message(), name="shutdown-task")
        with pytest.raises(RuntimeError):
            loop.run_until_complete(task)

        with caplog.at_level("ERROR"):
            manager._task_done_callback(task)
        assert "RuntimeError" in caplog.text
    finally:
        loop.close()


@pytest.mark.asyncio
async def test_cleanup_logs_shutdown_cancellation_at_info(caplog, monkeypatch):
    app = TerminalLLMChat.__new__(TerminalLLMChat)
    app.event_bus = SimpleNamespace(get_service=lambda _name: None)
    app.acp_orchestrator = SimpleNamespace(shutdown=AsyncMock(side_effect=asyncio.CancelledError()))
    app._background_tasks = []
    app.shutdown = AsyncMock()
    fake_code_service = SimpleNamespace(shutdown=AsyncMock())
    monkeypatch.setitem(sys.modules, "kollabor.code_service", fake_code_service)

    with caplog.at_level("INFO"):
        await app._cleanup_impl()

    assert "ACP runner cleanup failed" not in caplog.text


def test_detached_renderer_skips_raw_mode_restore():
    renderer = TerminalRenderer.__new__(TerminalRenderer)
    renderer.terminal_state = SimpleNamespace(is_terminal=False, exit_raw_mode=Mock())

    renderer.exit_raw_mode()

    renderer.terminal_state.exit_raw_mode.assert_not_called()


@pytest.mark.asyncio
async def test_shutdown_stops_hub_intake_before_provider_shutdown():
    order = []

    class Hub:
        async def stop_message_intake(self):
            order.append("hub")

    class EventBus:
        def get_service(self, name):
            return Hub() if name == "hub_plugin" else None

        async def emit_with_hooks(self, *_args):
            pass

    async def shutdown_provider():
        assert order == ["hub"]
        order.append("provider")

    app = TerminalLLMChat.__new__(TerminalLLMChat)
    app._shutdown_complete = False
    app.event_bus = EventBus()
    app.running = True
    app.script_widget_manager = SimpleNamespace(
        shutdown_refresh_scheduler=AsyncMock()
    )
    app.input_handler = SimpleNamespace(stop=AsyncMock())
    app.llm_service = SimpleNamespace(shutdown=shutdown_provider)
    app.conversation_logger = SimpleNamespace(shutdown=AsyncMock())
    app.mcp_integration = SimpleNamespace(shutdown=AsyncMock())
    app.plugin_instances = {}
    app._needs_terminal_cleanup = False
    app.pipe_mode = False

    await app.shutdown()

    assert order == ["hub", "provider"]


@pytest.mark.asyncio
async def test_late_hub_message_is_saved_for_next_start(monkeypatch):
    plugin = HubPlugin()
    plugin._identity = SimpleNamespace(identity="receiver")
    plugin._accepting_messages = False
    message = HubMessage(
        from_agent="sender-id",
        from_identity="sender",
        to="receiver",
        content="arrived during shutdown",
    )
    save_to_mailbox = AsyncMock()
    monkeypatch.setattr(AgentMessenger, "send_to_file", save_to_mailbox)

    result = await plugin._on_message_received(message)

    assert result is True
    save_to_mailbox.assert_awaited_once_with("receiver", message)
