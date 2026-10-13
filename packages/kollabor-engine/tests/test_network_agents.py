"""The web sidebar's network block: this computer's name and the remote agents."""

from types import SimpleNamespace

import pytest
from kollabor_engine.routes import sessions as sessions_route

from plugins.hub.messenger import AgentMessenger
from plugins.hub.relay_commands import network_label

REMOTE = [{"name": "lapis", "device": "prod-box", "handle": "lapis@prod-box", "state": "idle"}]
LAB = [{"name": "opal", "device": "lab-box", "handle": "opal@lab-box", "state": "idle"}]


def seen(network):
    """The remote rows without the roster's last_seen stamp."""
    return [{k: v for k, v in row.items() if k != "last_seen"} for row in network["remote"]]


def status(network, device, agents):
    """A daemon's network_status answer, its network named as people read it."""
    return {
        "type": "network_status",
        "network": network_label(network, "kollabor.ai"),
        "device": device,
        "trust": "open",
        "agents": agents,
    }


@pytest.fixture
def agents_here(monkeypatch):
    """Live agents on this computer: ``add(answer)`` lists one whose hub socket
    answers network_status with ``answer`` (None: it never answers)."""
    monkeypatch.setattr(sessions_route.socket, "gethostname", lambda: "devbox.local")
    rows, answers = [], {}

    def add(answer, pid=None):
        number = len(rows) + 1
        socket_path = f"/s/agent-{number}.sock"
        rows.append({
            "session_id": f"agent-{number}",
            "identity": "koordinator",
            "workspace": f"/w/folder-{number}",
            "socket_path": socket_path,
            "daemon_pid": pid or 9000 + number,
        })
        answers[socket_path] = answer

    async def network_status(socket_path, timeout=5.0, **_):
        return answers.get(socket_path)

    monkeypatch.setattr(sessions_route.HubBridge, "discover_sessions", lambda self, use_cache=False: rows)
    monkeypatch.setattr(AgentMessenger, "request_network_status", network_status)
    return add


@pytest.fixture
def registry():
    """The engine's session registry, restored after the test."""
    live = sessions_route.get_session_registry()
    before = dict(live)
    live.clear()
    yield live
    live.clear()
    live.update(before)


@pytest.mark.asyncio
async def test_network_block_names_this_computer_and_its_remote_agents(agents_here, registry):
    agents_here(status("home-net", "devbox", REMOTE))

    network = (await sessions_route.list_sessions())["network"]

    assert network["device"] == "devbox" and network["live"] is True
    assert seen(network) == REMOTE
    [block] = network["networks"]
    assert (block["name"], block["live"], block["trust"]) == ("home-net", True, "open")
    assert [row["device"] for row in block["devices"]] == ["prod-box"]


@pytest.mark.asyncio
async def test_every_network_on_this_computer_shows_under_its_own_name(agents_here, registry):
    # Two folders here on two networks: both rosters show, each under its own network.
    agents_here(status("home-net", "devbox", REMOTE))
    agents_here(status("lab-net", "devbox-lab", LAB))

    network = (await sessions_route.list_sessions())["network"]

    assert seen(network) == REMOTE + LAB
    blocks = {block["name"]: block for block in network["networks"]}
    assert set(blocks) == {"home-net", "lab-net"}
    assert [row["device"] for row in blocks["home-net"]["devices"]] == ["prod-box"]
    assert [row["device"] for row in blocks["lab-net"]["devices"]] == ["lab-box"]
    assert blocks["lab-net"]["folder"] == "/w/folder-2"


@pytest.mark.asyncio
async def test_a_daemon_that_fails_is_skipped_for_one_that_answers(agents_here, registry):
    agents_here(None)
    agents_here(status("home-net", "devbox", REMOTE))

    network = (await sessions_route.list_sessions())["network"]

    assert seen(network) == REMOTE


@pytest.mark.asyncio
async def test_solo_daemon_is_off_the_mesh_so_it_is_not_asked(agents_here, registry):
    agents_here(status("home-net", "other", REMOTE), pid=4242)
    registry["net-solo"] = SimpleNamespace(
        session_id="net-solo",
        alive=True,
        solo=True,
        device="",
        daemon=SimpleNamespace(pid=4242),
        to_dict=lambda: {"session_id": "net-solo", "daemon_pid": 4242},
    )

    network = (await sessions_route.list_sessions())["network"]

    assert network["device"] == "devbox" and network["remote"] == []
    assert network["live"] is False
    assert network["networks"] == [] and network["contacts"] == []


@pytest.mark.asyncio
async def test_no_answer_means_no_remote_rows(agents_here, registry):
    agents_here(None)

    network = (await sessions_route.list_sessions())["network"]

    assert network["device"] == "devbox" and network["remote"] == []
    assert network["live"] is False
    # The v2 aggregate (#158) adds networks/contacts; empty on this machine.
    assert network["networks"] == [] and network["contacts"] == []
