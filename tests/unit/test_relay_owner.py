"""Private workspace ownership and actual Unix-socket relay RPC boundaries."""

import asyncio
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

import pytest
from nacl.signing import SigningKey, VerifyKey

from kollabor_rpc.models import RpcResponse
from plugins.hub.messenger import AgentSocketServer
from plugins.hub.relay_owner import (
    RPC_REQUEST_LIMIT,
    RPC_RESPONSE_LIMIT,
    RelayOwnerError,
    WorkspaceRelayOwner,
    local_relay_rpc,
)


def run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def owner(tmp_path):
    return WorkspaceRelayOwner(tmp_path / "workspace", tmp_path / "private-state")


def test_workspace_owner_uses_existing_state_location_without_creating_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    workspace = tmp_path / "workspace"
    instance = WorkspaceRelayOwner(workspace)
    expected = tmp_path / ".kollab/network" / hashlib.sha256(str(workspace.resolve()).encode()).hexdigest()
    assert instance.state_dir == expected
    assert not (expected / "device.key").exists()
    assert not (expected / "state.json").exists()


def test_only_one_owner_and_release_preserves_lock_inode(tmp_path):
    first, second = owner(tmp_path), owner(tmp_path)
    assert first.owner() is None
    assert first.acquire("/tmp/owner-a.sock", "agent-a")
    inode = (first.state_dir / "relay-owner.lock").stat().st_ino
    assert first.acquire("/tmp/owner-a.sock", "agent-a")
    assert not second.acquire("/tmp/owner-b.sock", "agent-b")
    assert second.owner() == {"socket_path": "/tmp/owner-a.sock", "agent_id": "agent-a", "pid": os.getpid()}
    second.release()
    assert second.owner()["agent_id"] == "agent-a"
    first.release()
    assert second.owner() is None
    assert second.acquire("/tmp/owner-b.sock", "agent-b")
    assert first.owner()["agent_id"] == "agent-b"
    assert (first.state_dir / "relay-owner.lock").stat().st_ino == inode
    second.release()


def test_takeover_after_process_exit_ignores_stale_record(tmp_path):
    state = tmp_path / "private-state"
    script = """
import os,sys
from pathlib import Path
from plugins.hub.relay_owner import WorkspaceRelayOwner
owner=WorkspaceRelayOwner(Path(sys.argv[1]),Path(sys.argv[2]))
assert owner.acquire('/tmp/dead-owner.sock','dead-agent')
os._exit(0)
"""
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path), str(state)], capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr.decode()
    instance = WorkspaceRelayOwner(tmp_path, state)
    assert (state / "relay-owner.json").exists()
    assert instance.owner() is None
    assert instance.acquire("/tmp/new-owner.sock", "new-agent")
    assert instance.owner()["agent_id"] == "new-agent"
    instance.release()


def test_takeover_replaces_corrupt_private_stale_record(tmp_path):
    instance = owner(tmp_path)
    record = instance.state_dir / "relay-owner.json"
    record.write_text("partial write")
    record.chmod(0o600)
    assert instance.acquire("/tmp/new-owner.sock", "new-agent")
    assert instance.owner()["agent_id"] == "new-agent"
    instance.release()


def test_existing_identity_and_state_are_untouched(tmp_path):
    instance = owner(tmp_path)
    for name in ("device.key", "state.json", "invite-private.txt"):
        (instance.state_dir / name).write_text("preserve " + name)
    before = {p.name: (p.read_bytes(), p.stat().st_ino) for p in instance.state_dir.iterdir()}
    assert instance.acquire("/tmp/owner.sock", "agent")
    instance.release()
    for name, expected in before.items():
        p = instance.state_dir / name
        assert (p.read_bytes(), p.stat().st_ino) == expected


def test_release_never_deletes_another_claim(tmp_path):
    instance = owner(tmp_path)
    assert instance.acquire("/tmp/owner.sock", "agent")
    path = instance.state_dir / "relay-owner.json"
    record = json.loads(path.read_text())
    record["claim"] = "0" * 32
    path.write_text(json.dumps(record))
    instance.release()
    assert path.exists()


def test_private_permissions(tmp_path):
    instance = owner(tmp_path)
    assert instance.acquire("/tmp/owner.sock", "agent")
    assert stat.S_IMODE(instance.state_dir.stat().st_mode) == 0o700
    for name in ("relay-owner.lock", "relay-owner.json"):
        assert stat.S_IMODE((instance.state_dir / name).stat().st_mode) == 0o600
    instance.release()


@pytest.mark.parametrize("kind", ["directory", "lock", "record"])
def test_symlinks_rejected_without_overwriting_target(tmp_path, kind):
    target = tmp_path / "target"
    if kind == "directory":
        target.mkdir(mode=0o700)
        (tmp_path / "private-state").symlink_to(target)
        with pytest.raises(RelayOwnerError):
            owner(tmp_path)
    else:
        instance = owner(tmp_path)
        target.write_text("untouched")
        name = "relay-owner.lock" if kind == "lock" else "relay-owner.json"
        (instance.state_dir / name).symlink_to(target)
        with pytest.raises(RelayOwnerError):
            instance.acquire("/tmp/owner.sock", "agent")
        assert target.read_text() == "untouched"


def test_insecure_directory_rejected(tmp_path):
    state = tmp_path / "private-state"
    state.mkdir(mode=0o755)
    with pytest.raises(RelayOwnerError):
        owner(tmp_path)


@asynccontextmanager
async def server(handler):
    with tempfile.TemporaryDirectory(prefix="krpc-", dir="/tmp") as directory:
        path = Path(directory) / "hub.sock"
        tasks = set()

        async def connection(reader, writer):
            try:
                await handler(reader, writer)
            finally:
                writer.close()
                await writer.wait_closed()

        def accept(reader, writer):
            task = asyncio.create_task(connection(reader, writer))
            tasks.add(task)

        listener = await asyncio.start_unix_server(accept, str(path), limit=RPC_RESPONSE_LIMIT)
        path.chmod(0o600)
        try:
            yield str(path)
        finally:
            listener.close()
            await listener.wait_closed()
            if tasks:
                await asyncio.wait_for(asyncio.gather(*tasks), timeout=3)


async def echo(reader, writer):
    request = json.loads(await reader.readline())
    assert request["action"] == "rpc_request"
    writer.write(
        json.dumps(
            RpcResponse(request["request_id"], result={"ok": True, "method": request["method"]}).to_wire()
        ).encode()
        + b"\n"
    )
    await writer.drain()


def test_actual_unix_rpc_wire_and_peer_credentials():
    async def scenario():
        async with server(echo) as socket_path:
            result = await local_relay_rpc(socket_path, "relay.status", {})
            assert result == {"ok": True, "method": "relay.status"}

    run(scenario())


@pytest.mark.parametrize("method", ["state.cancel_current_request", "shutdown", "relay.unknown", "relay.status.evil"])
def test_non_relay_method_rejected_before_connect(method):
    with pytest.raises(RelayOwnerError, match="method"):
        run(local_relay_rpc("/tmp/nonexistent.sock", method, {}))


@pytest.mark.parametrize("path", ["wss://remote.example/ws", "localhost:9000", "relative.sock", "\x00abstract"])
def test_offbox_and_nonabsolute_targets_rejected(path):
    with pytest.raises(RelayOwnerError, match="absolute Unix"):
        run(local_relay_rpc(path, "relay.status", {}))


def test_oversize_request_rejected_without_connect():
    with pytest.raises(RelayOwnerError, match="size limit"):
        run(local_relay_rpc("/tmp/nonexistent.sock", "relay.send", {"secret": "X" * RPC_REQUEST_LIMIT}))


def test_rpc_requires_owned_private_socket(tmp_path):
    path = tmp_path / "file"
    path.write_text("not a socket")
    with pytest.raises(RelayOwnerError):
        run(local_relay_rpc(str(path), "relay.status", {}))


def test_wrong_request_id_is_rejected():
    async def wrong(reader, writer):
        await reader.readline()
        writer.write(json.dumps(RpcResponse("wrong-id", result={"ok": True}).to_wire()).encode() + b"\n")
        await writer.drain()

    async def scenario():
        async with server(wrong) as socket_path:
            with pytest.raises(RelayOwnerError, match="match"):
                await local_relay_rpc(socket_path, "relay.send", {})

    run(scenario())


def test_handler_errors_are_redacted():
    secret = "secret-invitation-material"

    async def reject(reader, writer):
        request = json.loads(await reader.readline())
        writer.write(
            json.dumps(RpcResponse(request["request_id"], error=secret, error_kind="handler").to_wire()).encode()
            + b"\n"
        )
        await writer.drain()

    async def scenario():
        async with server(reject) as socket_path:
            with pytest.raises(RelayOwnerError) as error:
                await local_relay_rpc(socket_path, "relay.command", {"command": secret})
            assert secret not in str(error.value)

    run(scenario())


def test_deadline_closes_idle_connection():
    async def stall(reader, writer):
        await reader.readline()
        assert await reader.read() == b""

    async def scenario():
        async with server(stall) as socket_path:
            with pytest.raises(RelayOwnerError, match="timed out"):
                await local_relay_rpc(socket_path, "relay.status", {}, timeout=0.03)

    run(scenario())


def test_response_bound_and_duplicate_fields():
    async def scenario(payload):
        async def invalid(reader, writer):
            await reader.readline()
            writer.write(payload)
            await writer.drain()

        async with server(invalid) as socket_path:
            with pytest.raises(RelayOwnerError):
                await local_relay_rpc(socket_path, "relay.status", {})

    run(scenario(b"X" * (RPC_RESPONSE_LIMIT + 1) + b"\n"))
    run(scenario(b'{"request_id":"first","request_id":"second"}\n'))


def test_wrong_peer_uid_rejected(monkeypatch):
    monkeypatch.setattr(AgentSocketServer, "_get_peer_credentials", staticmethod(lambda _: (1, os.getuid() + 1)))

    async def drain(reader, writer):
        assert await reader.read() == b""

    async def scenario():
        async with server(drain) as socket_path:
            with pytest.raises(RelayOwnerError, match="different user"):
                await local_relay_rpc(socket_path, "relay.status", {})

    run(scenario())


def test_optional_strict_auth_matches_hub_challenge_protocol():
    key = SigningKey.generate()
    nonce = "0123456789abcdef" * 4

    class Identity:
        @staticmethod
        def sign_message(designation, content):
            assert designation == "local-agent"
            return key.sign(content).signature.hex()

    async def authenticated(reader, writer):
        writer.write(json.dumps({"type": "auth_challenge", "nonce": nonce}).encode() + b"\n")
        await writer.drain()
        response = json.loads(await reader.readline())
        assert response["type"] == "auth_response"
        VerifyKey(key.verify_key.encode()).verify(nonce.encode(), bytes.fromhex(response["signature"]))
        writer.write(json.dumps({"type": "auth_ok", "designation": response["designation"]}).encode() + b"\n")
        await writer.drain()
        await echo(reader, writer)

    async def scenario():
        async with server(authenticated) as socket_path:
            result = await local_relay_rpc(
                socket_path,
                "relay.status",
                {},
                auth={
                    "identity_manager": Identity(),
                    "designation": "local-agent",
                    "require_auth": True,
                },
            )
            assert result["ok"]

    run(scenario())


def test_server_auth_cannot_silently_downgrade():
    async def unauthenticated(reader, writer):
        writer.write(b'{"type":"pong"}\n')
        await writer.drain()
        assert await reader.read() == b""

    async def scenario():
        async with server(unauthenticated) as socket_path:
            with pytest.raises(RelayOwnerError, match="authentication"):
                await local_relay_rpc(socket_path, "relay.status", {}, auth={"require_auth": True})

    run(scenario())
