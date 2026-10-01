"""RelayState persistence: device_name/trust defaults, round-trips, and old files.

docs/specs/agent-network-simple-flow.md §3/§4: trust is one setting per
network (open by default), and a device has a human name. RelayStateStore
must keep loading state files written before those fields existed.
"""

import json

import pytest
from nacl.signing import SigningKey

from plugins.hub.relay_state import ID, RelayError, RelayStateStore

PEER_KEY = SigningKey.generate().verify_key.encode().hex()


@pytest.fixture
def store(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return RelayStateStore(workspace, tmp_path / "state")


def test_fresh_state_defaults_to_open_trust_and_no_device_name(store):
    assert store.state.device_name == ""
    assert store.state.trust == "open"
    assert store.state.peer_devices == {}
    assert store.state.peer_trust == {}


def test_peer_devices_and_peer_trust_round_trip_through_save_and_reload(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_dir = tmp_path / "state"
    first = RelayStateStore(workspace, state_dir)
    first.state.peer_devices = {PEER_KEY: "laptop-kollab"}
    first.state.peer_trust = {PEER_KEY: "agents"}
    first.save()

    reloaded = RelayStateStore(workspace, state_dir)
    assert reloaded.state.peer_devices == {PEER_KEY: "laptop-kollab"}
    assert reloaded.state.peer_trust == {PEER_KEY: "agents"}


def test_older_state_file_missing_peer_devices_and_peer_trust_still_loads(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_dir = tmp_path / "state"
    first = RelayStateStore(workspace, state_dir)
    old_payload = {
        k: v
        for k, v in json.loads(first.state_path.read_text()).items()
        if k not in ("peer_devices", "peer_trust")
    }
    assert "peer_devices" not in old_payload and "peer_trust" not in old_payload
    first.state_path.write_text(json.dumps(old_payload))

    reloaded = RelayStateStore(workspace, state_dir)
    assert reloaded.state.peer_devices == {}
    assert reloaded.state.peer_trust == {}


def test_invalid_peer_device_key_is_rejected_on_save(store):
    store.state.peer_devices = {"not-a-key": "laptop-kollab"}
    with pytest.raises(RelayError):
        store.save()


def test_invalid_peer_device_name_is_rejected_on_save(store):
    store.state.peer_devices = {PEER_KEY: "Not Valid!"}
    with pytest.raises(RelayError):
        store.save()


def test_invalid_peer_trust_value_is_rejected_on_save(store):
    store.state.peer_trust = {PEER_KEY: "manual"}
    with pytest.raises(RelayError):
        store.save()


def test_invalid_peer_trust_key_is_rejected_on_save(store):
    store.state.peer_trust = {"not-a-key": "agents"}
    with pytest.raises(RelayError):
        store.save()


def test_device_name_and_trust_round_trip_through_save_and_reload(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_dir = tmp_path / "state"
    first = RelayStateStore(workspace, state_dir)
    first.state.device_name = "laptop-kollab"
    first.state.trust = "agents"
    first.save()

    reloaded = RelayStateStore(workspace, state_dir)
    assert reloaded.state.device_name == "laptop-kollab"
    assert reloaded.state.trust == "agents"


def test_older_state_file_missing_device_name_and_trust_still_loads(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_dir = tmp_path / "state"
    first = RelayStateStore(workspace, state_dir)
    old_payload = {
        k: v
        for k, v in json.loads(first.state_path.read_text()).items()
        if k not in ("device_name", "trust")
    }
    assert "device_name" not in old_payload and "trust" not in old_payload
    first.state_path.write_text(json.dumps(old_payload))

    reloaded = RelayStateStore(workspace, state_dir)
    assert reloaded.state.device_name == ""
    assert reloaded.state.trust == "open"
    assert ID.fullmatch(reloaded.state.workspace_id)


def test_unknown_field_in_state_file_is_still_rejected(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_dir = tmp_path / "state"
    first = RelayStateStore(workspace, state_dir)
    payload = json.loads(first.state_path.read_text())
    payload["not_a_real_field"] = "x"
    first.state_path.write_text(json.dumps(payload))

    with pytest.raises(RelayError, match="unsupported relay state fields"):
        RelayStateStore(workspace, state_dir)


def test_invalid_device_name_is_rejected_on_save(store):
    store.state.device_name = "Not Valid!"
    with pytest.raises(RelayError):
        store.save()


def test_invalid_trust_level_is_rejected_on_save(store):
    store.state.trust = "wide-open"
    with pytest.raises(RelayError):
        store.save()


def test_client_approve_and_revoke_keep_names_written_by_another_store(tmp_path):
    """The client's long-lived copy must not save stale peer names back."""
    from plugins.hub.relay_client import RelayClient

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_dir = tmp_path / "state"
    client = RelayClient(workspace, state_dir=state_dir)
    other = SigningKey.generate().verify_key.encode().hex()

    bridge_store = RelayStateStore(workspace, state_dir)
    bridge_store.state.peer_devices = {PEER_KEY: "laptop-kollab"}
    bridge_store.state.peer_trust = {PEER_KEY: "agents"}
    bridge_store.state.device_name = "laptop-kollab"
    bridge_store.save()

    client.approve(other)
    saved = RelayStateStore(workspace, state_dir).state
    assert saved.peer_devices == {PEER_KEY: "laptop-kollab"}
    assert saved.peer_trust == {PEER_KEY: "agents"}
    assert saved.device_name == "laptop-kollab"

    client.revoke(PEER_KEY)
    saved = RelayStateStore(workspace, state_dir).state
    assert saved.peer_devices == {} and saved.peer_trust == {}
    assert saved.approvals == [other]


def _bridge_writes(workspace, state_dir):
    """What /connect trust and /connect name do: a second store saves these."""
    bridge_store = RelayStateStore(workspace, state_dir)
    bridge_store.state.trust = "manual"
    bridge_store.state.device_name = "renamed-box"
    bridge_store.state.peer_devices = {PEER_KEY: "laptop-kollab"}
    bridge_store.state.peer_trust = {PEER_KEY: "agents"}
    bridge_store.save()


def _client(tmp_path):
    from plugins.hub.relay_client import RelayClient

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_dir = tmp_path / "state"
    return RelayClient(workspace, state_dir=state_dir), workspace, state_dir


@pytest.mark.asyncio
async def test_close_and_rotate_keep_the_trust_and_name_the_bridge_saved(tmp_path):
    """The client's long-lived copy must not revert trust to open on any save."""
    client, workspace, state_dir = _client(tmp_path)
    _bridge_writes(workspace, state_dir)

    await client.close(disable=True)
    saved = RelayStateStore(workspace, state_dir).state
    assert (saved.trust, saved.device_name) == ("manual", "renamed-box")
    assert saved.peer_devices == {PEER_KEY: "laptop-kollab"}

    _bridge_writes(workspace, state_dir)
    client.rotate_room()
    saved = RelayStateStore(workspace, state_dir).state
    assert (saved.trust, saved.device_name) == ("manual", "renamed-box")
    # A new room makes every prior peer meaningless, so their names go.
    assert saved.peer_devices == {} and saved.peer_trust == {}


@pytest.mark.asyncio
async def test_connect_keeps_the_trust_and_name_the_bridge_saved(tmp_path, monkeypatch):
    client, workspace, state_dir = _client(tmp_path)
    _bridge_writes(workspace, state_dir)

    async def idle():
        client._first_attempt.set()

    monkeypatch.setattr(client, "_run", idle)
    await client.connect("https://kollabor.ai", ws_url="wss://kollabor.ai/relay/v1/ws")

    saved = RelayStateStore(workspace, state_dir).state
    assert (saved.trust, saved.device_name) == ("manual", "renamed-box")
    assert saved.peer_trust == {PEER_KEY: "agents"}
    assert saved.origin == "https://kollabor.ai" and saved.enabled is True

    with pytest.raises(RelayError, match="/connect leave"):
        await client.connect(
            "https://other.example", ws_url="wss://other.example/relay/v1/ws"
        )
    await client.close()


@pytest.mark.asyncio
async def test_leave_forgets_the_network_but_keeps_this_devices_name_and_trust(
    tmp_path, monkeypatch
):
    client, workspace, state_dir = _client(tmp_path)

    async def idle():
        client._first_attempt.set()

    monkeypatch.setattr(client, "_run", idle)
    await client.connect("https://kollabor.ai", ws_url="wss://kollabor.ai/relay/v1/ws")
    client.approve(PEER_KEY)
    old_room = client.state.room
    _bridge_writes(workspace, state_dir)

    await client.leave()

    saved = RelayStateStore(workspace, state_dir).state
    assert saved.origin == "" and saved.enabled is False
    assert saved.approvals == [] and saved.inviter == ""
    assert saved.room != old_room
    assert (saved.trust, saved.device_name) == ("manual", "renamed-box")
    assert saved.peer_devices == {} and saved.peer_trust == {}
    # Empty means a fresh code join is allowed again.
    from plugins.hub.enrollment_client import _destination_state_empty

    assert _destination_state_empty(client)
