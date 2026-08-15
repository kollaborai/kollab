"""Lifecycle regression tests for ProcessManager cleanup."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / "packages" / "kollabor-agent" / "src"))

from kollabor_agent.process_manager import ProcessManager, SpawnRequest


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

    await __import__("asyncio").to_thread(proc.wait, 5)
    assert await manager.cleanup_dead() == 1
    assert proc.stdout.closed
    assert proc.stdin.closed
    assert manager.get("short-lived") is None
