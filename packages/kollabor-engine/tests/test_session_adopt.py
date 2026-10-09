"""Every live agent on this computer opens in the web UI; the engine never stops one it did not start."""

import asyncio
import json
import os
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from kollabor_engine import daemon_pool, server
from kollabor_engine.hub_bridge import HubBridge
from kollabor_engine.routes import sessions as sessions_route
from kollabor_engine.session import EngineSession


@pytest.fixture
def registry():
    sessions = server.get_session_registry()
    before = dict(sessions)
    yield sessions
    sessions.clear()
    sessions.update(before)


def _row(session_id="a1b2c3", pid=4242, **extra):
    return {
        "session_id": session_id,
        "name": "koordinator",
        "identity": "koordinator",
        "agent": "default",
        "workspace": "/w/mentiko",
        "daemon_pid": pid,
        "socket_path": "",
        "created_at": 0,
        "active": True,
        "discovered": True,
        "external": True,
        **extra,
    }


@pytest.mark.asyncio
async def test_an_adopted_agent_is_detached_and_never_signalled(monkeypatch):
    # A real socket that speaks the attach handshake, standing in for a
    # terminal agent. Short dir: unix socket paths are capped near 104 bytes.
    sock_dir = tempfile.mkdtemp(prefix="kad-", dir="/tmp")
    sock = str(Path(sock_dir) / "k.sock")
    frames = []

    async def agent(reader, writer):
        frames.append(json.loads(await reader.readline()))
        writer.write(b'{"type": "attach_ack"}\n')
        await writer.drain()
        while line := await reader.readline():
            frames.append(json.loads(line))
        writer.close()

    listener = await asyncio.start_unix_server(agent, path=sock)
    signals = []
    real_kill = os.kill

    def kill(pid, sig):
        if sig:
            signals.append((pid, sig))
            return None
        return real_kill(pid, sig)

    monkeypatch.setattr(daemon_pool.os, "kill", kill)
    pool = daemon_pool.DaemonPool()
    try:
        handle = await pool.adopt("a1b2c3", _row(pid=os.getpid(), socket_path=sock))
        assert handle.alive
        assert frames[0]["action"] == "attach"
        assert frames[0]["client_id"] == f"engine-{os.getpid()}-a1b2c3"

        assert await pool.stop("a1b2c3")
        await asyncio.sleep(0.05)
    finally:
        listener.close()
        await listener.wait_closed()
        shutil.rmtree(sock_dir, ignore_errors=True)

    assert {"type": "detach"} in frames
    assert signals == []  # no SIGTERM, no SIGKILL: the agent is still its owner's


@pytest.mark.asyncio
async def test_concurrent_requests_open_an_agent_once(monkeypatch, registry):
    monkeypatch.setattr(HubBridge, "discover_sessions", lambda self, use_cache=False: [_row()])
    opened = []

    async def adopt(found):
        opened.append(found["session_id"])
        await asyncio.sleep(0.05)  # both requests are in flight while it opens
        return SimpleNamespace(session_id=found["session_id"])

    monkeypatch.setattr(EngineSession, "adopt", staticmethod(adopt))
    request = SimpleNamespace(path_params={"session_id": "a1b2c3"})

    await asyncio.gather(server.open_named_session(request), server.open_named_session(request))

    assert opened == ["a1b2c3"]
    assert registry["a1b2c3"].session_id == "a1b2c3"


def test_a_session_route_opens_the_agent_it_names(monkeypatch, registry):
    monkeypatch.setattr(HubBridge, "discover_sessions", lambda self, use_cache=False: [_row()])

    async def refresh_history():
        return [{"role": "user", "content": "from the terminal"}]

    async def adopt(found):
        return SimpleNamespace(
            session_id=found["session_id"],
            alive=True,
            last_turn_error=None,
            refresh_history=refresh_history,
        )

    monkeypatch.setattr(EngineSession, "adopt", staticmethod(adopt))
    client = TestClient(server.create_app())

    history = client.get("/sessions/a1b2c3/history")
    unknown = client.get("/sessions/nobody/history")

    assert history.status_code == 200
    assert history.json()["history"] == [{"role": "user", "content": "from the terminal"}]
    assert unknown.status_code == 404


def test_an_agent_that_will_not_attach_answers_503(monkeypatch, registry):
    monkeypatch.setattr(HubBridge, "discover_sessions", lambda self, use_cache=False: [_row()])

    async def adopt(found):
        raise ConnectionRefusedError("socket gone")

    monkeypatch.setattr(EngineSession, "adopt", staticmethod(adopt))
    response = TestClient(server.create_app()).get("/sessions/a1b2c3/history")

    assert response.status_code == 503
    assert "could not open this agent" in response.json()["detail"]
    assert "a1b2c3" not in registry


@pytest.mark.asyncio
async def test_list_sessions_lists_each_agent_once(monkeypatch, registry):
    # This engine's own daemon is in presence too, under its agent id.
    own = SimpleNamespace(
        session_id="griffin-born",
        alive=True,
        to_dict=lambda: {"session_id": "griffin-born", "daemon_pid": 111},
    )
    registry["griffin-born"] = own
    monkeypatch.setattr(
        HubBridge,
        "discover_sessions",
        lambda self, use_cache=False: [_row("own-agent-id", pid=111), _row("terminal-id", pid=222)],
    )

    async def no_network(_registry):
        return {"device": "", "remote": []}

    monkeypatch.setattr(sessions_route, "_network_snapshot", no_network)
    result = await sessions_route.list_sessions()

    assert [item["session_id"] for item in result["sessions"]] == ["griffin-born", "terminal-id"]


@pytest.mark.asyncio
async def test_a_daemon_this_engine_is_starting_is_not_listed_as_another_agent(monkeypatch, registry):
    # A new daemon writes presence before the engine attaches and registers it.
    pool = daemon_pool.DaemonPool()
    pool._starting["new-chat"] = ("bismuth", os.path.realpath("/w/mentiko"))
    pool._daemons["attached-chat"] = SimpleNamespace(pid=333)
    monkeypatch.setattr(sessions_route, "get_daemon_pool", lambda: pool)
    monkeypatch.setattr(
        HubBridge,
        "discover_sessions",
        lambda self, use_cache=False: [
            _row("starting-id", pid=0, identity="bismuth"),
            _row("attached-id", pid=333, identity="zircon"),
            _row("other-folder-id", pid=444, identity="bismuth", workspace="/w/other"),
        ],
    )

    async def no_network(_registry):
        return {"device": "", "remote": []}

    monkeypatch.setattr(sessions_route, "_network_snapshot", no_network)
    result = await sessions_route.list_sessions()

    assert [item["session_id"] for item in result["sessions"]] == ["other-folder-id"]


def test_a_daemon_this_engine_is_starting_does_not_open_as_another_agent(monkeypatch, registry):
    pool = daemon_pool.DaemonPool()
    pool._starting["new-chat"] = ("koordinator", os.path.realpath("/w/mentiko"))
    monkeypatch.setattr(server, "get_daemon_pool", lambda: pool)
    monkeypatch.setattr(HubBridge, "discover_sessions", lambda self, use_cache=False: [_row()])

    async def adopt(found):
        raise AssertionError("must not adopt this engine's own daemon")

    monkeypatch.setattr(EngineSession, "adopt", staticmethod(adopt))
    response = TestClient(server.create_app()).get("/sessions/a1b2c3/history")

    assert response.status_code == 404


def test_adopted_session_reports_itself_as_external():
    session = EngineSession(session_id="a1b2c3", profile=None, approval_mode="")
    session.external = True
    session.display_name = "2610082249-nexus-drift"
    session.daemon = SimpleNamespace(identity="koordinator", agent_name="default", pid=4242, alive=True)

    row = session.to_dict()

    assert row["external"] is True
    assert row["name"] == "2610082249-nexus-drift"
    assert row["identity"] == "koordinator"
    assert row["daemon_pid"] == 4242


@pytest.mark.asyncio
async def test_an_open_agent_keeps_the_name_of_its_current_conversation(monkeypatch, registry):
    # The terminal ran /new: presence names the new conversation's log.
    session = EngineSession(session_id="a1b2c3", profile=None, approval_mode="")
    session.external = True
    session.display_name = "2610082249-nexus-drift"
    session.daemon = SimpleNamespace(identity="koordinator", agent_name="", pid=4242, alive=True)
    registry["a1b2c3"] = session
    monkeypatch.setattr(
        HubBridge,
        "discover_sessions",
        lambda self, use_cache=False: [_row("a1b2c3", pid=4242, name="2610090000-quiet-heron")],
    )

    async def no_network(_registry):
        return {"device": "", "remote": []}

    monkeypatch.setattr(sessions_route, "_network_snapshot", no_network)
    result = await sessions_route.list_sessions()

    assert [item["name"] for item in result["sessions"]] == ["2610090000-quiet-heron"]

