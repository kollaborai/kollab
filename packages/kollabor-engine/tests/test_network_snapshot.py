"""The web UI's networks: every agent on this computer is asked, one answer per network."""

import asyncio
import subprocess
from contextlib import aclosing
from types import SimpleNamespace

import pytest
from kollabor_engine import server
from kollabor_engine.hub_bridge import HubBridge, network_daemons
from kollabor_engine.routes import sessions as sessions_route

from plugins.hub.messenger import AgentMessenger
from plugins.hub.relay_commands import network_label

ROWS = [{"name": "koordinator", "device": "server-box", "handle": "koordinator@server-box", "state": "idle"}]
LAB = [{"name": "lapis", "device": "lab-box", "handle": "lapis@lab-box", "state": "idle"}]


def _status(network="", device="", agents=()):
    return {"type": "network_status", "network": network, "device": device, "trust": "open", "agents": list(agents)}


def _chat(session_id, pid, solo=False, device=""):
    return SimpleNamespace(session_id=session_id, solo=solo, device=device, daemon=SimpleNamespace(pid=pid))


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
    last. Records which sockets were asked, how many asks were out at once, and
    which were cancelled; a socket in ``slow`` answers only when cancelled."""
    monkeypatch.chdir(tmp_path)
    rows = [
        {"session_id": "a-chat", "workspace": "/w/chat", "socket_path": "/s/chat.sock", "daemon_pid": 33},
        {"session_id": "a-other", "workspace": "/w/other", "socket_path": "/s/other.sock", "daemon_pid": 11},
        {"session_id": "a-home", "workspace": str(tmp_path), "socket_path": "/s/home.sock", "daemon_pid": 22},
    ]
    state = SimpleNamespace(
        answers={
            "/s/chat.sock": _status(),
            "/s/other.sock": _status(),
            "/s/home.sock": _status("mac-net", "mac-box", ROWS),
        },
        asked=[], cancelled=[], slow=set(), out=0, peak=0, home=tmp_path,
    )

    async def network_status(socket_path, timeout=5.0, **_):
        state.asked.append(socket_path)
        state.out += 1
        state.peak = max(state.peak, state.out)
        try:
            await asyncio.sleep(3600 if socket_path in state.slow else 0)
        except asyncio.CancelledError:
            state.cancelled.append(socket_path)
            raise
        finally:
            state.out -= 1
        return state.answers.get(socket_path)

    monkeypatch.setattr(HubBridge, "discover_sessions", lambda self, use_cache=True: rows)
    monkeypatch.setattr(AgentMessenger, "request_network_status", network_status)
    return state


def _answers(network):
    return [(a["network"], a["device"], a["remote"], a["folder"]) for a in network["networks"]]


def test_quiet_network_reports_existing_local_process_by_hub(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    other = tmp_path / "other"
    repo.mkdir()
    other.mkdir()
    (repo / "subfolder").mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    monkeypatch.setenv("KOLLAB_HUB_PROJECT_SCOPED", "true")
    # This live process has stale presence and did not answer network_status.
    monkeypatch.setattr(
        "plugins.hub.presence.live_agents_on_machine",
        lambda: [(repo, {"project": str(repo / "subfolder"), "solo": False})],
    )
    network = {"live": False, "folder": str(repo), "networks": [
        {"folder": str(repo), "live": False}, {"folder": str(other), "live": False},
    ]}
    pool = SimpleNamespace(all=lambda: [], starting_folders=lambda: [])

    result = sessions_route._mark_local_network_agents(network, {}, pool)

    assert result["live"] is False
    assert result["local_agent_running"] is True
    assert [group["local_agent_running"] for group in result["networks"]] == [True, False]


def test_solo_and_remote_sessions_do_not_count_as_local_network_carriers(tmp_path):
    network = {"folder": str(tmp_path), "networks": [{"folder": str(tmp_path)}]}
    registry = {
        "solo": SimpleNamespace(alive=True, solo=True, device="", workspace=str(tmp_path)),
        "remote": SimpleNamespace(alive=True, solo=False, device="far", workspace=str(tmp_path)),
    }
    pool = SimpleNamespace(all=lambda: [], starting_folders=lambda: [])

    result = sessions_route._mark_local_network_agents(network, registry, pool)

    assert result["local_agent_running"] is False
    assert result["networks"][0]["local_agent_running"] is False


def test_starting_agent_and_unknown_presence_prevent_a_second_launch(tmp_path, monkeypatch):
    pool = SimpleNamespace(all=lambda: [], starting_folders=lambda: [str(tmp_path)])
    network = {"folder": str(tmp_path)}
    assert sessions_route._mark_local_network_agents(network, {}, pool)["local_agent_running"] is True

    def unavailable():
        raise OSError("presence unavailable")

    monkeypatch.setattr("plugins.hub.presence.live_agents_on_machine", unavailable)
    pool.starting_folders = lambda: []
    assert sessions_route._mark_local_network_agents(network, {}, pool)["local_agent_running"] is None


@pytest.mark.asyncio
async def test_with_no_chat_here_the_agents_on_this_computer_answer(registry, local_agents):
    # After a restart no chat runs here yet: the other computers still show.
    network = await sessions_route._network_snapshot(registry)

    assert (network["device"], network["remote"], network["folder"]) == ("mac-box", ROWS, str(local_agents.home))
    assert _answers(network) == [("mac-net", "mac-box", ROWS, str(local_agents.home))]
    # Every agent is asked, all at once, this engine's folder first.
    assert local_agents.asked == ["/s/home.sock", "/s/chat.sock", "/s/other.sock"]
    assert local_agents.peak == 3


@pytest.mark.asyncio
async def test_every_network_on_this_computer_shows(registry, local_agents):
    # Two folders here on two networks: the second network's agents show too.
    local_agents.answers["/s/other.sock"] = _status("lab-net", "mac-lab", LAB)

    network = await sessions_route._network_snapshot(registry)

    assert _answers(network) == [
        ("mac-net", "mac-box", ROWS, str(local_agents.home)),
        ("lab-net", "mac-lab", LAB, "/w/other"),
    ]
    assert network["remote"] == ROWS + LAB and network["device"] == "mac-box"


@pytest.mark.asyncio
async def test_an_agent_on_no_network_is_passed(registry, local_agents):
    local_agents.answers["/s/home.sock"] = _status()
    local_agents.answers["/s/other.sock"] = _status("lab-net", "mac-lab", LAB)

    network = await sessions_route._network_snapshot(registry)

    assert _answers(network) == [("lab-net", "mac-lab", LAB, "/w/other")]
    assert network["folder"] == "/w/other"


@pytest.mark.asyncio
async def test_the_first_agent_on_a_network_answers_for_it(registry, local_agents):
    local_agents.answers["/s/chat.sock"] = _status("mac-net", "mac-box", ROWS)

    network = await sessions_route._network_snapshot(registry)

    assert [a["folder"] for a in network["networks"]] == [str(local_agents.home)]
    assert network["remote"] == ROWS


@pytest.mark.asyncio
async def test_the_network_goes_by_the_name_its_memberships_know(registry, local_agents):
    # network_status names it for people; the blocks on disk know the bare name.
    local_agents.answers["/s/home.sock"] = _status(network_label("home-net", "kollabor.ai"), "mac-box", ROWS)

    network = await sessions_route._network_snapshot(registry)

    assert network["networks"][0]["network"] == "home-net"


@pytest.mark.asyncio
async def test_chats_here_are_asked_but_not_solo_ones_or_another_computers(registry, local_agents):
    registry["web-1"] = _chat("web-1", 33)  # this engine's chat: a-chat's daemon
    registry["solo"] = _chat("solo", 11, solo=True)  # off the mesh: a-other's daemon
    registry["lapis@far-box"] = _chat("lapis@far-box", 0, device="far-box")

    await sessions_route._network_snapshot(registry)

    assert local_agents.asked == ["/s/home.sock", "/s/chat.sock"]


@pytest.mark.asyncio
async def test_no_agent_on_a_network_shows_none(registry, local_agents):
    local_agents.answers["/s/home.sock"] = None  # an old agent that never answers

    network = await sessions_route._network_snapshot(registry)

    assert network["remote"] == [] and network["device"] and "networks" not in network


@pytest.mark.asyncio
async def test_a_caller_that_stops_early_cancels_the_asks_still_out(local_agents):
    local_agents.slow.add("/s/other.sock")

    async with aclosing(network_daemons()) as found:
        async for row, _status_answer in found:
            break
    await asyncio.sleep(0)

    assert row["socket_path"] == "/s/home.sock"
    assert "/s/other.sock" in local_agents.cancelled and local_agents.out == 0
