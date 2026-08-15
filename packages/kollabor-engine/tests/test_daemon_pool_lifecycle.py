"""Daemon-handle lifecycle regressions."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from kollabor_engine import daemon_pool


@pytest.mark.asyncio
async def test_close_awaits_writer_wait_closed_before_returning():
    writer = SimpleNamespace(
        write=lambda _data: None,
        drain=AsyncMock(),
        close=lambda: None,
        wait_closed=AsyncMock(),
    )
    handle = daemon_pool.DaemonHandle(
        "sess_lifecycle", "web-lifecycle", SimpleNamespace()
    )
    handle._writer = writer

    with patch.object(
        daemon_pool.HubBridge,
        "get_agent_by_identity",
        return_value=None,
    ):
        await handle.close()

    writer.wait_closed.assert_awaited_once()
    assert handle._closed is True
