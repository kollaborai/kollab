"""Operator diagnostics reuse signed discovery and the real encrypted client."""

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from plugins.hub import config_sync
from plugins.hub.dns.discovery import DiscoveryError, normalize_target, verify_manifest
from plugins.hub.dns.discovery_publish import publish
from plugins.hub.relay_client import RelayClient, RelayError
from plugins.hub.relay_state import is_machine_network_state_dir
from scripts.relay import peer_probe
from tests.unit.test_relay_application_transport import pair  # noqa: F401


@pytest.mark.parametrize("layout", ["network/member", "workspaces/work/networks/member"])
def test_ownership_resolves_root_and_state_aliases(tmp_path, layout):
    root = tmp_path / "actual"
    root.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    assert is_machine_network_state_dir(root / layout, alias)
    assert is_machine_network_state_dir(alias / layout, root)


@pytest.mark.parametrize(
    "outside",
    [
        "actual-copy/network/member",
        "actual/network-copy/member",
        "actual/workspaces-copy/work/networks/member",
        "actual/workspaces/work/networks-copy/member",
    ],
)
def test_ownership_rejects_lookalike_outside_paths(tmp_path, outside):
    assert not is_machine_network_state_dir(tmp_path / outside, tmp_path / "actual")


def test_config_sync_preserves_its_root_accessor(tmp_path, monkeypatch):
    root = tmp_path / "actual"
    root.mkdir()
    alias = tmp_path / "alias"
    alias.symlink_to(root, target_is_directory=True)
    monkeypatch.setattr(config_sync, "kollab_root", lambda _root: alias)
    assert config_sync.is_machine_network_state_dir(root / "network/member")


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", ["network/member", "workspaces/work/networks/member"])
@pytest.mark.parametrize("root_alias", [False, True])
@pytest.mark.parametrize("state_alias", [False, True])
async def test_probe_rejects_managed_state_before_constructing_client(
    tmp_path, monkeypatch, layout, root_alias, state_alias
):
    home = tmp_path / "home"
    home.mkdir()
    root = home / ".kollab"
    if root_alias:
        actual = tmp_path / "actual"
        actual.mkdir()
        root.symlink_to(actual, target_is_directory=True)
    else:
        root.mkdir()
    state_root = root
    if state_alias:
        state_root = tmp_path / "state-alias"
        state_root.symlink_to(root, target_is_directory=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))
    factory = Mock(side_effect=AssertionError("client must not be constructed"))
    monkeypatch.setattr(peer_probe, "RelayClient", factory)
    args = SimpleNamespace(state_dir=state_root / layout, workspace=tmp_path / "workspace")

    with pytest.raises(RelayError) as rejected:
        await peer_probe.run(args)

    assert peer_probe.error_result(rejected.value) == {"error": "managed_state"}
    factory.assert_not_called()
    assert not (root / layout).exists()



def linked_layout(tmp_path, monkeypatch, location, root_alias=False, target_exists=True):
    home = tmp_path / "home"
    home.mkdir()
    root = home / ".kollab"
    if root_alias:
        actual_root = tmp_path / "actual-root"
        actual_root.mkdir()
        root.symlink_to(actual_root, target_is_directory=True)
    else:
        root.mkdir()
    external = tmp_path / "external"
    if target_exists:
        external.mkdir()
    layouts = {
        "network": (root / "network", external),
        "workspaces": (root / "workspaces", external),
        "workspace": (root / "workspaces/work", external),
        "networks": (root / "workspaces/work/networks", external),
    }
    alias, target = layouts[location]
    alias.parent.mkdir(parents=True, exist_ok=True)
    alias.symlink_to(target, target_is_directory=True)
    tail = {
        "network": "member",
        "workspaces": "work/networks/member",
        "workspace": "networks/member",
        "networks": "member",
    }[location]
    physical = external / tail
    if target_exists:
        physical.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))
    return alias / tail, physical


@pytest.mark.asyncio
@pytest.mark.parametrize("location", ["network", "workspaces", "workspace", "networks"])
@pytest.mark.parametrize("physical_input", [False, True])
@pytest.mark.parametrize("root_alias", [False, True])
async def test_probe_rejects_linked_layout_before_client_construction(
    tmp_path, monkeypatch, location, physical_input, root_alias
):
    alias, physical = linked_layout(tmp_path, monkeypatch, location, root_alias)
    state = physical if physical_input else alias
    assert is_machine_network_state_dir(state)
    assert config_sync.is_machine_network_state_dir(state)
    assert not is_machine_network_state_dir(tmp_path / "outside/network/member")
    assert not is_machine_network_state_dir(tmp_path / "external-copy/member")
    factory = Mock(side_effect=AssertionError("managed client must not be constructed"))
    monkeypatch.setattr(peer_probe, "RelayClient", factory)

    with pytest.raises(RelayError) as rejected:
        await peer_probe.run(SimpleNamespace(state_dir=state, workspace=tmp_path / "workspace"))

    assert peer_probe.error_result(rejected.value) == {"error": "managed_state"}
    factory.assert_not_called()
    assert not (physical / "device.key").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("location", ["network", "workspaces", "workspace", "networks"])
async def test_dangling_layout_alias_owns_its_future_target(tmp_path, monkeypatch, location):
    alias, physical = linked_layout(tmp_path, monkeypatch, location, target_exists=False)
    assert is_machine_network_state_dir(alias)
    assert is_machine_network_state_dir(physical)
    factory = Mock(side_effect=AssertionError("managed client must not be constructed"))
    monkeypatch.setattr(peer_probe, "RelayClient", factory)

    with pytest.raises(RelayError) as rejected:
        await peer_probe.run(SimpleNamespace(state_dir=physical, workspace=tmp_path / "workspace"))

    assert peer_probe.error_result(rejected.value) == {"error": "managed_state"}
    factory.assert_not_called()
    assert not physical.exists()


def test_non_workspace_files_do_not_claim_outside_state(tmp_path):
    workspaces = tmp_path / "root/workspaces"
    workspaces.mkdir(parents=True)
    (workspaces / "index.json").write_text("{}")
    assert not is_machine_network_state_dir(tmp_path / "outside/member", tmp_path / "root")


@pytest.mark.asyncio
@pytest.mark.parametrize("location", ["network", "workspaces", "workspace", "networks"])
@pytest.mark.parametrize("physical_input", [False, True])
async def test_linked_managed_clients_keep_join_and_leave_hooks(
    tmp_path, monkeypatch, location, physical_input
):
    from plugins.hub import device_primary

    alias, physical = linked_layout(tmp_path, monkeypatch, location)
    state = physical if physical_input else alias
    inviter = RelayClient(tmp_path / "inviter", state_dir=tmp_path / "inviter-state")
    inviter.state.origin = "https://relay.example"
    reconcile = Mock()
    retire = Mock()
    monkeypatch.setattr(device_primary, "reconcile_join_with_choice", reconcile)
    monkeypatch.setattr(device_primary, "retire_primary_network", retire)
    client = RelayClient(tmp_path / "workspace", state_dir=state)

    client.join_invite(inviter.invite())
    reconcile.assert_called_once_with(
        state_dir=state, origin=inviter.state.origin, joined_primary_key=inviter.public_key
    )
    await client.leave()
    retire.assert_called_once_with(
        inviter.public_key, state_dir=state, state_origin=inviter.state.origin
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "location", ["network", "workspaces", "workspaces/work", "workspaces/work/networks"]
)
async def test_unresolvable_layout_is_not_permission_to_construct_probe(
    tmp_path, monkeypatch, location
):
    home = tmp_path / "home"
    root = home / ".kollab"
    loop = root / location
    loop.parent.mkdir(parents=True)
    loop.symlink_to(loop.name, target_is_directory=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))
    factory = Mock(side_effect=AssertionError("ownership must be established first"))
    monkeypatch.setattr(peer_probe, "RelayClient", factory)

    with pytest.raises(RelayError) as rejected:
        await peer_probe.run(
            SimpleNamespace(state_dir=tmp_path / "outside/member", workspace=tmp_path / "workspace")
        )

    assert peer_probe.error_result(rejected.value) == {"error": "state_ownership"}
    factory.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["resolution", "listing", "workspace_stat"])
async def test_inaccessible_layout_fails_before_probe_construction(
    tmp_path, monkeypatch, failure
):
    home = tmp_path / "home"
    root = home / ".kollab"
    workspace = root / "workspaces/work"
    workspace.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda _cls: home))
    original_resolve = Path.resolve
    original_iterdir = Path.iterdir
    original_stat = Path.stat

    def resolve(path, strict=False):
        if failure == "resolution" and path == root / "network" and strict:
            raise PermissionError("private layout sentinel")
        return original_resolve(path, strict=strict)

    def iterdir(path):
        if failure == "listing" and path == root / "workspaces":
            raise PermissionError("private layout sentinel")
        return original_iterdir(path)

    def stat(path, *args, **kwargs):
        if failure == "workspace_stat" and path == workspace:
            raise PermissionError("private layout sentinel")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setattr(Path, "iterdir", iterdir)
    monkeypatch.setattr(Path, "stat", stat)
    factory = Mock(side_effect=AssertionError("ownership must be established first"))
    monkeypatch.setattr(peer_probe, "RelayClient", factory)

    with pytest.raises(RelayError) as rejected:
        await peer_probe.run(
            SimpleNamespace(state_dir=tmp_path / "outside/member", workspace=tmp_path / "workspace")
        )

    assert peer_probe.error_result(rejected.value) == {"error": "state_ownership"}
    factory.assert_not_called()


@pytest.mark.asyncio
async def test_isolated_join_and_leave_need_no_machine_configuration(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "plugins.hub.device_primary", None)
    first = RelayClient(tmp_path / "first", state_dir=tmp_path / "first-state")
    second = RelayClient(tmp_path / "second", state_dir=tmp_path / "second-state")
    first.state.origin = "https://relay.example"
    first._store.save()

    assert second.join_invite(first.invite()) == first.state.origin
    assert second.state.approvals == [first.public_key]
    await second.leave()
    assert second.state.origin == ""
    assert second.state.approvals == []


@pytest.fixture
def discovered(tmp_path):
    manifest = publish(
        "https://relay.example",
        tmp_path / "publisher-state",
        tmp_path / "public/agent-keys.json",
        relay_control="https://relay.example/relay/v1",
    )
    return verify_manifest(manifest, normalize_target("https://relay.example"))


@pytest.mark.asyncio
async def test_invitation_is_private_exclusive_and_discovery_is_pinned(
    tmp_path, monkeypatch, discovered
):
    first = RelayClient(tmp_path / "first", state_dir=tmp_path / "first-state")
    second = RelayClient(tmp_path / "second", state_dir=tmp_path / "second-state")
    first.state.origin = discovered.origin
    invitation_file = tmp_path / "invitation"
    result = await peer_probe.command(first, "invite " + str(invitation_file))

    assert result == "invitation saved"
    assert first.invite() not in result
    assert invitation_file.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        await peer_probe.command(first, "invite " + str(invitation_file))

    discover = AsyncMock(return_value=discovered)
    connect = AsyncMock(return_value={"state": "online"})
    monkeypatch.setattr(peer_probe, "discover", discover)
    monkeypatch.setattr(second, "connect", connect)

    await peer_probe.attach(second, "", invitation_file)

    discover.assert_awaited_once_with(discovered.origin)
    connect.assert_awaited_once_with(
        discovered.origin, ws_url="wss://relay.example/relay/v1/ws"
    )
    assert second.state.inviter == first.public_key
    assert list((second.state_dir / "discovered").glob("*.json"))

    rotated = publish(
        discovered.origin,
        tmp_path / "rotated-publisher",
        tmp_path / "rotated-public/agent-keys.json",
        relay_control=discovered.origin + "/relay/v1",
    )
    discover.return_value = verify_manifest(rotated, normalize_target(discovered.origin))
    with pytest.raises(DiscoveryError) as rejected:
        await peer_probe.attach(second, discovered.origin)
    assert rejected.value.code == "key_changed"
    assert connect.await_count == 1


@pytest.mark.asyncio
async def test_public_or_symlink_invitation_is_rejected_before_discovery(tmp_path, monkeypatch):
    client = RelayClient(tmp_path / "workspace", state_dir=tmp_path / "state")
    invitation = tmp_path / "invitation"
    invitation.write_text("private invitation sentinel")
    invitation.chmod(0o644)
    discover = AsyncMock(side_effect=AssertionError("must not discover"))
    monkeypatch.setattr(peer_probe, "discover", discover)

    with pytest.raises(RelayError):
        await peer_probe.attach(client, "", invitation)

    invitation.chmod(0o600)
    alias = tmp_path / "alias"
    alias.symlink_to(invitation)
    with pytest.raises(OSError):
        await peer_probe.attach(client, "", alias)
    discover.assert_not_awaited()


@pytest.mark.asyncio
async def test_probe_ping_preserves_crypto_approval_and_no_application_handler(pair):  # noqa: F811
    left, right, wire = pair
    assert left._request_handler is None and right._request_handler is None
    pong = await peer_probe.command(left, "ping " + right.public_key)
    assert pong["workspace_id"] == right.state.workspace_id
    assert all("ciphertext" in frame for _, frame in wire.sent)
    with pytest.raises(RelayError, match="not_supported"):
        await left.request(right.public_key, "directory", {})
    await peer_probe.command(left, "revoke " + right.public_key)
    with pytest.raises(RelayError, match="explicit local approval"):
        await peer_probe.command(left, "ping " + right.public_key)


@pytest.mark.asyncio
async def test_stdin_bounds_errors_and_always_disables_client(tmp_path, monkeypatch, capsys):
    closed = []
    commands = []

    class Client:
        def status(self):
            return {"state": "online"}

        async def close(self, *, disable):
            closed.append(disable)

    monkeypatch.setattr(peer_probe, "RelayClient", lambda *_args, **_kwargs: Client())
    monkeypatch.setattr(peer_probe, "attach", AsyncMock(return_value={"state": "online"}))

    async def command(_client, value):
        commands.append(value)
        raise ValueError("private invitation sentinel")

    monkeypatch.setattr(peer_probe, "command", command)
    monkeypatch.setattr(
        sys, "stdin", io.StringIO("x" * 5000 + "approve must-not-run\nbad\nquit\n")
    )
    await peer_probe.run(
        SimpleNamespace(
            workspace=tmp_path / "workspace",
            state_dir=tmp_path / "state",
            origin="https://relay.example",
            invitation_file=None,
        )
    )

    output = capsys.readouterr().out
    rows = [json.loads(line) for line in output.splitlines()]
    assert commands == ["bad"]
    assert rows[1]["result"] == rows[2]["result"] == {"error": "ValueError"}
    assert "private invitation sentinel" not in output
    assert "must-not-run" not in output
    assert closed == [True]

