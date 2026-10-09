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
