"""Device record + v1->v2 workspace state migration (issue #158 slice 1).

The contract under test:
- ~/.kollab/device/{device.json, root.key} created once per machine, private.
- Membership keys per (device, network) under device/networks/<slug>/key.
- Migrating a v1 ~/.kollab/network/<digest>/ preserves the WIRE KEY (the
  legacy device.key becomes the membership key) and moves state.json
  byte-for-byte: no re-joins, peers' approvals keep validating.
- Migration is idempotent and refuses (moving nothing) on inconsistency.
- The machine-global guards (join floor, stale primary) accept both layouts.
"""

import hashlib
import json
import os
import stat
from pathlib import Path

import pytest
from nacl.signing import SigningKey

from plugins.hub.config_sync import is_machine_network_state_dir, kollab_root
from plugins.hub.device_record import (
    DeviceRecord,
    DeviceRecordError,
    migrate_legacy_network_state,
    origin_slug,
    workspace_dir,
)
from plugins.hub.relay_state import RelayState, RelayStateStore


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "kollab-root"
    root.mkdir()
    monkeypatch.setenv("HOME", str(tmp_path))
    # kollab_root() -> get_config_directory() -> Path.home()/".kollab"
    yield tmp_path
    # tmp_path teardown handles cleanup


def _legacy_state_dir(workspace: Path, home: Path) -> Path:
    digest = hashlib.sha256(str(workspace.resolve()).encode()).hexdigest()
    return home / ".kollab" / "network" / digest


def _seed_legacy(workspace: Path, home: Path, *, origin="https://relay.example.com"):
    path = _legacy_state_dir(workspace, home)
    path.mkdir(mode=0o700, parents=True)
    os.chmod(path, 0o700)
    key = SigningKey.generate()
    (path / "device.key").write_text(key.encode().hex() + "\n")
    os.chmod(path / "device.key", 0o600)
    state = RelayState(origin=origin, enabled=True, device_name="laptop-kollab")
    payload = json.loads(json.dumps(_asdict(state)))
    (path / "state.json").write_text(json.dumps(payload))
    os.chmod(path / "state.json", 0o600)
    return key


def _asdict(state: RelayState) -> dict:
    from dataclasses import asdict

    return asdict(state)


def test_device_record_created_once_private(home):
    first = DeviceRecord()
    device_id = first.device_id
    assert device_id and len(device_id) == 32
    root_key = first.root_key
    again = DeviceRecord()
    assert again.device_id == device_id
    assert again.root_key.encode() == root_key.encode()  # same machine identity
    record_dir = home / ".kollab" / "device"
    assert stat.S_IMODE(record_dir.stat().st_mode) & 0o777 == 0o700
    root_key_stat = (record_dir / "root.key").lstat()
    assert stat.S_IMODE(root_key_stat.st_mode) == 0o600
    device_json_stat = (record_dir / "device.json").lstat()
    assert stat.S_IMODE(device_json_stat.st_mode) == 0o600


def test_add_network_mints_membership_key_and_first_network_is_primary(home):
    record = DeviceRecord()
    slug = record.add_network("https://relay.example.com", "example-net")
    assert slug == origin_slug("https://relay.example.com")
    key = record.membership_key(slug)
    assert isinstance(key, SigningKey)
    assert record.primary == slug
    other = record.add_network("https://other.example.com")
    assert record.primary == slug  # first stays primary


def test_add_network_enforces_membership_cap(home):
    record = DeviceRecord()
    for i in range(64):
        record.add_network(f"https://net-{i}.example.com")
    with pytest.raises(DeviceRecordError):
        record.add_network("https://over-limit.example.com")


def test_membership_key_files_are_private(home):
    record = DeviceRecord()
    slug = record.add_network("https://relay.example.com")
    path = record.membership_key_path(slug)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) & 0o777 == 0o700


def test_migration_moves_state_preserving_wire_key(home, tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    legacy_key = _seed_legacy(workspace, home)
    assert migrate_legacy_network_state(workspace) is True

    # device record now knows the network, with the legacy key as membership key
    record = DeviceRecord()
    slug = origin_slug("https://relay.example.com")
    assert slug in record.networks()
    assert record.membership_key(slug).encode() == legacy_key.encode()

    # workspace record holds the same RelayState bytes
    state_file = workspace_dir(workspace) / "networks" / slug / "state.json"
    assert state_file.exists()
    legacy_state = json.loads((_legacy_state_dir(workspace, home) / "state.json").read_text())
    assert json.loads(state_file.read_text()) == legacy_state


def test_migration_is_idempotent(home, tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    _seed_legacy(workspace, home)
    assert migrate_legacy_network_state(workspace) is True
    assert migrate_legacy_network_state(workspace) is False


def test_migration_without_state_json_uses_key_only(home, tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    path = _legacy_state_dir(workspace, home)
    path.mkdir(mode=0o700, parents=True)
    key = SigningKey.generate()
    key_file = path / "device.key"
    key_file.write_text(key.encode().hex() + "\n")
    os.chmod(key_file, 0o600)
    assert migrate_legacy_network_state(workspace) is True
    target = workspace_dir(workspace) / "networks" / "unjoined"
    assert (target / "device.key").exists()


def test_migration_refuses_missing_key(home, tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    path = _legacy_state_dir(workspace, home)
    path.mkdir(mode=0o700, parents=True)
    state_file = path / "state.json"
    state_file.write_text(json.dumps(_asdict(RelayState(origin="https://r.example.com"))))
    os.chmod(state_file, 0o600)
    with pytest.raises(DeviceRecordError):
        migrate_legacy_network_state(workspace)


def test_migration_keeps_both_keys_when_second_folder_claims_network(home, tmp_path):
    workspace_a = tmp_path / "a"
    workspace_b = tmp_path / "b"
    workspace_a.mkdir(); workspace_b.mkdir()
    key_a = _seed_legacy(workspace_a, home)
    key_b = _seed_legacy(workspace_b, home)  # same network, different key
    assert migrate_legacy_network_state(workspace_a) is True
    assert migrate_legacy_network_state(workspace_b) is True
    record = DeviceRecord()
    slug = origin_slug("https://relay.example.com")
    assert record.membership_key(slug).encode() == key_a.encode()
    # workspace B keeps its own legacy key inside its workspace record
    target_b = workspace_dir(workspace_b) / "networks" / slug
    seed_b = (target_b / "device.key").read_text().strip()
    assert bytes.fromhex(seed_b) == bytes(key_b)


def test_default_workspace_state_dir_resolves_v2_after_migration(home, tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    _seed_legacy(workspace, home)
    from plugins.hub.relay_state import default_workspace_state_dir

    # The first resolve migrates in place and returns the v2 membership dir
    # (device primary slug); a second call is stable.
    slug = origin_slug("https://relay.example.com")
    expected = workspace_dir(workspace) / "networks" / slug
    assert default_workspace_state_dir(workspace) == expected
    assert default_workspace_state_dir(workspace) == expected


def test_guards_accept_both_layouts(home, tmp_path):
    v1 = home / ".kollab" / "network" / ("d" * 64)
    assert is_machine_network_state_dir(v1) is True
    v2 = home / ".kollab" / "workspaces" / ("d" * 64) / "networks" / "abc123"
    assert is_machine_network_state_dir(v2) is True
    assert is_machine_network_state_dir(tmp_path / "elsewhere" / "work") is False
