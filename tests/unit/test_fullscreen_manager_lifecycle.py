"""Lifecycle regression tests for the full-screen manager."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import kollabor_tui.fullscreen.manager as manager_module
from kollabor_tui.fullscreen.manager import FullScreenManager


class StubSession:
    """Minimal session that records which loop owns its awaited run."""

    def __init__(self, plugin, event_bus, **kwargs):
        self.plugin = plugin
        self.event_bus = event_bus
        self.kwargs = kwargs
        self.running = False
        self.run_loop = None

    async def run(self):
        self.run_loop = asyncio.get_running_loop()
        return True

    def get_stats(self):
        return {"run_loop": self.run_loop}


def test_record_session_requires_a_running_loop_without_allocating_one(monkeypatch):
    manager = FullScreenManager(None, None)
    plugin = SimpleNamespace(name="test-plugin")

    def fail_new_event_loop():
        pytest.fail("_record_session allocated an event loop it does not own")

    monkeypatch.setattr(asyncio, "new_event_loop", fail_new_event_loop)

    with pytest.raises(RuntimeError, match="no running event loop"):
        manager._record_session(plugin, True)

    assert manager.session_history == []


@pytest.mark.asyncio
async def test_launch_plugin_uses_its_running_loop_without_allocating_one(
    monkeypatch,
):
    event_bus = SimpleNamespace(emit_with_hooks=AsyncMock())
    manager = FullScreenManager(event_bus, None)
    plugin = SimpleNamespace(
        name="test-plugin",
        metadata=SimpleNamespace(aliases=[]),
    )
    assert manager.register_plugin(plugin)

    def fail_new_event_loop():
        pytest.fail("launch_plugin allocated an event loop it does not own")

    monkeypatch.setattr(asyncio, "new_event_loop", fail_new_event_loop)
    monkeypatch.setattr(manager_module, "FullScreenSession", StubSession)

    running_loop = asyncio.get_running_loop()
    assert await manager.launch_plugin(plugin.name, option="value")

    assert manager.current_session is None
    assert len(manager.session_history) == 1
    record = manager.session_history[0]
    assert record["plugin_name"] == plugin.name
    assert record["success"] is True
    assert record["stats"] == {"run_loop": running_loop}
    assert isinstance(record["timestamp"], float)
    assert event_bus.emit_with_hooks.await_count == 3
