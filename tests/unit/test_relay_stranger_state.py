"""The `links` list in RelayState: accepted strangers reached across rooms.

docs/specs/agent-network-simple-flow.md Story 5. The list is persisted with the
rest of the relay state, validated on save, dropped by revoke and rotate, and
kept when the client saves its long-lived copy over a bridge write.
"""

import json

import pytest
from nacl.signing import SigningKey

from plugins.hub.relay_client import RelayClient
from plugins.hub.relay_state import RelayError, RelayStateStore

PEER_KEY = SigningKey.generate().verify_key.encode().hex()


def _workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace, tmp_path / "state"


def test_links_default_empty_and_round_trip(tmp_path):
    workspace, state_dir = _workspace(tmp_path)
    first = RelayStateStore(workspace, state_dir)
    assert first.state.links == []
    first.state.links = [PEER_KEY]
    first.save()

    assert RelayStateStore(workspace, state_dir).state.links == [PEER_KEY]


def test_older_state_file_missing_links_still_loads(tmp_path):
    workspace, state_dir = _workspace(tmp_path)
    first = RelayStateStore(workspace, state_dir)
    old_payload = {
        k: v for k, v in json.loads(first.state_path.read_text()).items() if k != "links"
    }
    first.state_path.write_text(json.dumps(old_payload))

    assert RelayStateStore(workspace, state_dir).state.links == []


@pytest.mark.parametrize("bad", [["not-a-key"], [PEER_KEY, PEER_KEY], "notalist", [1]])
def test_invalid_links_are_rejected_on_save(tmp_path, bad):
    workspace, state_dir = _workspace(tmp_path)
    store = RelayStateStore(workspace, state_dir)
    store.state.links = bad
    with pytest.raises((RelayError, TypeError)):
        store.save()


def test_client_revoke_and_rotate_drop_a_stranger_link(tmp_path):
    workspace, state_dir = _workspace(tmp_path)
    client = RelayClient(workspace, state_dir=state_dir)
    bridge_store = RelayStateStore(workspace, state_dir)
    bridge_store.state.links = [PEER_KEY]
    bridge_store.state.peer_trust = {PEER_KEY: "agents"}
    bridge_store.save()
    client.approve(PEER_KEY)  # adopts what the bridge saved
    assert client.state.links == [PEER_KEY]

    client.revoke(PEER_KEY)
    assert RelayStateStore(workspace, state_dir).state.links == []

    bridge_store = RelayStateStore(workspace, state_dir)
    bridge_store.state.links = [PEER_KEY]
    bridge_store.save()
    client.rotate_room()
    assert RelayStateStore(workspace, state_dir).state.links == []


def test_client_saves_keep_links_written_by_another_store(tmp_path):
    workspace, state_dir = _workspace(tmp_path)
    client = RelayClient(workspace, state_dir=state_dir)
    other = SigningKey.generate().verify_key.encode().hex()
    bridge_store = RelayStateStore(workspace, state_dir)
    bridge_store.state.links = [PEER_KEY]
    bridge_store.save()

    client.approve(other)

    assert RelayStateStore(workspace, state_dir).state.links == [PEER_KEY]


def test_a_stranger_who_joins_with_a_code_becomes_a_member(tmp_path):
    workspace, state_dir = _workspace(tmp_path)
    client = RelayClient(workspace, state_dir=state_dir)
    bridge_store = RelayStateStore(workspace, state_dir)
    bridge_store.state.links = [PEER_KEY]
    bridge_store.state.peer_trust = {PEER_KEY: "agents"}
    bridge_store.save()
    client.approve(PEER_KEY)

    client.add_config_recipient(PEER_KEY)  # the issuer just accepted its join code

    saved = RelayStateStore(workspace, state_dir).state
    assert saved.links == [] and PEER_KEY not in saved.peer_trust
    assert saved.config_recipients == [PEER_KEY] and saved.approvals == [PEER_KEY]
