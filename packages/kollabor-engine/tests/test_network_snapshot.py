"""The web UI's network block: this engine's chats first, then any agent on this computer."""

from types import SimpleNamespace

import pytest
from kollabor_engine import server
from kollabor_engine.hub_bridge import HubBridge
from kollabor_engine.routes import sessions as sessions_route

from plugins.hub.messenger import AgentMessenger

ROWS = [{"name": "koordinator", "device": "server-box", "handle": "koordinator@server-box", "state": "idle"}]


def _status(network="", device="", agents=()):
    return {"type": "network_status", "network": network, "device": device, "trust": "open", "agents": list(agents)}


def _chat(session_id, pid, device="", remote=()):
    async def get_hub_state():
        return SimpleNamespace(device=device, remote=list(remote))

    return SimpleNamespace(
        session_id=session_id,
        alive=True,
        solo=False,
        device="",
        daemon=SimpleNamespace(pid=pid),
        state=SimpleNamespace(get_hub_state=get_hub_state),
    )


@pytest.fixture
def registry():
    sessions = server.get_session_registry()
    before = dict(sessions)
    sessions.clear()
    yield sessions
    sessions.clear()
    sessions.update(before)


@pytest.fixture
def local_agents(monkeypatch, tmp_path):
    """Agents on this computer, the one in this engine's folder (the cwd) listed
    last. Records which sockets were asked."""
    monkeypatch.chdir(tmp_path)
    rows = [
        {"session_id": "a-chat", "workspace": "/w/chat", "socket_path": "/s/chat.sock", "daemon_pid": 33},
        {"session_id": "a-other", "workspace": "/w/other", "socket_path": "/s/other.sock", "daemon_pid": 11},
        {"session_id": "a-home", "workspace": str(tmp_path), "socket_path": "/s/home.sock", "daemon_pid": 22},
    ]
    answers = {
        "/s/chat.sock": _status(),
        "/s/other.sock": _status(),
        "/s/home.sock": _status("mac-net", "mac-box", ROWS),
    }
    asked = []

    async def network_status(socket_path, timeout=5.0, **_):
        asked.append(socket_path)
        return answers.get(socket_path)

    monkeypatch.setattr(HubBridge, "discover_sessions", lambda self, use_cache=True: rows)
    monkeypatch.setattr(AgentMessenger, "request_network_status", network_status)
    return SimpleNamespace(answers=answers, asked=asked)


@pytest.mark.asyncio
async def test_with_no_chat_here_an_agent_on_this_computer_answers(registry, local_agents):
    # After a restart no chat runs here yet: the other computers still show.
    assert await sessions_route._network_snapshot(registry) == {"device": "mac-box", "remote": ROWS}
    # This engine's folder first; once it answers no other agent is asked.
    assert local_agents.asked == ["/s/home.sock"]


@pytest.mark.asyncio
async def test_an_agent_on_no_network_is_passed(registry, local_agents):
    local_agents.answers["/s/home.sock"] = _status()
    local_agents.answers["/s/other.sock"] = _status("lab-net", "mac-box", ROWS)

    assert (await sessions_route._network_snapshot(registry))["remote"] == ROWS
    assert local_agents.asked == ["/s/home.sock", "/s/chat.sock", "/s/other.sock"]


@pytest.mark.asyncio
async def test_a_chat_here_on_no_network_does_not_hide_the_network(registry, local_agents):
    # A daemon this engine started: its own session id, the agent's pid.
    registry["web-1"] = _chat("web-1", 33)
    local_agents.answers["/s/home.sock"] = _status()
    local_agents.answers["/s/other.sock"] = _status("lab-net", "mac-box", ROWS)

    assert (await sessions_route._network_snapshot(registry))["remote"] == ROWS
    # The chat was asked through its own state, never again over its socket.
    assert local_agents.asked == ["/s/home.sock", "/s/other.sock"]


@pytest.mark.asyncio
async def test_a_chat_here_on_the_network_answers_first(registry, local_agents):
    registry["a-chat"] = _chat("a-chat", 33, device="mac-box", remote=ROWS)

    assert await sessions_route._network_snapshot(registry) == {"device": "mac-box", "remote": ROWS}
    assert local_agents.asked == []


@pytest.mark.asyncio
async def test_no_agent_on_a_network_shows_none(registry, local_agents):
    local_agents.answers["/s/home.sock"] = None  # an old agent that never answers

    network = await sessions_route._network_snapshot(registry)

    assert network["remote"] == [] and network["device"]
