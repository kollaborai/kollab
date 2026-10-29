"""A halt waiter (halt_runtime._StatusSocket) lists with its line and never adopts."""

import json
import os
import socket
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from kollabor_engine import hub_bridge, server

LINE = "Network halted by mac. Resume needs a person on two machines: Settings → Network."


def _waiter(path: Path) -> socket.socket:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(path))
    sock.listen(4)

    def serve():
        while True:
            try:
                conn, _ = sock.accept()
            except OSError:
                return
            with conn:
                request = json.loads(conn.makefile("rb").readline())
                status = request.get("action") == "get_status"
                reply = {"type": "status", "state": "halted", "halted": LINE} if status else {}
                conn.sendall(json.dumps(reply).encode() + b"\n")

    threading.Thread(target=serve, daemon=True).start()
    return sock


@pytest.fixture
def waiter(tmp_path, monkeypatch):
    short = Path(tempfile.mkdtemp(prefix="kw", dir="/tmp"))  # AF_UNIX paths are short on macOS
    sock_path = short / "halted.sock"
    sock = _waiter(sock_path)
    # written once by the waiter: no last_heartbeat, liveness is the pid
    (tmp_path / "halted-1.json").write_text(json.dumps({
        "identity": "lapis", "pid": os.getpid(), "socket_path": str(sock_path),
        "project": "/Users/me/dev/kollab", "state": "halted", "started_at": time.time(),
    }))
    monkeypatch.setattr(hub_bridge, "_find_presence_dirs", lambda: [tmp_path])
    yield sock_path
    sock.close()
    sock_path.unlink(missing_ok=True)
    short.rmdir()


def test_a_waiter_lists_with_its_status_line(waiter):
    rows = hub_bridge.HubBridge().discover_sessions(use_cache=False)
    assert [(r["session_id"], r["halted"]) for r in rows] == [("lapis", LINE)]


def test_a_silent_waiter_still_says_the_network_is_halted(tmp_path):
    assert hub_bridge.waiter_line(tmp_path / "nothing.sock") == hub_bridge.WAITER_FALLBACK


@pytest.mark.asyncio
async def test_opening_a_waiter_runs_no_turn(waiter):
    with pytest.raises(HTTPException) as refused:
        await server.open_named_session(SimpleNamespace(path_params={"session_id": "lapis"}))
    assert refused.value.status_code == 409 and refused.value.detail == LINE
    assert "lapis" not in server._sessions
