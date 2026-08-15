"""Lifecycle regression tests for ProcessManager cleanup."""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "packages" / "kollabor-agent" / "src"))

from kollabor_agent.process_manager import ProcessManager, SpawnRequest


@pytest.mark.asyncio
async def test_cleanup_dead_closes_naturally_exited_process_pipes():
    manager = ProcessManager()
    managed = await manager.spawn(
        SpawnRequest(
            name="short-lived",
            cmd=[sys.executable, "-c", "print('done', flush=True)"],
        )
    )
    assert managed is not None
    proc = managed._handle
    assert proc.stdout is not None
    assert proc.stdin is not None

    await asyncio.to_thread(proc.wait, 5)
    assert await manager.cleanup_dead() == 1
    assert proc.stdout.closed
    assert proc.stdin.closed
    assert manager.get("short-lived") is None


@pytest.mark.asyncio
async def test_kill_uses_the_running_loop_without_allocating_an_unowned_loop(
    monkeypatch,
):
    manager = ProcessManager()
    managed = await manager.spawn(
        SpawnRequest(
            name="long-lived",
            cmd=[sys.executable, "-c", "import time; time.sleep(30)"],
        )
    )
    assert managed is not None

    def fail_new_event_loop():
        pytest.fail("kill() allocated an event loop it does not own")

    monkeypatch.setattr(asyncio, "new_event_loop", fail_new_event_loop)
    assert await manager.kill("long-lived", graceful_timeout=1)
