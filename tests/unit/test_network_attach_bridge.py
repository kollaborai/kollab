"""Who may open a device's agents from another device (plugins/hub/network_attach.py).

Two bridges on the fake relay from test_relay_agent_bridge.py. The right device
runs a stand-in agent socket; the left device opens it through the network.
"""

import asyncio
import dataclasses
import json
import shutil
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from plugins.hub import network_attach
from plugins.hub.network_attach import AttachRefused, NetworkAttach
from plugins.hub.relay_state import RelayError, RelayStateStore
from tests.unit.test_relay_agent_bridge import allow, handle


@pytest_asyncio.fixture
async def devices(bridges):
    """left opens agents on right; right runs one stand-in agent socket."""
    members, _ = bridges
    (left, *_), (right, *_) = members
    notices = []
    for bridge in (left, right):
        bridge.network_attach = NetworkAttach(
            request=bridge.secure_transport.request,
            target=bridge.attach_target,
            peer_name=bridge._peer_name,
            notice=notices.append,
        )
    for bridge in (left, right):
        bridge.set_trust_level("open")
    right.bind_peer_device(left.commands.client.public_key, "left-box")

    folder = Path(tempfile.mkdtemp(prefix="kat"))
    path = str(folder / "agent.sock")

    async def serve(reader, writer):
        writer.write(b"agent here\n")
        await writer.drain()
        while line := await reader.readline():
            writer.write(line.upper())
            await writer.drain()

    server = await asyncio.start_unix_server(serve, path=path)
    row = right.directory.rows[0]
    right.directory.rows[0] = dataclasses.replace(row, socket_path=path)
    yield left, right, notices
    server.close()
    shutil.rmtree(folder, ignore_errors=True)


async def _open(left, right):
    """What a chat on ``left`` gets when it opens ``right``'s agent: a socket path or AttachRefused."""
    return await left.attach(await handle(left, right))


@pytest.mark.asyncio
async def test_a_member_device_opens_an_agent_under_trust_open_with_no_command(devices):
    left, right, notices = devices
    opened = await _open(left, right)
    reader, writer = await asyncio.open_unix_connection(opened)
    assert await reader.readline() == b"agent here\n"
    writer.write(b"hello\n")
    await writer.drain()
    assert await reader.readline() == b"HELLO\n"
    assert notices == ["left-box opened sapphire from the network"]

    # Revoked: the rules say no, so the device closes the agent it held open.
    right.commands.client.revoke(left.commands.client.public_key)
    await right.network_attach.recheck()
    assert not right.network_attach._channels


@pytest.mark.asyncio
async def test_trust_manual_never_opens_agents(devices):
    left, right, _ = devices
    right.set_trust_level("manual")
    with pytest.raises(AttachRefused, match="uses trust manual"):
        await _open(left, right)


@pytest.mark.asyncio
async def test_trust_agents_opens_only_the_agents_that_device_may_reach(devices):
    left, right, _ = devices
    right.set_trust_level("agents")
    with pytest.raises(AttachRefused, match="reach only the agents it allowed"):
        await _open(left, right)
    allow(left, right)
    assert await _open(left, right)


@pytest.mark.asyncio
async def test_an_accepted_stranger_cannot_open_agents(devices):
    left, right, _ = devices
    named = await handle(left, right)
    right.set_peer_link(left.commands.client.public_key)
    with pytest.raises(RelayError):
        await left._rpc_attach({"handle": named})
    assert not right.network_attach._channels


@pytest.mark.asyncio
async def test_a_connect_command_that_withdraws_an_open_agent_closes_it(devices, monkeypatch):
    monkeypatch.setattr(network_attach, "IDLE_SECONDS", 60.0)  # only the command's own recheck
    left, right, notices = devices
    opened = await _open(left, right)
    reader, _ = await asyncio.open_unix_connection(opened)
    assert await reader.readline() == b"agent here\n"

    await right._rpc_command({"value": "trust manual", "agent_id": right.identity.agent_id})
    assert await asyncio.wait_for(reader.read(), 5) == b""
    assert notices[-1] == "closed sapphire for left-box: it may no longer open it"


def test_every_relay_method_the_bridge_calls_or_serves_is_allowlisted():
    """One missing here fails only in a window that does not own the network."""
    import re

    from plugins.hub import relay_agent
    from plugins.hub.relay_owner import RELAY_METHODS

    used = set(re.findall(r'"(relay\.[a-z_]+)"', Path(relay_agent.__file__).read_text()))
    assert {"relay.attach", "relay.enroll_device"} <= used <= RELAY_METHODS


@pytest.mark.asyncio
async def test_a_request_cancelled_while_waiting_leaves_the_holders_session_alone(devices):
    """Only the request holding a secure session may discard it."""
    left, right, _ = devices
    release, calls = asyncio.Event(), []

    class SlowDevice:
        async def receive(self, peer, method, payload):
            calls.append(method)
            if len(calls) == 1:
                await release.wait()
            return {"closed": True}

        async def close(self):
            pass

    right.network_attach = SlowDevice()
    key = right.commands.client.public_key
    frame = {"channel": "0" * 32, "seq": 0, "data": ""}
    first = asyncio.create_task(left.secure_transport.request(key, "attach_data", frame, timeout=10))
    for _ in range(300):
        if calls:
            break
        await asyncio.sleep(0.01)
    waiting = asyncio.create_task(left.secure_transport.request(key, "attach_data", frame, timeout=10))
    await asyncio.sleep(0.05)
    waiting.cancel()
    release.set()
    assert await first == {"closed": True}
    assert left.secure_transport._outbound


@pytest.mark.asyncio
async def test_a_refusal_reaches_a_window_that_does_not_own_the_network(tmp_path, caplog):
    """A second window asks through the owner RPC, which hides handler errors: the
    other computer's refusal must still arrive in its own words, with no traceback."""
    import secrets
    from unittest.mock import AsyncMock

    from kollabor_agent.runtime import AgentRuntime
    from kollabor_events import EventBus
    from kollabor_rpc import RpcServer
    from plugins.hub.messenger import AgentSocketServer
    from plugins.hub.plugin import HubPlugin
    from plugins.hub.relay_agent import RelayAgentBridge
    from plugins.hub.relay_conversations import RelayAddress
    from tests.unit.test_relay_agent_bridge import Directory

    refusal = "box uses trust manual: its agents take requests only through /connect authorize"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    directory = Directory(workspace, AgentRuntime(identity="sapphire", agent_id="one"))
    directory.rows = []
    instances = []
    try:
        for name in ("one", "two"):
            hub = HubPlugin(event_bus=EventBus())
            hub._rpc_server = RpcServer()
            hub._identity = AgentRuntime(identity=name, agent_id=name + "-session")
            server = AgentSocketServer(
                hub._identity.agent_id, AsyncMock(), socket_name="attach-proof-" + secrets.token_hex(6)
            )
            server._rpc_server = hub._rpc_server
            hub._identity.socket_path = str(await server.start())
            directory.rows += Directory(workspace, hub._identity).rows
            bridge = RelayAgentBridge(hub, workspace, state_dir=tmp_path / "state", directory=directory)
            hub._relay_agent = bridge
            instances.append((bridge, server))
        owner, window = [item[0] for item in instances]
        await owner.start()
        await window.start()
        assert owner.commands is not None and window.commands is None

        class Refusing:
            async def open(self, peer, agent_id, name):
                raise AttachRefused(refusal)

            async def close(self):
                pass

        owner.network_attach = Refusing()
        owner.resolve_handle = AsyncMock(return_value=str(RelayAddress("b" * 64, "c" * 32, "lapis-session")))
        with pytest.raises(AttachRefused, match=refusal):
            await window.attach("lapis@box")
        with pytest.raises(AttachRefused, match="box uses trust manual"):
            await owner.attach("lapis@box")  # the owner's own windows: same words
        assert not [r for r in caplog.records if r.levelname in ("ERROR", "CRITICAL") or r.exc_info]
    finally:
        for bridge, server in instances:
            await bridge.close()
            await server.stop()


def test_a_relay_state_from_the_attach_allow_list_loads_without_it(tmp_path):
    """Who may open agents follows trust now; a state file from the allow list still loads."""
    store = RelayStateStore(tmp_path, tmp_path / "state")
    data = json.loads(store.state_path.read_text())
    store.state_path.write_text(json.dumps(data | {"attach_allowed": ["a" * 64]}))

    again = RelayStateStore(tmp_path, tmp_path / "state")
    again.save()

    assert "attach_allowed" not in json.loads(again.state_path.read_text())
