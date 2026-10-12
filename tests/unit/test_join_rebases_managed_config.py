"""A join names the device's primary; a record another primary left outlives it.

The managed-config record is machine-global, so the device record's
primary-network choice -- never a join -- decides which primary owns it
(issue #158, the #121 takeover class).
"""

from kollabor_config.managed_config import (
    ManagedConfig,
    read_managed_config,
    write_managed_config,
)
from plugins.hub.relay_client import RelayClient

STALE = "a" * 64


def _record(primary_key):
    return ManagedConfig(
        primary_key=primary_key, primary_name="old-mac", revision=5, digest="d" * 64
    )


def _mkdir(path):
    path.mkdir(parents=True)
    return path


def _joiner_and_token(tmp_path, monkeypatch, *, own_home=True):
    """A device with a fresh workspace and a join code from a new primary."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    issuer = RelayClient(
        _mkdir(tmp_path / "issuer"), state_dir=tmp_path / "issuer-state"
    )
    issuer.state.origin = "https://kollabor.ai"
    issuer._store.save()
    state_dir = (
        tmp_path / "home" / ".kollab" / "network" / "joiner"
        if own_home
        else tmp_path / "elsewhere"
    )
    joiner = RelayClient(_mkdir(tmp_path / "joiner"), state_dir=state_dir)
    return joiner, issuer._store.invite(), issuer.public_key


def test_a_join_never_drops_a_record_another_primary_left(tmp_path, monkeypatch):
    # Before #158, the latest join took the machine's record: a torn-down
    # workspace's primary (the M2 bug) was cleared by the NEXT join -- the same
    # mechanism two live workspaces used to steal settings sync from each other
    # (#121). Now the device record owns the entry: a join never touches it.
    # A leftover on a machine with no recorded choice lingers until the human
    # re-chooses (/connect primary, an explicit act); the new network's sealed
    # config is refused as other_primary until then.
    joiner, token, _ = _joiner_and_token(tmp_path, monkeypatch)
    write_managed_config(_record(STALE))

    joiner.join_invite(token)

    record = read_managed_config()
    assert record is not None
    assert record.primary_key == STALE  # the join did not take the entry
    assert joiner.state.inviter != STALE  # ...though it named its own primary


def test_rejoining_the_same_primary_keeps_its_record(tmp_path, monkeypatch):
    joiner, token, primary_key = _joiner_and_token(tmp_path, monkeypatch)
    write_managed_config(_record(primary_key))

    joiner.join_invite(token)

    assert read_managed_config().primary_key == primary_key


def test_a_state_outside_the_machines_own_home_never_touches_the_record(
    tmp_path, monkeypatch
):
    joiner, token, _ = _joiner_and_token(tmp_path, monkeypatch, own_home=False)
    write_managed_config(_record(STALE))

    joiner.join_invite(token)

    assert read_managed_config().primary_key == STALE
