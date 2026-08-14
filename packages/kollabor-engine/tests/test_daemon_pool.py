"""Daemon-pool lifecycle regressions."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from kollabor_engine import daemon_pool


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
