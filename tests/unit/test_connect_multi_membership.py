"""Multi-membership surface on /connect (issue #158 CLI slice).

The membership registry (issue #162 slice 3) is real on main, so the
present-path tests drive the actual ``Memberships`` reader over on-disk
``networks/<slug>/state.json`` layouts in a tmp home. Absent-path tests
pin the degradation: an unusable registry must leave the v1 single-network
behavior byte-for-byte.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugins.hub.device_record import NETWORKS_DIR_NAME, DeviceRecord, workspace_dir
from plugins.hub.plugin import format_connect_help
from plugins.hub.relay_commands import NO_NETWORK, RelayCommands

BRIDGE = SimpleNamespace(
    trust_level=lambda: "open",
    device_name=lambda: "laptop",
    plugin=SimpleNamespace(_presence=None, _identity=None),
    _enrollment_issuer=None,
    effective_trust=None,
)


def make_commands(tmp_path: Path) -> RelayCommands:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    return RelayCommands(
        workspace, state_dir=tmp_path / "relay-state", agent_bridge=BRIDGE
    )


def make_membership(
    tmp_path: Path,
    workspace: Path,
    slug: str,
    *,
    origin: str = "",
    network_name: str = "",
    trust: str = "open",
    enabled: bool = True,
) -> Path:
    """Create one on-disk membership the real registry will discover."""
    state_dir = workspace_dir(workspace) / NETWORKS_DIR_NAME / slug
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "state.json").write_text(
        json.dumps(
            {
                "origin": origin,
                "enabled": enabled,
                "network_name": network_name,
                "trust": trust,
                "device_name": "laptop",
            }
        )
    )
    return state_dir


def connected(commands: RelayCommands, origin: str) -> None:
    commands.client.state.origin = origin
    commands.client._store.save()


@pytest.fixture()
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


# --------------------------------------------------------------------- #
# status
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_status_without_memberships_is_the_v1_block(tmp_home):
    commands = make_commands(tmp_home)
    connected(commands, "https://kollabor.ai")

    status = await commands.format_status()

    assert status.startswith("network ")
    assert "networks:" not in status
    assert "join code run /connect code" in status


@pytest.mark.asyncio
async def test_status_with_no_network_stays_v1(tmp_home):
    commands = make_commands(tmp_home)

    status = await commands.format_status()

    assert status.startswith(NO_NETWORK)
    assert "networks:" not in status


@pytest.mark.asyncio
async def test_status_absent_registry_falls_back_even_with_layout(
    tmp_home, monkeypatch
):
    """A broken registry must not change the output of a joined device."""
    workspace = tmp_home / "workspace"
    workspace.mkdir()
    make_membership(
        tmp_home, workspace, "alpha", origin="https://alpha.example", enabled=True
    )
    commands = make_commands(tmp_home)
    connected(commands, "https://kollabor.ai")
    monkeypatch.setattr(
        RelayCommands, "_membership_rows", lambda self: None, raising=True
    )

    status = await commands.format_status()

    assert status.startswith("network ")
    assert "networks:" not in status


@pytest.mark.asyncio
async def test_status_registry_without_list_is_absent(tmp_home, monkeypatch):
    """Duck-typing: a Memberships without .list() degrades, never raises."""
    fake = types.ModuleType("plugins.hub.membership_runtime")

    class Memberships:  # no list()
        def __init__(self, workspace):
            pass

    fake.Memberships = Memberships
    monkeypatch.setitem(sys.modules, "plugins.hub.membership_runtime", fake)
    commands = make_commands(tmp_home)
    connected(commands, "https://kollabor.ai")

    status = await commands.format_status()

    assert status.startswith("network ")
    assert "networks:" not in status


@pytest.mark.asyncio
async def test_status_multi_membership_header_and_compact_rows(tmp_home):
    workspace = tmp_home / "workspace"
    workspace.mkdir()
    make_membership(
        tmp_home,
        workspace,
        "alpha",
        origin="https://alpha.example",
        network_name="alpha",
    )
    make_membership(
        tmp_home,
        workspace,
        "beta",
        origin="https://beta.example",
        network_name="beta",
        trust="agents",
        enabled=False,
    )
    commands = make_commands(tmp_home)
    connected(commands, "https://kollabor.ai")

    status = await commands.format_status()

    assert "device laptop — networks: alpha (primary), beta" in status
    # the connected network keeps its full block
    assert "network kollabor.ai  trust: open" in status
    assert "join code run /connect code" in status
    # siblings are compact, one line each, with their own truth
    assert "  alpha  alpha  via alpha.example" in status
    assert "not connected in this window" in status
    assert "  beta  beta  via beta.example  trust: agents  reconnect disabled" in status


@pytest.mark.asyncio
async def test_status_primary_marker_follows_device_record(tmp_home):
    workspace = tmp_home / "workspace"
    workspace.mkdir()
    make_membership(tmp_home, workspace, "alpha", origin="https://alpha.example")
    make_membership(tmp_home, workspace, "beta", origin="https://beta.example")
    record = DeviceRecord()
    record.payload["networks"] = {
        "alpha": {
            "origin": "https://alpha.example",
            "network_name": "alpha",
            "joined_at": 1,
        },
        "beta": {
            "origin": "https://beta.example",
            "network_name": "beta",
            "joined_at": 2,
        },
    }
    record.payload["primary"] = "beta"
    record.save()
    commands = make_commands(tmp_home)
    connected(commands, "https://kollabor.ai")

    status = await commands.format_status()

    assert "device laptop — networks: alpha, beta (primary)" in status


@pytest.mark.asyncio
async def test_membership_line_survives_unreadable_state(tmp_home):
    workspace = tmp_home / "workspace"
    workspace.mkdir()
    slug_dir = make_membership(tmp_home, workspace, "alpha")
    (slug_dir / "state.json").write_text("{not json")
    make_membership(tmp_home, workspace, "beta", origin="https://beta.example")

    commands = make_commands(tmp_home)
    connected(commands, "https://kollabor.ai")

    status = await commands.format_status()

    assert "  alpha  (state unreadable)" in status


# --------------------------------------------------------------------- #
# leave
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_leave_multi_membership_without_arg_lists_and_keeps(
    tmp_home, monkeypatch
):
    workspace = tmp_home / "workspace"
    workspace.mkdir()
    make_membership(tmp_home, workspace, "alpha", origin="https://alpha.example")
    make_membership(tmp_home, workspace, "beta", origin="https://beta.example")
    commands = make_commands(tmp_home)
    connected(commands, "https://kollabor.ai")
    leaves = []

    async def fake_leave():
        leaves.append(1)

    monkeypatch.setattr(commands.client, "leave", fake_leave, raising=True)

    result = await commands.run("leave")

    assert "alpha" in result and "beta" in result
    assert "/connect leave <network>" in result
    assert leaves == []


@pytest.mark.asyncio
async def test_leave_named_sibling_explains_without_leaving(tmp_home, monkeypatch):
    workspace = tmp_home / "workspace"
    workspace.mkdir()
    make_membership(tmp_home, workspace, "alpha", origin="https://alpha.example")
    commands = make_commands(tmp_home)
    connected(commands, "https://kollabor.ai")
    leaves = []

    async def fake_leave():
        leaves.append(1)

    monkeypatch.setattr(commands.client, "leave", fake_leave, raising=True)

    result = await commands.run("leave alpha.example")

    assert "not the network this window is connected to" in result
    assert "issue #162" in result
    assert leaves == []


@pytest.mark.asyncio
async def test_leave_named_connected_network_still_leaves(tmp_home, monkeypatch):
    cleared = []
    monkeypatch.setattr(
        "plugins.hub.relay_commands.clear_managed_config",
        lambda *a, **k: cleared.append(1),
    )
    commands = make_commands(tmp_home)
    connected(commands, "https://kollabor.ai")
    leaves = []

    async def fake_leave():
        leaves.append(1)

    monkeypatch.setattr(commands.client, "leave", fake_leave, raising=True)

    result = await commands.run("leave kollabor.ai")

    assert "left the network" in result
    assert leaves == [1]
    assert cleared == [1]


# --------------------------------------------------------------------- #
# join
# --------------------------------------------------------------------- #


def fake_result(origin: str) -> SimpleNamespace:
    """A discovery result whose relay advertisement _relay_url accepts."""
    return SimpleNamespace(
        origin=origin,
        summary=lambda: f"beacon at {origin}",
        manifest={
            "endpoints": {"control": origin + "/relay/v1"},
            "coordinator": {"protocols": ["kollab-relay/1"]},
            "discovery": {"roles": ["relay", "rendezvous"]},
        },
    )


@pytest.mark.asyncio
async def test_join_second_network_does_not_leave_the_first(tmp_home, monkeypatch):
    commands = make_commands(tmp_home)
    connected(commands, "https://kollabor.ai")
    connects = []

    async def discover(value):
        return fake_result("https://other.example"), "", (), False

    monkeypatch.setattr(commands, "_discover", discover)
    monkeypatch.setattr(
        commands.client,
        "connect",
        lambda *a, **k: connects.append(a),
        raising=True,
    )

    result = await commands.run("https://other.example")

    assert "already on kollabor.ai" in result
    assert "second membership" in result
    assert "issue #162" in result
    assert "/connect leave" in result
    assert connects == []


@pytest.mark.asyncio
async def test_join_second_network_uses_bridge_joiner_when_offered(
    tmp_home, monkeypatch
):
    commands = make_commands(tmp_home)
    connected(commands, "https://kollabor.ai")
    joined = []

    async def discover(value):
        return fake_result("https://other.example"), "", (), False

    async def joiner(origin):
        joined.append(origin)
        return f"joined {origin} as a second membership"

    monkeypatch.setattr(commands, "_discover", discover)
    monkeypatch.setattr(BRIDGE, "join_membership", joiner, raising=False)

    result = await commands.run("https://other.example")

    assert result == "joined https://other.example as a second membership"
    assert joined == ["https://other.example"]


@pytest.mark.asyncio
async def test_join_same_origin_reconnect_is_unchanged(tmp_home, monkeypatch):
    commands = make_commands(tmp_home)
    connected(commands, "https://kollabor.ai")
    connects = []

    async def discover(value):
        return fake_result("https://kollabor.ai"), "", (), False

    async def connect(origin, **kwargs):
        connects.append(origin)
        return None

    monkeypatch.setattr(commands, "_discover", discover)
    monkeypatch.setattr(commands.client, "connect", connect, raising=True)

    result = await commands.run("")

    assert "already on" not in result
    assert connects == ["https://kollabor.ai"]
    assert "join code run /connect code" in result


# --------------------------------------------------------------------- #
# primary
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_primary_usage_when_empty(tmp_home):
    commands = make_commands(tmp_home)

    result = await commands.run("primary")

    assert result == "usage: /connect primary <network>"


@pytest.mark.asyncio
async def test_primary_guidance_when_module_absent(tmp_home, monkeypatch):
    monkeypatch.setitem(sys.modules, "plugins.hub.device_primary", None)
    commands = make_commands(tmp_home)

    result = await commands.run("primary beta.example")

    assert "issue #164" in result


@pytest.mark.asyncio
async def test_primary_sets_and_passes_inviter_for_connected_network(
    tmp_home, monkeypatch
):
    calls = []

    def set_primary_network(primary_key, network, state_dir=None):
        calls.append((primary_key, network, state_dir))
        return True

    fake = types.ModuleType("plugins.hub.device_primary")
    fake.set_primary_network = set_primary_network
    monkeypatch.setitem(sys.modules, "plugins.hub.device_primary", fake)
    commands = make_commands(tmp_home)
    connected(commands, "https://kollabor.ai")
    commands.client.state.inviter = "inviter-key"

    result = await commands.run("primary kollabor.ai")

    assert result == "primary network set: kollabor.ai"
    assert calls == [("inviter-key", "kollabor.ai", commands.client.state_dir)]


@pytest.mark.asyncio
async def test_primary_empty_key_for_unconnected_network(tmp_home, monkeypatch):
    calls = []

    def set_primary_network(primary_key, network, state_dir=None):
        calls.append((primary_key, network))
        return False

    fake = types.ModuleType("plugins.hub.device_primary")
    fake.set_primary_network = set_primary_network
    monkeypatch.setitem(sys.modules, "plugins.hub.device_primary", fake)
    commands = make_commands(tmp_home)
    connected(commands, "https://kollabor.ai")

    result = await commands.run("primary other.example")

    assert "could not" in result or "not a network" in result
    assert calls == [("", "other.example")]


@pytest.mark.asyncio
async def test_primary_setter_errors_surface(tmp_home, monkeypatch):
    from plugins.hub.relay_state import RelayError

    def set_primary_network(primary_key, network, state_dir=None):
        raise RelayError("device record locked")

    fake = types.ModuleType("plugins.hub.device_primary")
    fake.set_primary_network = set_primary_network
    monkeypatch.setitem(sys.modules, "plugins.hub.device_primary", fake)
    commands = make_commands(tmp_home)

    result = await commands.run("primary beta.example")

    assert result == "connect: device record locked"


# --------------------------------------------------------------------- #
# help
# --------------------------------------------------------------------- #


def test_help_lists_primary_subcommand():
    help_text = format_connect_help()

    assert "/connect primary" in help_text
    assert "primary manages this device" in help_text
