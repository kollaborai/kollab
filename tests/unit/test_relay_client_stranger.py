"""Envelope binding for devices reached across rooms.

A member of the room binds its messages to the room hash. An accepted stranger
lives in a room of its own, so both ends bind to the pair of keys instead; a
message bound the other way is dropped before it reaches a handler.
"""

import hashlib

import pytest

from plugins.hub.relay_client import link_binding
from tests.unit.test_relay_application_transport import (  # noqa: F401
    encrypted,
    pair,
    until,
)

REQUEST = {"method": "message", "arguments": {}, "timeout_ms": 1000}


def test_the_pair_binding_is_symmetric_and_is_not_a_room_hash():
    first, second = "1" * 64, "2" * 64

    assert link_binding(first, second) == link_binding(second, first)
    assert link_binding(first, second) != link_binding(first, first)
    assert link_binding(first, second) != hashlib.sha256(bytes.fromhex(first)).hexdigest()
    assert len(link_binding(first, second)) == 64


@pytest.mark.asyncio
async def test_a_request_between_linked_strangers_completes(pair):  # noqa: F811
    left, right, _ = pair
    left.state.links = [right.public_key]
    right.state.links = [left.public_key]

    async def handle(peer, method, payload):
        return {"seen": payload}

    right.set_request_handler(handle)

    result = await left.request(right.public_key, "message", {"n": 1}, timeout=2)
    assert result == {"seen": {"n": 1}}


@pytest.mark.asyncio
async def test_a_stranger_message_bound_to_a_room_is_dropped(pair):  # noqa: F811
    left, right, _ = pair
    right.state.links = [left.public_key]
    called = []

    async def handle(*args):
        called.append(args)
        return {}

    right.set_request_handler(handle)

    await right._handle_frame(encrypted(left, right, "request", REQUEST))  # room-bound
    assert not called
    assert right.status()["counters"]["rejected_messages"] == 1

    bound = link_binding(left.public_key, right.public_key)
    await right._handle_frame(encrypted(left, right, "request", REQUEST, room=bound))
    await until(lambda: called)
    assert len(called) == 1


@pytest.mark.asyncio
async def test_a_room_member_message_bound_to_a_pair_is_dropped(pair):  # noqa: F811
    left, right, _ = pair  # same room, no link: a room member
    called = []

    async def handle(*args):
        called.append(args)
        return {}

    right.set_request_handler(handle)
    bound = link_binding(left.public_key, right.public_key)

    await right._handle_frame(encrypted(left, right, "request", REQUEST, room=bound))

    assert not called
    assert right.status()["counters"]["rejected_messages"] == 1
