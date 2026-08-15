"""Focused regression coverage for hub plugin startup loop ownership."""

import asyncio
import gc
import warnings

import pytest

from plugins.hub.plugin import HubPlugin


class _EventBus:
    def __init__(self) -> None:
        self.hooks = []

    async def register_hook(self, hook) -> None:
        self.hooks.append(hook)

    def get_service(self, name):
        return None


def test_register_hooks_without_running_loop_does_not_allocate_resources():
    """Invalid sync driving fails before any coroutine, task, or loop is created."""
    plugin = HubPlugin()
    plugin.event_bus = _EventBus()
    plugin._cli_args = None
    register_hooks = plugin.register_hooks()

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", ResourceWarning)
        with pytest.raises(RuntimeError, match="no running event loop"):
            register_hooks.send(None)
        register_hooks.close()
        gc.collect()

    assert plugin._startup_task is None
    assert [warning for warning in caught if warning.category is ResourceWarning] == []


def test_register_hooks_schedules_and_shutdown_drains_startup_task(monkeypatch):
    """Hub startup stays on the caller-owned loop and cannot leak at teardown."""
    plugin = HubPlugin()
    plugin.event_bus = _EventBus()
    plugin._cli_args = None
    startup_entered = asyncio.Event()
    startup_cancelled = asyncio.Event()

    async def start_hub() -> None:
        startup_entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            startup_cancelled.set()
            raise

    plugin._start_hub = start_hub

    async def exercise() -> None:
        await plugin.register_hooks()
        await asyncio.wait_for(startup_entered.wait(), timeout=1)

        startup_task = plugin._startup_task
        assert startup_task is not None
        assert startup_task.get_loop() is asyncio.get_running_loop()
        assert not startup_task.done()

        await plugin.shutdown(exit_process=False)

        assert startup_cancelled.is_set()
        assert startup_task.cancelled()
        current = asyncio.current_task()
        assert [
            task
            for task in asyncio.all_tasks()
            if task is not current and not task.done()
        ] == []

    with asyncio.Runner() as runner:
        loop = runner.get_loop()
        monkeypatch.setattr(
            asyncio,
            "new_event_loop",
            lambda: (_ for _ in ()).throw(
                AssertionError("register_hooks must not create or own another loop")
            ),
        )
        runner.run(exercise())

    assert loop.is_closed()
