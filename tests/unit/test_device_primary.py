"""The device record's primary-network choice owns the managed-config entry.

Unit tests for plugins/hub/device_primary.py (issue #158, the #121
settings-sync takeover class):

- a join never clears or rewrites the managed-config primary entry
- the primary_network write path (the device record is the only writer)
- retire_primary_network (the leave path) and _forget_stale_primary's
  retargeted behavior
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from nacl.signing import SigningKey

from kollabor_config.managed_config import (
    ManagedConfig,
    read_managed_config,
    write_managed_config,
)
from plugins.hub import device_primary
from plugins.hub.relay_client import RelayClient


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def record_for(primary_key: str, revision: int = 3) -> ManagedConfig:
    return ManagedConfig(
        primary_key=primary_key,
        primary_name="primary-kollab",
        revision=revision,
        digest="d" * 64,
        keys=(("kollabor", "llm", "active_profile"),),
        files={"skills/example/SKILL.md": "f" * 64},
    )


ALPHA_SLUG = "aaaaaaaaaaaaaaaa"
BETA_SLUG = "bbbbbbbbbbbbbbbb"


def machine_with_choice(machine: Path, primary_slug: str = ALPHA_SLUG) -> Path:
    """A machine whose device record knows alpha and beta, choosing one."""
    write_json(
        machine / ".kollab" / "device" / "device.json",
        {
            "device_id": "0123456789abcdef0123456789abcdef",
            "device_name": "machine",
            "networks": {
                ALPHA_SLUG: {
                    "origin": "alpha.example",
                    "network_name": "alpha",
                    "joined_at": 1,
                },
                BETA_SLUG: {
                    "origin": "beta.example",
                    "network_name": "beta",
                    "joined_at": 2,
                },
            },
            "primary": primary_slug,
        },
    )
    return machine


@pytest.fixture()
def machine(tmp_path, monkeypatch) -> Path:
    home = tmp_path / "machine"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


@pytest.fixture()
def keys():
    old, new = SigningKey.generate(), SigningKey.generate()
    return bytes(old.verify_key).hex(), bytes(new.verify_key).hex()


# --- a join never touches the managed-config primary entry ------------------


def test_a_join_for_another_network_never_clears_the_entry(machine, keys):
    old_key, new_key = keys
    write_managed_config(record_for(old_key))
    machine_with_choice(machine)

    cleared = device_primary.reconcile_join_with_choice(
        state_dir=machine / ".kollab" / "network" / "workspace-b",
        origin="beta.example",  # joining beta; the machine chose alpha
        joined_primary_key=new_key,
    )
    assert cleared is False
    assert read_managed_config().primary_key == old_key


def test_a_join_on_a_machine_without_a_choice_never_clears_the_entry(machine, keys):
    old_key, new_key = keys
    write_managed_config(record_for(old_key))
    # No device.json at all: a pre-v2 machine. Still no join may touch it.

    cleared = device_primary.reconcile_join_with_choice(
        state_dir=machine / ".kollab" / "network" / "workspace-b",
        origin="beta.example",
        joined_primary_key=new_key,
    )
    assert cleared is False
    assert read_managed_config().primary_key == old_key


def test_a_damaged_device_record_reads_as_no_choice_and_never_clears(machine, keys):
    old_key, new_key = keys
    write_managed_config(record_for(old_key))
    write_json(machine / ".kollab" / "device" / "device.json", {"not": "json-schema"})
    write_managed_config(record_for(old_key))  # ensure the entry survived the write

    assert device_primary.read_primary_choice() == ("", "")
    assert (
        device_primary.reconcile_join_with_choice(
            state_dir=machine / ".kollab" / "network" / "w",
            origin="alpha.example",
            joined_primary_key=new_key,
        )
        is False
    )
    assert read_managed_config().primary_key == old_key


def test_a_foreign_state_directory_never_touches_the_entry(machine, keys):
    old_key, new_key = keys
    write_managed_config(record_for(old_key))
    machine_with_choice(machine)

    # A state under a test root plays a second machine: no say over this one.
    assert (
        device_primary.reconcile_join_with_choice(
            state_dir=Path("/tmp/aq-test-repo-elsewhere/state"),
            origin="alpha.example",
            joined_primary_key=new_key,
        )
        is False
    )
    assert read_managed_config().primary_key == old_key


def test_a_join_for_the_chosen_network_clears_only_its_own_abandoned_leftover(
    machine, keys
):
    old_key, new_key = keys
    # The machine chose alpha; the entry still names an OLDER primary of the
    # same alpha network (a re-created network, a revoked key): abandoned.
    write_managed_config(record_for(old_key))
    machine_with_choice(machine, ALPHA_SLUG)

    cleared = device_primary.reconcile_join_with_choice(
        state_dir=machine / ".kollab" / "network" / "workspace-b",
        origin="alpha.example",
        joined_primary_key=new_key,
    )
    assert cleared is True
    assert read_managed_config() is None


def test_a_join_for_the_chosen_network_leaves_the_entry_of_its_own_primary(
    machine, keys
):
    old_key, _new_key = keys
    write_managed_config(record_for(old_key))
    machine_with_choice(machine, ALPHA_SLUG)

    assert (
        device_primary.reconcile_join_with_choice(
            state_dir=machine / ".kollab" / "network" / "workspace-b",
            origin="alpha.example",
            joined_primary_key=old_key,  # this join's primary holds the entry
        )
        is False
    )
    assert read_managed_config().primary_key == old_key


def test_the_join_path_in_relay_client_no_longer_clears_another_networks_entry(
    machine, keys
):
    """RelayClient._forget_stale_primary is retargeted at the device record."""
    old_key, new_key = keys
    write_managed_config(record_for(old_key))
    machine_with_choice(machine)

    RelayClient._forget_stale_primary(
        _join_namespace(machine, origin="beta.example", inviter=new_key)
    )
    assert read_managed_config().primary_key == old_key  # the #121 takeover is dead


def _join_namespace(machine: Path, *, origin: str, inviter: str):
    from types import SimpleNamespace

    return SimpleNamespace(
        state_dir=machine / ".kollab" / "network" / "workspace-b",
        state=SimpleNamespace(origin=origin, inviter=inviter),
    )


# --- the primary_network write path: the device record is the only writer ---


def test_set_primary_network_records_the_choice_and_releases_the_old_entry(
    machine, keys
):
    old_key, new_key = keys
    write_managed_config(record_for(old_key))
    machine_with_choice(machine, ALPHA_SLUG)

    assert device_primary.set_primary_network(
        new_key,
        "beta",
        state_dir=machine / ".kollab" / "network" / "workspace-b",
    )
    payload = read_json(machine / ".kollab" / "device" / "device.json")
    assert payload["primary"] == BETA_SLUG
    assert read_managed_config() is None  # alpha's entry released for beta's


def test_set_primary_network_is_idempotent_for_the_same_network(machine, keys):
    old_key, _ = keys
    write_managed_config(record_for(old_key))
    machine_with_choice(machine, ALPHA_SLUG)

    assert (
        device_primary.set_primary_network(
            old_key,
            "alpha",
            state_dir=machine / ".kollab" / "network" / "workspace-a",
        )
        is False
    )
    assert read_managed_config().primary_key == old_key  # nothing changed


def test_set_primary_network_refuses_a_network_the_machine_never_joined(machine, keys):
    old_key, _ = keys
    write_managed_config(record_for(old_key))
    machine_with_choice(machine, ALPHA_SLUG)

    assert (
        device_primary.set_primary_network(
            old_key,
            "gamma",
            state_dir=machine / ".kollab" / "network" / "workspace-a",
        )
        is False
    )
    assert read_managed_config().primary_key == old_key  # refusal clears nothing
    assert (
        read_json(machine / ".kollab" / "device" / "device.json")["primary"]
        == ALPHA_SLUG
    )


def test_set_primary_network_refuses_a_foreign_state_directory(machine, keys):
    old_key, new_key = keys
    write_managed_config(record_for(old_key))
    machine_with_choice(machine, ALPHA_SLUG)

    assert (
        device_primary.set_primary_network(
            new_key,
            "beta",
            state_dir=Path("/tmp/aq-test-repo-elsewhere/state"),
        )
        is False
    )
    assert read_managed_config().primary_key == old_key


def test_set_primary_network_writes_no_record_when_none_exists(machine, keys):
    _, new_key = keys
    assert (
        device_primary.set_primary_network(
            new_key,
            "beta",
            state_dir=machine / ".kollab" / "network" / "workspace-a",
        )
        is False
    )
    assert not (machine / ".kollab" / "device" / "device.json").exists()


def test_set_primary_network_accepts_the_flat_primary_network_spelling(machine, keys):
    old_key, new_key = keys
    write_managed_config(record_for(old_key))
    write_json(
        machine / ".kollab" / "device" / "device.json",
        {
            "device_id": "0123456789abcdef0123456789abcdef",
            "device_name": "machine",
            "networks": {
                ALPHA_SLUG: {
                    "origin": "alpha.example",
                    "network_name": "alpha",
                    "joined_at": 1,
                },
                BETA_SLUG: {
                    "origin": "beta.example",
                    "network_name": "beta",
                    "joined_at": 2,
                },
            },
            "primary_network": "alpha",
        },
    )

    assert device_primary.set_primary_network(
        new_key,
        "beta",
        state_dir=machine / ".kollab" / "network" / "workspace-b",
    )
    payload = read_json(machine / ".kollab" / "device" / "device.json")
    assert payload["primary"] == BETA_SLUG  # one spelling, one truth
    assert "primary_network" not in payload
    assert read_managed_config() is None


# --- retire_primary_network: the leave path -------------------------------


def test_leaving_the_chosen_network_releases_the_entry_and_forgets_the_choice(
    machine, keys
):
    old_key, _ = keys
    write_managed_config(record_for(old_key))
    machine_with_choice(machine, ALPHA_SLUG)

    assert device_primary.retire_primary_network(
        old_key,
        state_dir=machine / ".kollab" / "network" / "workspace-a",
        state_origin="alpha.example",  # the workspace was ON the chosen network
    )
    assert read_managed_config() is None
    payload = read_json(machine / ".kollab" / "device" / "device.json")
    assert payload["primary"] == ""  # the choice is forgotten, the record stays


def test_leaving_with_another_workspace_still_on_the_network_keeps_the_entry(
    machine, keys
):
    old_key, _ = keys
    write_managed_config(record_for(old_key))
    machine_with_choice(machine, ALPHA_SLUG)
    # A second workspace's v1 state still names alpha's origin.
    write_json(
        machine / ".kollab" / "network" / "workspace-b" / "state.json",
        {"origin": "alpha.example", "room": "r", "inviter": old_key},
    )

    assert (
        device_primary.retire_primary_network(
            old_key,
            state_dir=machine / ".kollab" / "network" / "workspace-a",
        )
        is False
    )
    assert read_managed_config().primary_key == old_key


def test_leaving_with_another_v2_workspace_still_on_the_network_keeps_the_entry(
    machine, keys
):
    old_key, _ = keys
    write_managed_config(record_for(old_key))
    machine_with_choice(machine, ALPHA_SLUG)
    write_json(
        machine
        / ".kollab"
        / "workspaces"
        / "aaaa"
        / "networks"
        / ALPHA_SLUG
        / "state.json",
        {"origin": "alpha.example"},
    )

    assert (
        device_primary.retire_primary_network(
            old_key,
            state_dir=machine / ".kollab" / "network" / "workspace-a",
        )
        is False
    )
    assert read_managed_config().primary_key == old_key


def test_leaving_a_network_that_is_not_the_choice_keeps_the_entry(machine, keys):
    old_key, _ = keys
    write_managed_config(record_for(old_key))
    machine_with_choice(machine, ALPHA_SLUG)  # chosen: alpha

    assert (
        device_primary.retire_primary_network(
            old_key,
            state_dir=machine / ".kollab" / "network" / "workspace-b",
            state_origin="beta.example",  # the workspace was on BETA, not alpha
        )
        is False
    )
    assert read_managed_config().primary_key == old_key
    # _network_still_held is not even consulted: the departing workspace was
    # not on the chosen network, so the choice stands.


def test_retiring_with_the_wrong_primary_key_keeps_the_entry(machine, keys):
    old_key, new_key = keys
    write_managed_config(record_for(old_key))
    machine_with_choice(machine, ALPHA_SLUG)

    assert (
        device_primary.retire_primary_network(
            new_key,  # not the primary the record names
            state_dir=machine / ".kollab" / "network" / "workspace-a",
        )
        is False
    )
    assert read_managed_config().primary_key == old_key


def test_retiring_on_a_machine_without_a_choice_keeps_the_entry(machine, keys):
    old_key, _ = keys
    write_managed_config(record_for(old_key))
    # No device.json: the entry predates the device record; only an explicit
    # new choice may move it, never a leave.

    assert (
        device_primary.retire_primary_network(
            old_key,
            state_dir=machine / ".kollab" / "network" / "workspace-a",
        )
        is False
    )
    assert read_managed_config().primary_key == old_key


def test_a_choice_the_record_cannot_resolve_is_never_retired_by_a_leave(machine, keys):
    old_key, _ = keys
    write_managed_config(record_for(old_key))
    write_json(
        machine / ".kollab" / "device" / "device.json",
        {
            "device_id": "0123456789abcdef0123456789abcdef",
            "device_name": "machine",
            "networks": {},
            "primary_network": "alpha",  # no entry resolves this name
        },
    )

    assert (
        device_primary.retire_primary_network(
            old_key,
            state_dir=machine / ".kollab" / "network" / "workspace-a",
        )
        is False
    )
    assert read_managed_config().primary_key == old_key


def test_a_foreign_state_directory_never_retires_this_machines_entry(machine, keys):
    old_key, _ = keys
    write_managed_config(record_for(old_key))
    machine_with_choice(machine, ALPHA_SLUG)

    assert (
        device_primary.retire_primary_network(
            old_key,
            state_dir=Path("/tmp/aq-test-repo-elsewhere/state"),
        )
        is False
    )
    assert read_managed_config().primary_key == old_key
