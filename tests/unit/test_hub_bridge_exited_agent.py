"""#566: a request for an agent that exited must not reach a same-named successor.

A successor with the same identity binds the same identity-named socket path.
The bridge's cached presence record for the exited agent still names that path,
so the request used to be answered by the successor, labelled with the exited
agent's id.
"""

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest
from kollabor_engine import hub_bridge
from kollabor_engine.hub_bridge import HubBridge


@pytest.fixture
def sock_path(tmp_path):
    # Unix socket paths are capped near 104 bytes on macOS, so fall back to a short dir.
    if len(str(tmp_path / "first.sock")) <= 100:
        yield tmp_path / "first.sock"
        return
    short = Path(tempfile.mkdtemp(dir="/tmp"))
    try:
        yield short / "first.sock"
    finally:
        shutil.rmtree(short, ignore_errors=True)


def _record(agent_id, identity, **fields):
    return {
        "agent_id": agent_id,
        "identity": identity,
        "pid": os.getpid(),
        "last_heartbeat": time.time(),
        **fields,
    }


async def _serve_successor(reader, writer):
    await reader.readline()
    writer.write(b'{"type": "output", "lines": ["successor output"]}\n')
    await writer.drain()
    writer.close()


@pytest.mark.skipif(os.name == "nt", reason="the stub successor is a unix socket")
@pytest.mark.parametrize(
    "crashed",
    [True, False],
    ids=["crash-leaves-presence-file", "graceful-exit-removes-presence-file"],
)
def test_a_request_for_an_exited_agent_is_not_answered_by_its_successor(
    tmp_path, monkeypatch, sock_path, crashed
):
    presence = tmp_path / "presence"
    presence.mkdir()
    # The bridge caches presence for five seconds. A long TTL makes the exit land inside
    # the cache window on every run, which is the scenario in the issue.
    monkeypatch.setattr(hub_bridge, "CACHE_TTL_SECONDS", 3600)
    monkeypatch.setattr(hub_bridge, "_find_presence_dirs", lambda: [presence])
    exited = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        old_file = presence / "old-agent.json"
        old_file.write_text(
            json.dumps(_record("old-agent", "first", pid=exited.pid, socket_path=str(sock_path)))
        )
        (presence / "new-agent.json").write_text(
            json.dumps(_record("new-agent", "first", socket_path=str(sock_path)))
        )
        bridge = HubBridge()
        assert bridge.get_agent("old-agent") is not None  # the cache holds the live agent's record

        exited.kill()
        exited.wait()
        assert not hub_bridge._pid_alive(exited.pid)
        if not crashed:
            old_file.unlink()

        async def ask_both():
            # The successor binds the path the exited agent's socket used.
            server = await asyncio.start_unix_server(_serve_successor, path=str(sock_path))
            async with server:
                return (
                    await bridge.get_agent_output("old-agent"),
                    await bridge.get_agent_output("new-agent"),
                )

        exited_reply, successor_reply = asyncio.run(ask_both())
    finally:
        if exited.poll() is None:
            exited.kill()
            exited.wait()

    assert successor_reply == "successor output"  # the stub is reachable by its own id
    assert exited_reply is None


@pytest.mark.skipif(os.name == "nt", reason="the stub successor is a unix socket")
def test_a_request_for_an_agent_restarted_in_place_is_not_answered_by_its_new_image(
    tmp_path, monkeypatch, sock_path
):
    """hub_restart and /upgrade re-exec the process: same pid, new agent id, same socket path."""
    presence = tmp_path / "presence"
    presence.mkdir()
    monkeypatch.setattr(hub_bridge, "CACHE_TTL_SECONDS", 3600)
    monkeypatch.setattr(hub_bridge, "_find_presence_dirs", lambda: [presence])
    old_file = presence / "old-agent.json"
    old_file.write_text(json.dumps(_record("old-agent", "first", socket_path=str(sock_path))))
    bridge = HubBridge()
    assert bridge.get_agent("old-agent") is not None  # the cache holds the old image's record

    # The old image removed its record on shutdown; the new image, with the same pid,
    # wrote its own under a new agent id.
    old_file.unlink()
    (presence / "new-agent.json").write_text(
        json.dumps(_record("new-agent", "first", socket_path=str(sock_path)))
    )

    async def ask_both():
        server = await asyncio.start_unix_server(_serve_successor, path=str(sock_path))
        async with server:
            return (
                await bridge.get_agent_output("old-agent"),
                await bridge.get_agent_output("new-agent"),
            )

    old_reply, new_reply = asyncio.run(ask_both())

    assert new_reply == "successor output"
    assert old_reply is None
