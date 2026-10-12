"""Membership registry per (workspace, network) — issue #158 slice 3.

Contract under test:
- Memberships discovers membership dirs under
  ~/.kollab/workspaces/<digest>/networks/<slug>/ (v2 layout from slice 1).
- With one membership, primary() is that membership and state_dir matches
  the slice-1 resolver — N=1 keeps today's single-membership behavior.
- The device record's primary network wins when it names one of this
  workspace's memberships; otherwise deterministic first slug.
- register() enforces the 8-membership cap and is idempotent per origin.
- resolve() maps None to primary and rejects unknown slugs.
"""

import hashlib
import json
import os
import stat
from pathlib import Path

import pytest
from nacl.signing import SigningKey

from plugins.hub.device_record import (
    NETWORKS_DIR_NAME,
    origin_slug,
    workspace_dir,
)
from plugins.hub.membership_runtime import (
    MAX_MEMBERSHIPS_PER_WORKSPACE,
    Memberships,
)
from plugins.hub.relay_state import RelayError


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def _membership_dir(workspace: Path, home: Path, slug: str) -> Path:
    digest = hashlib.sha256(str(workspace.resolve()).encode()).hexdigest()
    path = home / ".kollab" / "workspaces" / digest / NETWORKS_DIR_NAME / slug
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def _seed_state(directory: Path, origin: str):
    payload = {
        "origin": origin,
        "enabled": True,
        "workspace_id": "a" * 32,
        "device_name": "laptop-kollab",
    }
    state = directory / "state.json"
    state.write_text(json.dumps(payload))
    os.chmod(state, 0o600)


def test_empty_workspace_has_no_memberships(home, tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    memberships = Memberships(workspace)
    assert memberships.list() == []
    with pytest.raises(RelayError):
        memberships.primary()


def test_single_membership_is_primary_and_matches_slice1_resolver(home, tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    slug = origin_slug("https://relay.example.com")
    directory = _membership_dir(workspace, home, slug)
    _seed_state(directory, "https://relay.example.com")

    memberships = Memberships(workspace)
    listed = memberships.list()
    assert [m.slug for m in listed] == [slug]
    primary = memberships.primary()
    assert primary.slug == slug
    assert primary.state_dir == directory

    from plugins.hub.relay_state import default_workspace_state_dir

    assert primary.state_dir == default_workspace_state_dir(workspace)


def test_device_primary_wins_when_network_is_a_membership(home, tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    slug_a = origin_slug("https://a.example.com")
    slug_b = origin_slug("https://b.example.com")
    _seed_state(_membership_dir(workspace, home, slug_a), "https://a.example.com")
    _seed_state(_membership_dir(workspace, home, slug_b), "https://b.example.com")

    # device record names b's network primary
    from plugins.hub.device_record import DeviceRecord

    record = DeviceRecord()
    record.add_network("https://b.example.com", "b-net")

    memberships = Memberships(workspace)
    assert memberships.primary().slug == slug_b
    # device primary not among this workspace's memberships -> the
    # deterministic first slug wins (fresh registry re-reads the record)
    class ForeignPrimary:
        primary = origin_slug("https://c.example.com")

    fresh = Memberships(workspace)
    fresh._device_record = ForeignPrimary()  # type: ignore[assignment]
    assert fresh.primary().slug == min(slug_a, slug_b)


def test_register_enforces_cap_and_is_idempotent(home, tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    memberships = Memberships(workspace)
    for i in range(MAX_MEMBERSHIPS_PER_WORKSPACE):
        slug = origin_slug(f"https://net-{i}.example.com")
        _seed_state(_membership_dir(workspace, home, slug), f"https://net-{i}.example.com")
    with pytest.raises(RelayError):
        memberships.register("https://over.example.com")
    # same origin twice -> same membership, no error
    again = Memberships(workspace)
    first = again.register("https://net-0.example.com")
    assert first.slug == origin_slug("https://net-0.example.com")


def test_resolve_maps_none_to_primary_and_rejects_unknown(home, tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    slug = origin_slug("https://relay.example.com")
    _seed_state(_membership_dir(workspace, home, slug), "https://relay.example.com")
    memberships = Memberships(workspace)
    assert memberships.resolve(None).slug == slug
    assert memberships.resolve("").slug == slug
    with pytest.raises(RelayError):
        memberships.resolve("no-such-slug")


def test_unjoined_placeholder_is_not_a_membership(home, tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    _membership_dir(workspace, home, "unjoined")
    memberships = Memberships(workspace)
    assert memberships.list() == []


# -- relay.* network-arg dispatch (slice 3, part 2) -------------------------


class _FakeClient:
    def __init__(self, label: str):
        self.label = label

    def status(self):
        return {"state": "online", "label": self.label}


class _FakeCommands:
    def __init__(self, label: str):
        self.client = _FakeClient(label)


def _bridge_for_dispatch(tmp_path, home, memberships_by_slug):
    """Minimal RelayAgentBridge stand-in exercising _membership_commands."""
    import plugins.hub.relay_agent as relay_agent

    workspace = tmp_path / "project"
    workspace.mkdir(exist_ok=True)
    bridge = relay_agent.RelayAgentBridge.__new__(relay_agent.RelayAgentBridge)
    bridge.workspace = workspace.resolve()
    bridge.owner = relay_agent.WorkspaceRelayOwner(workspace)
    bridge.memberships = Memberships(workspace)
    bridge._membership_runtimes = {}
    # primary runtime is the bridge's own commands facade
    primary = bridge.memberships.primary()
    bridge.commands = _FakeCommands(f"primary:{primary.slug}")
    for slug, label in memberships_by_slug.items():
        if slug != primary.slug:
            bridge._membership_runtimes[slug] = _FakeCommands(label)
    return bridge


def test_membership_commands_resolves_none_to_primary_runtime(home, tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    slug = origin_slug("https://relay.example.com")
    _seed_state(_membership_dir(workspace, home, slug), "https://relay.example.com")
    bridge = _bridge_for_dispatch(tmp_path, home, {})
    import asyncio

    commands = asyncio.run(bridge._membership_commands(None))
    assert commands.client.status()["label"] == f"primary:{slug}"


def test_membership_commands_secondary_builds_and_caches(home, tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    slug_a = origin_slug("https://a.example.com")
    slug_b = origin_slug("https://b.example.com")
    _seed_state(_membership_dir(workspace, home, slug_a), "https://a.example.com")
    _seed_state(_membership_dir(workspace, home, slug_b), "https://b.example.com")
    # the NON-primary slug is the secondary under test
    primary_slug = Memberships(workspace).primary().slug
    secondary = slug_a if primary_slug == slug_b else slug_b
    # seed the cache like an earlier call did
    bridge = _bridge_for_dispatch(tmp_path, home, {secondary: f"secondary:{secondary}"})
    import asyncio

    commands = asyncio.run(bridge._membership_commands(secondary))
    assert commands.client.status()["label"] == f"secondary:{secondary}"
    cached = asyncio.run(bridge._membership_commands(secondary))
    assert cached is commands  # cache hit, no rebuild
    with pytest.raises(RelayError):
        asyncio.run(bridge._membership_commands("unknown-slug"))
