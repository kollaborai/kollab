"""An accepted stranger is not a mesh member: no peer records go to it."""

import pytest

from tests.unit.test_peer_transport import mesh_network  # noqa: F401


@pytest.mark.asyncio
async def test_an_accepted_stranger_is_never_exchanged_with(mesh_network):  # noqa: F811
    clients, states, _application_calls, _dispatch_calls, wire = mesh_network
    relay, destination = clients["relay"], clients["destination"]
    relay.state.links = [destination.public_key]
    before = len(wire.sent)

    await states["relay"]["mesh"].exchange_peer(destination.public_key)
    await states["relay"]["mesh"].refresh()

    to_stranger = [frame for _, frame in wire.sent[before:] if frame["to"] == destination.public_key]
    assert to_stranger == []
    # the member on the other side is still refreshed
    to_member = [frame for _, frame in wire.sent[before:] if frame["to"] == clients["origin"].public_key]
    assert to_member


@pytest.mark.asyncio
async def test_a_member_is_still_exchanged_with(mesh_network):  # noqa: F811
    clients, states, *_rest, wire = mesh_network
    before = len(wire.sent)

    await states["relay"]["mesh"].exchange_peer(clients["destination"].public_key)

    assert any(frame["to"] == clients["destination"].public_key for _, frame in wire.sent[before:])
