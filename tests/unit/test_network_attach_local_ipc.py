"""The attach device reaches its agent through local_ipc, on every platform (#268)."""

import asyncio
from pathlib import Path

from kollabor_config import local_ipc
from plugins.hub import network_attach
from plugins.hub.network_attach import NetworkAttach


def test_the_attach_module_has_no_direct_unix_socket_calls():
    source = Path(network_attach.__file__).read_text(encoding="utf-8")
    assert "start_unix_server" not in source
    assert "open_unix_connection" not in source


def test_a_refused_agent_connect_goes_through_local_ipc_with_the_read_limit(
    tmp_path, monkeypatch
):
    calls = []

    async def refuse(endpoint, **kw):
        calls.append((endpoint, kw))
        raise ConnectionRefusedError

    monkeypatch.setattr(local_ipc, "connect", refuse)
    socket_path = str(tmp_path / "agent.sock")

    async def target(peer, agent_id, name):
        return socket_path

    async def run():
        attach = NetworkAttach(
            request=None,
            target=target,
            peer_name=lambda key: "mac",
            notice=lambda text: None,
        )
        return await attach._accept(
            "mac", "c" * 32, {"agent_id": "lapis-1", "name": "lapis"}
        )

    result = asyncio.run(run())
    assert result == {"refused": "lapis is not running here any more"}
    assert calls == [(socket_path, {"limit": 4 * network_attach.READ_BYTES})]
