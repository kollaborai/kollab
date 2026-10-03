"""Two members that both run a direct endpoint reach each other, direct or over the relay.

Real bridges over an in-process relay wire. A admits B and C; B and C approve each
other only by A's signed member list, and each listens on its own TLS endpoint.
"""

from __future__ import annotations

import logging
import re

import pytest
import pytest_asyncio

from plugins.hub.peer_router import TransientPeerDeliveryError
from plugins.hub.relay_state import RelayError, failure_text

from .test_mesh_network import deliver_locator, make_node
from .test_network_members import joined, sync
from .test_peer_transport import RelayWire, _mint_tls_cert

pytestmark = pytest.mark.usefixtures("unthrottled_relay")


async def build(tmp_path):
    cert, key = _mint_tls_cert(tmp_path)
    wire = RelayWire()
    a = await make_node(tmp_path, "mac", designation="lapis")
    b = await make_node(
        tmp_path, "srv-b", designation="koordinator", endpoint=(cert, key, cert)
    )
    c = await make_node(
        tmp_path, "srv-c", designation="peridot", endpoint=(cert, key, cert)
    )
    nodes = (a, b, c)
    for node in nodes:
        wire.add(node.client)
        await node.bridge.membership_sync.close()  # the test drives every tick
        node.bridge.membership_sync._poll = 0.0
    for node in nodes:
        for other in nodes:
            if other is not node:
                node.client._peers[other.key] = other.client._session_id
    joined(a, b)
    joined(a, c)
    await sync(nodes)
    assert b.key in c.client.state.approvals and c.key in b.client.state.approvals
    return a, b, c, nodes, wire


@pytest_asyncio.fixture
async def net(tmp_path):
    a, b, c, nodes, wire = await build(tmp_path)
    yield a, b, c
    for node in nodes:
        await node.bridge.close()
        if node.server is not None:
            await node.server.stop()
    await wire.close()


async def directory(asker, peer):
    reply = await asker.bridge.secure_transport.request(peer.key, "directory", {}, timeout=5)
    assert set(reply) == {"agents", "truncated"}


async def refresh(*nodes):
    for _ in range(2):
        for node in nodes:
            await node.mesh.refresh()


@pytest.mark.asyncio
@pytest.mark.parametrize("who", ["none", "b_has_c", "c_has_b", "both"])
async def test_members_with_endpoints_reach_each_other_whichever_locators_have_crossed(net, who):
    _a, b, c = net
    if who in ("b_has_c", "both"):
        deliver_locator(b, c)
    if who in ("c_has_b", "both"):
        deliver_locator(c, b)
    for _round in range(2):  # the first opens the sessions and the link, the second rides them
        await directory(b, c)
        await directory(c, b)
        await refresh(b, c)


@pytest.mark.asyncio
async def test_a_link_whose_sessions_are_gone_does_not_block_the_next_handshake(net):
    _a, b, c = net
    deliver_locator(b, c)
    deliver_locator(c, b)
    await directory(b, c)
    await refresh(b, c)
    assert b.mesh.router.link_between(b.mesh.local_peer_id, c.mesh.local_peer_id) is not None
    for node in (b, c):  # expiry or a failed request drops the sessions; the link stays
        node.bridge.secure_transport._clear_sessions()
    await directory(b, c)
    await directory(c, b)


def test_a_log_line_names_the_class_and_shows_only_a_fixed_message():
    assert failure_text(RelayError("peer is not approved")) == "RelayError: peer is not approved"
    assert failure_text(KeyError("token-123")) == "KeyError"


@pytest.mark.asyncio
async def test_a_relay_handler_failure_is_logged_by_class_and_never_by_text(net, caplog):
    _a, b, c = net

    async def boom(_key, _method, _arguments):
        raise KeyError("token-123")

    c.client.set_request_handler(boom)
    with caplog.at_level(logging.WARNING), pytest.raises(RelayError):
        await b.client.request(c.key, "peer.forward", {}, timeout=3)
    assert "peer application request failed: KeyError" in caplog.text
    assert "token-123" not in caplog.text


@pytest.mark.asyncio
async def test_a_rejected_secure_packet_is_logged_by_class(net, caplog):
    _a, b, c = net
    with caplog.at_level(logging.WARNING), pytest.raises(RelayError):
        await c.bridge.secure_transport.handle_packet(b.key, {}, None)
    assert re.search(r"secure conversation packet was rejected: \w+Error", caplog.text)


@pytest.mark.asyncio
async def test_a_failed_direct_forward_is_logged_by_class_and_never_by_text(net, caplog):
    _a, b, c = net
    deliver_locator(b, c)
    deliver_locator(c, b)

    async def boom(*_args):
        raise KeyError("token-123")

    b.server.set_peer_forward_handler(boom)
    with caplog.at_level(logging.WARNING), pytest.raises(TransientPeerDeliveryError):
        await c.mesh._send_direct_peer_forward(b.key, {"v": 1}, timeout=3)
    assert "authenticated peer forwarding failed: KeyError" in caplog.text
    assert "token-123" not in caplog.text
