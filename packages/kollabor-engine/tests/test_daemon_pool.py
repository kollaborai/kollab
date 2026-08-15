"""Daemon-pool lifecycle regressions."""

import asyncio
from collections.abc import Iterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from kollabor_engine import daemon_pool


def _close_current_event_loop() -> None:
    """Close and clear the non-running loop left on the main-thread policy."""
    policy = asyncio.get_event_loop_policy()
    try:
        loop = policy.get_event_loop()
    except RuntimeError:
        return
    if not loop.is_running():
        loop.close()
        policy.set_event_loop(None)


@pytest.fixture(scope="module", autouse=True)
def _release_pytest_asyncio_replacement_loop() -> Iterator[None]:
    """Release the clean loop pytest-asyncio 0.21 leaves after async tests."""
    yield
    _close_current_event_loop()


def test_close_current_event_loop_releases_policy_resource() -> None:
    """Module cleanup closes and clears its pytest-asyncio replacement loop."""
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    policy.set_event_loop(loop)

    _close_current_event_loop()

    assert loop.is_closed()
    with pytest.raises(RuntimeError, match="no current event loop"):
        policy.get_event_loop()


@pytest.mark.asyncio
async def test_spawn_closes_dead_existing_handle_before_replacement():
    pool = daemon_pool.DaemonPool()
    stale = SimpleNamespace(alive=False, close=AsyncMock())
    pool._daemons["sess_stale"] = stale

    replacement = SimpleNamespace(
        connect=AsyncMock(),
        identity="web-stale",
    )
    process = SimpleNamespace()

    async def await_socket(handle):
        assert "sess_stale" not in pool._daemons
        assert stale.close.await_count == 1
        return "/tmp/kollab-stale.sock"

    with (
        patch.object(daemon_pool.subprocess, "Popen", return_value=process),
        patch.object(daemon_pool, "DaemonHandle", return_value=replacement),
        patch.object(pool, "_await_socket", side_effect=await_socket),
    ):
        result = await pool.spawn("sess_stale", workspace="/tmp")

    assert result is replacement
    assert pool._daemons["sess_stale"] is replacement
    stale.close.assert_awaited_once()
    replacement.connect.assert_awaited_once_with("/tmp/kollab-stale.sock")
