"""Who may open a device's agents from another device (plugins/hub/network_attach.py).

Two bridges on the fake relay from test_relay_agent_bridge.py. The right device
runs a stand-in agent socket; the left device opens it through the network.
"""

import asyncio
import dataclasses
import shutil
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
from nacl.signing import SigningKey

from plugins.hub import network_attach
from plugins.hub.network_attach import NetworkAttach
from plugins.hub.relay_state import RelayError
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
    return await left._rpc_attach({"handle": await handle(left, right)})


@pytest.mark.asyncio
async def test_an_agent_opens_from_the_network_only_once_a_person_there_allows_it(devices):
    left, right, notices = devices
    here = right.device_name()
    with pytest.raises(RelayError) as refused:
        await _open(left, right)
    assert str(refused.value) == (
        f"{here} has not let left-box open its agents. "
        f"On {here}, run: /connect attach allow left-box"
    )

    said = await right.commands.run("attach allow left-box")
    assert said.startswith("left-box may now open this computer's agents")
    assert await right.commands.run("attach") == (
        "may open this computer's agents: left-box"
    )
    opened = await _open(left, right)
    reader, writer = await asyncio.open_unix_connection(opened["socket_path"])
    assert await reader.readline() == b"agent here\n"
    writer.write(b"hello\n")
    await writer.drain()
    assert await reader.readline() == b"HELLO\n"
    assert notices == ["left-box opened sapphire from the network"]

    said = await right.commands.run("attach deny left-box")
    assert said == "left-box may no longer open this computer's agents"
    assert await asyncio.wait_for(reader.read(), 5) == b""
    assert not right.network_attach._channels


@pytest.mark.asyncio
async def test_trust_manual_never_opens_agents(devices):
    left, right, _ = devices
    await right.commands.run("attach allow left-box")
    right.set_trust_level("manual")
    with pytest.raises(RelayError, match="uses trust manual"):
        await _open(left, right)


@pytest.mark.asyncio
async def test_trust_agents_opens_only_the_agents_that_device_may_reach(devices):
    left, right, _ = devices
    await right.commands.run("attach allow left-box")
    right.set_trust_level("agents")
    with pytest.raises(RelayError, match="reach only the agents it allowed"):
        await _open(left, right)
    allow(left, right)
    assert (await _open(left, right))["socket_path"]


@pytest.mark.asyncio
async def test_an_accepted_stranger_cannot_open_agents(devices):
    left, right, _ = devices
    await right.commands.run("attach allow left-box")
    named = await handle(left, right)
    right.set_peer_link(left.commands.client.public_key)
    with pytest.raises(RelayError):
        await left._rpc_attach({"handle": named})
    assert not right.network_attach._channels


@pytest.mark.asyncio
async def test_only_a_member_can_be_allowed(devices):
    left, right, _ = devices
    right.set_peer_link(left.commands.client.public_key)
    said = await right.commands.run("attach allow left-box")
    assert "not a member of this network" in said or said.startswith("connect:")
    assert left.commands.client.public_key not in right._state().state.attach_allowed


@pytest.mark.asyncio
async def test_a_later_approval_keeps_who_may_open_agents(devices):
    """The relay client saves its own copy of the state: it must not drop the grant."""
    left, right, _ = devices
    await right.commands.run("attach allow left-box")
    right.commands.client.approve(SigningKey.generate().verify_key.encode().hex())
    assert right._state().state.attach_allowed == [left.commands.client.public_key]


@pytest.mark.asyncio
async def test_a_connect_command_that_withdraws_an_open_agent_closes_it(devices, monkeypatch):
    monkeypatch.setattr(network_attach, "IDLE_SECONDS", 60.0)  # only the command's own recheck
    left, right, notices = devices
    await right.commands.run("attach allow left-box")
    opened = await _open(left, right)
    reader, _ = await asyncio.open_unix_connection(opened["socket_path"])
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
async def test_revoking_or_rotating_forgets_who_may_open_agents(devices):
    left, right, _ = devices
    key = left.commands.client.public_key
    await right.commands.run("attach allow left-box")
    right.commands.client.revoke(key)
    assert key not in right._state().state.attach_allowed

    right.commands.client.approve(key)  # readmitted with the same key: no grant comes back
    assert key not in right._state().state.attach_allowed
    right.bind_peer_device(key, "left-box")
    await right.commands.run("attach allow left-box")
    right.commands.client.rotate_room()
    assert right._state().state.attach_allowed == []


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
