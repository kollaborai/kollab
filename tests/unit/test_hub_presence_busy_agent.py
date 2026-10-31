"""A peer never deletes a live, busy agent's presence or socket (#545).

The koordinator froze for two minutes after each dreaming cycle (#546); peers
saw a stale heartbeat and a socket that did not answer, deleted both, and every
connection to it dropped.
"""

import json
import os
import time

import pytest

from kollabor_agent.runtime import AgentRuntime
from plugins.hub.presence import PresenceManager


def _peer_with_heartbeat_age(tmp_path, monkeypatch, age):
    presence, sockets = tmp_path / "presence", tmp_path / "sockets"
    presence.mkdir()
    sockets.mkdir()
    monkeypatch.setattr("plugins.hub.presence.get_presence_dir", lambda: presence)
    monkeypatch.setattr("plugins.hub.presence.get_socket_dir", lambda: sockets)
    sock = sockets / "koordinator.sock"
    sock.write_text("")  # nothing answers on it, as when the agent is stuck
    peer = AgentRuntime(name="coder", identity="koordinator")
    data = peer.to_presence_dict()
    data.update(pid=os.getpid(), socket_path=str(sock), last_heartbeat=time.time() - age)
    presence_file = presence / f"{peer.agent_id}.json"
    presence_file.write_text(json.dumps(data))
    return PresenceManager(AgentRuntime(name="coder", identity="lapis")), presence_file, sock


def test_a_busy_live_agent_keeps_its_files(tmp_path, monkeypatch):
    me, presence_file, sock = _peer_with_heartbeat_age(tmp_path, monkeypatch, age=120)
    assert me.discover_agents(include_solo=True) == []  # not a peer while it is stuck
    assert me.startup_scan() == 0
    assert presence_file.exists() and sock.exists()


@pytest.mark.asyncio
async def test_a_busy_live_agent_keeps_its_files_async(tmp_path, monkeypatch):
    me, presence_file, sock = _peer_with_heartbeat_age(tmp_path, monkeypatch, age=120)
    assert await me.discover_agents_async(include_solo=True) == []
    assert presence_file.exists() and sock.exists()


def test_a_live_pid_silent_past_the_grace_is_cleaned(tmp_path, monkeypatch):
    # Most likely a recycled pid: the agent's own process is gone.
    age = PresenceManager.LIVE_PID_GRACE_SECONDS + 60
    me, presence_file, sock = _peer_with_heartbeat_age(tmp_path, monkeypatch, age=age)
    assert me.discover_agents(include_solo=True) == []
    assert not presence_file.exists() and not sock.exists()
