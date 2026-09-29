"""RelayState persistence: device_name/trust defaults, round-trips, and old files.

docs/specs/agent-network-simple-flow.md §3/§4: trust is one setting per
network (open by default), and a device has a human name. RelayStateStore
must keep loading state files written before those fields existed.
"""

import json

import pytest

from plugins.hub.relay_state import ID, RelayError, RelayStateStore


@pytest.fixture
def store(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return RelayStateStore(workspace, tmp_path / "state")


def test_fresh_state_defaults_to_open_trust_and_no_device_name(store):
    assert store.state.device_name == ""
    assert store.state.trust == "open"


def test_device_name_and_trust_round_trip_through_save_and_reload(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    state_dir = tmp_path / "state"
    first = RelayStateStore(workspace, state_dir)
    first.state.device_name = "mac-kollab"
    first.state.trust = "agents"
    first.save()

    reloaded = RelayStateStore(workspace, state_dir)
    assert reloaded.state.device_name == "mac-kollab"
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
