"""Presence lifecycle regression tests."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from plugins.hub.presence import _atomic_write


def test_atomic_write_is_safe_for_concurrent_heartbeats(tmp_path: Path) -> None:
    path = tmp_path / "agent.json"

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(lambda i: _atomic_write(path, {"heartbeat": i}), range(100)))

    assert json.loads(path.read_text()) ["heartbeat"] in range(100)
    assert not list(tmp_path.glob("*.tmp"))
