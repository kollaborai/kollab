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
