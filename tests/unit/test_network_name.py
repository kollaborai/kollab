"""A network has a name: `<first device name>-net` (docs/specs/agent-network-simple-flow.md §4).

The first device names it once, when it starts the network; a device that joins
takes the name it is given in the signed decision; leaving forgets it. The
screens show it as `network      laptop-kollab-net  via kollabor.ai`.
"""

import json

import pytest
from nacl.signing import SigningKey

from plugins.hub.device_names import (
    default_device_name,
    default_network_name,
    validate_network_name,
)
from plugins.hub.relay_client import RelayClient
from plugins.hub.relay_commands import RelayCommands
from plugins.hub.relay_state import RelayError, RelayStateStore

PEER_KEY = SigningKey.generate().verify_key.encode().hex()
DIRECTORY = "https://kollabor.ai"
WS_URL = "wss://kollabor.ai/relay/v1/ws"


def _client(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_dir = tmp_path / "state"
    return RelayClient(workspace, state_dir=state_dir), workspace, state_dir


async def _connect(client, monkeypatch):
    async def idle():
        client._first_attempt.set()

    monkeypatch.setattr(client, "_run", idle)
    await client.connect(DIRECTORY, ws_url=WS_URL)


# --------------------------------------------------------------------- #
# The rule
# --------------------------------------------------------------------- #


def test_the_default_name_is_the_first_device_name_and_net():
    assert default_network_name("laptop-kollab") == "laptop-kollab-net"
    assert validate_network_name(default_network_name("laptop-kollab")) == "laptop-kollab-net"


def test_the_default_name_stays_a_valid_name_for_the_longest_device_name():
    longest = "-".join(["lab"] * 16)  # 63 characters, the longest a device name can be
    assert len(longest) == 63

    name = default_network_name(longest)

    assert name.endswith("-net") and len(name) <= 63
    assert validate_network_name(name) == name
    # cutting the device part must not leave a dash before -net
    assert "--" not in default_network_name("a" * 58 + "-b")


@pytest.mark.parametrize("bad", ["", "Mac Kollab", "-net", "x" * 64])
def test_a_network_name_follows_the_device_name_rule(bad):
    with pytest.raises(ValueError, match="network name"):
        validate_network_name(bad)


# --------------------------------------------------------------------- #
# State and client
# --------------------------------------------------------------------- #


def test_the_name_round_trips_and_an_older_state_file_loads_without_one(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_dir = tmp_path / "state"
    first = RelayStateStore(workspace, state_dir)
    assert first.state.network_name == ""
    first.state.network_name = "laptop-kollab-net"
    first.save()
    assert RelayStateStore(workspace, state_dir).state.network_name == "laptop-kollab-net"

    old = {k: v for k, v in json.loads(first.state_path.read_text()).items() if k != "network_name"}
    first.state_path.write_text(json.dumps(old))
    assert RelayStateStore(workspace, state_dir).state.network_name == ""


def test_an_invalid_network_name_is_rejected_on_save(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = RelayStateStore(workspace, tmp_path / "state")
    store.state.network_name = "Not Valid!"

    with pytest.raises(RelayError, match="network name"):
        store.save()


@pytest.mark.asyncio
async def test_the_first_device_names_the_network_after_itself(tmp_path, monkeypatch):
    client, workspace, state_dir = _client(tmp_path)

    await _connect(client, monkeypatch)

    saved = RelayStateStore(workspace, state_dir).state
    assert saved.network_name == default_network_name(default_device_name(workspace))
    await client.close()


@pytest.mark.asyncio
async def test_a_renamed_device_names_the_network_after_its_current_name(tmp_path, monkeypatch):
    client, workspace, state_dir = _client(tmp_path)
    bridge_store = RelayStateStore(workspace, state_dir)
    bridge_store.state.device_name = "laptop-kollab"  # /connect name, before the network exists
    bridge_store.save()

    await _connect(client, monkeypatch)

    assert RelayStateStore(workspace, state_dir).state.network_name == "laptop-kollab-net"
    await client.close()


@pytest.mark.asyncio
async def test_the_name_is_chosen_once_and_survives_a_rename_and_a_reconnect(tmp_path, monkeypatch):
    client, workspace, state_dir = _client(tmp_path)
    bridge_store = RelayStateStore(workspace, state_dir)
    bridge_store.state.device_name = "laptop-kollab"
    bridge_store.save()
    await _connect(client, monkeypatch)
    await client.close()
    bridge_store = RelayStateStore(workspace, state_dir)
    bridge_store.state.device_name = "renamed-box"
    bridge_store.save()

    await _connect(client, monkeypatch)  # what RelayCommands.resume does at every start

    assert RelayStateStore(workspace, state_dir).state.network_name == "laptop-kollab-net"
    await client.close()


@pytest.mark.asyncio
async def test_a_device_that_was_invited_does_not_name_the_network(tmp_path, monkeypatch):
    client, workspace, state_dir = _client(tmp_path)
    client.state.inviter = PEER_KEY  # what joining by code recorded
    client._store.save()

    await _connect(client, monkeypatch)

    assert RelayStateStore(workspace, state_dir).state.network_name == ""
    await client.close()


@pytest.mark.asyncio
async def test_the_client_keeps_a_name_the_bridge_saved_when_it_saves_its_own_copy(tmp_path, monkeypatch):
    """A joining device takes the name through the bridge; the client's stale copy must not undo it."""
    client, workspace, state_dir = _client(tmp_path)
    client.state.inviter = PEER_KEY
    client._store.save()
    bridge_store = RelayStateStore(workspace, state_dir)
    bridge_store.state.network_name = "laptop-kollab-net"
    bridge_store.save()

    await _connect(client, monkeypatch)

    assert RelayStateStore(workspace, state_dir).state.network_name == "laptop-kollab-net"
    await client.close()


@pytest.mark.asyncio
async def test_rotate_keeps_the_name_and_leave_forgets_it(tmp_path, monkeypatch):
    client, workspace, state_dir = _client(tmp_path)
    await _connect(client, monkeypatch)
    name = RelayStateStore(workspace, state_dir).state.network_name
    assert name
    await client.close()

    client.rotate_room()
    assert RelayStateStore(workspace, state_dir).state.network_name == name

    await client.leave()
    assert RelayStateStore(workspace, state_dir).state.network_name == ""


# --------------------------------------------------------------------- #
# The bridge and the screens
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_the_bridge_reads_the_name_and_a_joining_device_takes_the_first_one_it_is_given(bridges):
    members, _ = bridges
    (left, *_), _ = members
    assert left.network_name() == ""

    assert left.bind_network_name("laptop-kollab-net") is True
    assert left.network_name() == "laptop-kollab-net"
    assert left.bind_network_name("laptop-kollab-net") is True  # repeating is harmless
    assert left.bind_network_name("other-net") is False  # the first name wins
    assert left.bind_network_name("Not Valid!") is False
    assert left.network_name() == "laptop-kollab-net"


@pytest.mark.asyncio
async def test_the_status_and_the_screen_show_the_name_with_the_directory(bridges):
    members, _ = bridges
    (left, *_), _ = members
    left.bind_network_name("laptop-kollab-net")
    left.commands.client.state.origin = DIRECTORY

    status = await left.commands.format_status()
    snapshot = await left.commands.connect_snapshot()

    assert status.splitlines()[0].startswith("network laptop-kollab-net  via kollabor.ai")
    assert (snapshot.network, snapshot.domain) == ("laptop-kollab-net", "kollabor.ai")


def test_commands_without_a_name_fall_back_to_the_directory(tmp_path):
    from types import SimpleNamespace

    from tests.unit.test_hub_network_surface import _relay_commands

    bridges = (SimpleNamespace(), SimpleNamespace(network_name=lambda: ""))
    for index, bridge in enumerate(bridges):
        (tmp_path / str(index)).mkdir()
        commands = _relay_commands(tmp_path / str(index), agent_bridge=bridge)
        assert isinstance(commands, RelayCommands)
        assert commands._network_name("kollabor.ai") == "kollabor.ai"
