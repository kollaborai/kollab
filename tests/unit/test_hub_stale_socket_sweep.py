"""The startup socket sweep removes dead sockets only, never one a live agent still serves.

A kollab with another HOME (a test, a tmux spec) has its own presence dir but can
share the /tmp socket folder; it must not cut off the agents it cannot see.
"""

import os
import socket
import tempfile
import time
from pathlib import Path

from plugins.hub import presence
from plugins.hub.presence import PresenceManager


def _aged(path: Path) -> None:
    old = time.time() - 60
    os.utime(path, (old, old))


def test_sweep_keeps_a_live_socket_and_removes_a_dead_one(monkeypatch, tmp_path):
    sockets = Path(tempfile.mkdtemp(dir="/tmp"))  # short path: unix socket limit
    presence_dir = tmp_path / "presence"  # empty: this process sees no agents
    presence_dir.mkdir()
    monkeypatch.setattr(presence, "get_socket_dir", lambda: sockets)
    monkeypatch.setattr(presence, "get_presence_dir", lambda: presence_dir)

    live = socket.socket(socket.AF_UNIX)
    live.bind(str(sockets / "zircon.sock"))
    live.listen(1)
    dead = socket.socket(socket.AF_UNIX)
    dead.bind(str(sockets / "ghost.sock"))
    dead.close()  # the file stays, nothing listens
    for name in ("zircon.sock", "ghost.sock"):
        _aged(sockets / name)
    try:
        assert PresenceManager.cleanup_stale_sockets() == 1
        assert (sockets / "zircon.sock").exists()
        assert not (sockets / "ghost.sock").exists()
    finally:
        live.close()
