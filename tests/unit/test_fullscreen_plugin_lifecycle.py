"""Lifecycle regression tests for full-screen plugin timing."""

import asyncio
from types import SimpleNamespace

import pytest

from kollabor_tui.fullscreen import plugin as plugin_module
from kollabor_tui.fullscreen.plugin import FullScreenPlugin, PluginMetadata


class StubPlugin(FullScreenPlugin):
    """Minimal concrete plugin for lifecycle timing tests."""

    async def render_frame(self, delta_time: float) -> bool:
        return False

    async def handle_input(self, key_press) -> bool:
        return False


def reject_event_loop_allocation(monkeypatch):
    """Fail if plugin timing allocates an event loop it cannot own."""

    def fail_new_event_loop():
        pytest.fail("plugin timing allocated an event loop it does not own")

    monkeypatch.setattr(asyncio, "new_event_loop", fail_new_event_loop)


def test_runtime_stats_are_available_without_an_event_loop(monkeypatch):
    """Synchronous stats reads use the same monotonic lifecycle clock."""
    plugin = StubPlugin(PluginMetadata(name="test-plugin"))
    plugin.running = True
    plugin.start_time = 10.0
    plugin.frame_count = 7
    monkeypatch.setattr(
        plugin_module,
        "time",
        SimpleNamespace(monotonic=lambda: 13.5),
    )
    reject_event_loop_allocation(monkeypatch)

    stats = plugin.get_runtime_stats()

    assert stats["runtime_seconds"] == 3.5
    assert stats["fps"] == 2.0


@pytest.mark.asyncio
async def test_start_and_frame_stats_use_monotonic_timing_without_allocating_a_loop(
    monkeypatch,
):
    """Async lifecycle timing does not depend on event-loop clock access."""
    plugin = StubPlugin(PluginMetadata(name="test-plugin"))
    clock_values = iter((10.0, 10.25))
    monkeypatch.setattr(
        plugin_module,
        "time",
        SimpleNamespace(monotonic=lambda: next(clock_values)),
    )
    reject_event_loop_allocation(monkeypatch)

    await plugin.on_start()
    plugin.update_frame_stats()

    assert plugin.running is True
    assert plugin.start_time == 10.0
    assert plugin.frame_count == 1
    assert plugin.last_frame_time == 10.25
