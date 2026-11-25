"""Deferred-startup failure behavior."""

import asyncio
from types import SimpleNamespace

import pytest

from kollabor.application import TerminalLLMChat


@pytest.mark.asyncio
async def test_deferred_startup_failure_stops_foreground_runtime():
    app = object.__new__(TerminalLLMChat)
    app._background_tasks = []
    app._startup_complete = False
    app._startup_ready = asyncio.Event()
    app.running = True
    app.input_handler = SimpleNamespace(running=True)

    class RenderLoop:
        stopped = False

        def stop(self):
            self.stopped = True

    app.render_loop = RenderLoop()

    async def no_op_update_check():
        return None

    async def fail_loading_hooks():
        raise RuntimeError("hook initialization exploded")

    app._check_for_updates = no_op_update_check
    app._load_config_hooks = fail_loading_hooks

    with pytest.raises(RuntimeError, match="hook initialization exploded"):
        await app._deferred_startup()

    assert app.running is False
    assert app.input_handler.running is False
    assert app.render_loop.stopped is True
    assert app._startup_complete is False
    assert app._startup_ready.is_set()
    assert app._get_startup_status() == ("failed", "hook initialization exploded")


@pytest.mark.asyncio
async def test_sessions_cli_command_stays_interactive_by_default(monkeypatch):
    app = object.__new__(TerminalLLMChat)
    app._background_tasks = []
    app._startup_complete = False
    app._startup_ready = asyncio.Event()
    app.running = True
    app.pipe_mode = False
    app._attach_to = None
    app._attach_socket = None
    app._goal_driver = None
    app.args = SimpleNamespace(stay=False)
    app.input_handler = SimpleNamespace(running=True)

    state = {"cleanup_calls": 0, "exit_calls": []}

    async def no_op(*_args, **_kwargs):
        return None

    def close_background_task(coro, _name=None):
        coro.close()

    async def process_sessions_command():
        return {"name": "sessions", "args": [], "command_def": None}

    async def execute_command(_command):
        return 0

    async def cleanup():
        state["cleanup_calls"] += 1

    def exit_process(code):
        state["exit_calls"].append(code)

    app._boot_projects_sweep = lambda: None
    app.create_background_task = close_background_task
    app._check_for_updates = no_op
    app._start_parent_watchdog = lambda: None
    app._load_config_hooks = no_op
    app._initialize_llm_core = no_op
    app._display_provider_warning = no_op
    app._initialize_plugins = no_op
    app._initialize_acp_orchestrator = no_op
    app._process_cli_command = process_sessions_command
    app._execute_cli_command = execute_command
    app.cleanup = cleanup
    app._display_ready_message = no_op
    app.script_widget_manager = SimpleNamespace(
        initialize_refresh_scheduler=no_op
    )
    app.event_bus = SimpleNamespace(
        emit_with_hooks=no_op,
        get_service=lambda _name: None,
    )
    app._check_first_run_wizard = no_op
    monkeypatch.setattr("kollabor.application.sys.exit", exit_process)

    await app._deferred_startup()

    assert state == {"cleanup_calls": 0, "exit_calls": []}
    assert app._startup_complete is True
    assert app._startup_ready.is_set()
