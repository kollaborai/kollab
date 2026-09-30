"""A join names the device's primary; a record another primary left must not outlive it."""

from kollabor_config.managed_config import (
    ManagedConfig,
    read_managed_config,
    write_managed_config,
)
from plugins.hub.relay_client import RelayClient

STALE = "a" * 64


def _record(primary_key):
    return ManagedConfig(primary_key=primary_key, primary_name="old-mac", revision=5, digest="d" * 64)


def _mkdir(path):
    path.mkdir(parents=True)
    return path


def _joiner_and_token(tmp_path, monkeypatch, *, own_home=True):
    """A device with a fresh workspace and a join code from a new primary."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    issuer = RelayClient(_mkdir(tmp_path / "issuer"), state_dir=tmp_path / "issuer-state")
    issuer.state.origin = "https://kollabor.ai"
    issuer._store.save()
    state_dir = tmp_path / "home" / ".kollab" / "network" / "joiner" if own_home else tmp_path / "elsewhere"
    joiner = RelayClient(_mkdir(tmp_path / "joiner"), state_dir=state_dir)
    return joiner, issuer._store.invite(), issuer.public_key


def test_joining_a_new_primary_drops_a_record_another_primary_left(tmp_path, monkeypatch):
    # The live M2 proof: a torn-down workspace left its primary in the machine-global
    # record, so the next network's sealed config was refused as other_primary for good.
    joiner, token, _ = _joiner_and_token(tmp_path, monkeypatch)
    write_managed_config(_record(STALE))

    joiner.join_invite(token)

    assert read_managed_config() is None


def test_rejoining_the_same_primary_keeps_its_record(tmp_path, monkeypatch):
    joiner, token, primary_key = _joiner_and_token(tmp_path, monkeypatch)
    write_managed_config(_record(primary_key))

    joiner.join_invite(token)

    assert read_managed_config().primary_key == primary_key


def test_a_state_outside_the_machines_own_home_never_touches_the_record(tmp_path, monkeypatch):
    joiner, token, _ = _joiner_and_token(tmp_path, monkeypatch, own_home=False)
    write_managed_config(_record(STALE))

    joiner.join_invite(token)

    assert read_managed_config().primary_key == STALE
