"""Lifecycle regression tests for full-screen session timing."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor_tui.fullscreen import session as session_module
from kollabor_tui.fullscreen.session import FullScreenSession, SessionStats


def test_active_session_duration_is_available_without_an_event_loop(monkeypatch):
    """Synchronous stats reads must not allocate an unowned event loop."""
    stats = SessionStats(start_time=10.0)
    monkeypatch.setattr(
        session_module,
        "time",
        SimpleNamespace(monotonic=lambda: 13.5),
    )

    def fail_new_event_loop():
        pytest.fail("duration allocated an event loop it does not own")

    monkeypatch.setattr(asyncio, "new_event_loop", fail_new_event_loop)

    assert stats.duration == 3.5


@pytest.mark.asyncio
async def test_run_and_cleanup_use_monotonic_timing_without_allocating_a_loop(
    monkeypatch,
):
    """The async lifecycle records start and end time on the same clock."""
    plugin = SimpleNamespace(
        name="test-plugin",
        initialize=AsyncMock(return_value=True),
        on_start=AsyncMock(return_value=True),
        on_stop=AsyncMock(),
        cleanup=AsyncMock(),
    )
    session = FullScreenSession(plugin, None)
    session.renderer = MagicMock()
    clock = MagicMock(return_value=10.0)

    async def finish_session():
        clock.return_value = 13.5

    session._session_loop = finish_session
    monkeypatch.setattr(
        session_module,
        "time",
        SimpleNamespace(monotonic=clock),
    )

    def fail_new_event_loop():
        pytest.fail("session lifecycle allocated an event loop it does not own")

    monkeypatch.setattr(asyncio, "new_event_loop", fail_new_event_loop)

    assert await session.run() is True
    assert session.stats.start_time == 10.0
    assert session.stats.end_time == 13.5
    assert session.stats.duration == 3.5
    plugin.on_stop.assert_awaited_once()
    plugin.cleanup.assert_awaited_once()
    session.renderer.restore_terminal.assert_called_once()
