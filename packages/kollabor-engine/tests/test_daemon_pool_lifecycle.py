"""Daemon-handle lifecycle regressions."""

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
