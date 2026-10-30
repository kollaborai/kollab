"""On Windows the relay owner acquires and local RPC answers over local_ipc (#439)."""

import asyncio
import json
import os

import pytest

from plugins.hub.relay_owner import WorkspaceRelayOwner, local_relay_rpc

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows only")


def test_owner_acquires_on_windows(tmp_path):
    endpoint = tmp_path / "hub.sock"
    owner = WorkspaceRelayOwner(tmp_path, tmp_path / "state")
    try:
        assert owner.acquire(str(endpoint), "agent-a") is True
        assert owner.owner() == {
            "socket_path": str(endpoint),
            "agent_id": "agent-a",
            "pid": os.getpid(),
        }
        assert (tmp_path / "state" / "relay-owner.json").is_file()
    finally:
        owner.release()
    assert not (tmp_path / "state" / "relay-owner.json").exists()


def test_local_rpc_answers_on_windows(tmp_path):
    from kollabor_config import local_ipc

    async def scenario():
        async def handler(reader, writer):
            line = await reader.readline()
            request = json.loads(line)
            reply = {
                "action": "rpc_reply",
                "request_id": request["request_id"],
                "error": None,
                "error_kind": None,
                "result": {"ok": True},
            }
            writer.write(json.dumps(reply).encode() + b"\n")
            await writer.drain()
            writer.close()

        server = await local_ipc.serve(handler, tmp_path / "hub.sock")
        try:
            return await local_relay_rpc(
                str(tmp_path / "hub.sock"), "relay.status", {}
            )
        finally:
            server.close()

    assert asyncio.run(scenario()) == {"ok": True}


def test_hard_linked_owner_record_is_not_private(tmp_path):
    """_private_regular keeps the hard-link rejection on Windows (#439).

    os.stat fills st_nlink on Windows too, so a second NTFS hard link to the
    relay-owner record must stay refused there.
    """
    from plugins.hub.relay_owner import _private_regular

    record = tmp_path / "relay-owner.json"
    record.write_text("{}", encoding="utf-8")
    assert _private_regular(record.stat(), record) is True
    os.link(record, tmp_path / "second-link.json")
    info = record.stat()
    assert info.st_nlink == 2
    assert _private_regular(info, record) is False
