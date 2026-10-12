"""Relay behavior when two connections present the same device key.

Issue #158 multi-network design, open question: can one device key hold
two concurrent connections (two workspace folders, one network, one
machine)? This probe pins the OBSERVED backend behavior so the
multi-network runtime decision (per-workspace connections vs device-owner
multiplexing) rests on evidence, not assumption.

Findings (StandaloneBackend, the semantics the Redis backend mirrors):
- Same key, same room, second register: refused "duplicate_identity" — a
  second workspace presenting the SAME (key, room) cannot coexist.
- Same key, different rooms: accepted (presence[key] is a single slot that
  tracks the LATEST registration; cross-room delivery relies on that
  index, so two rooms = the first room's delivery becomes unreachable).
Conclusion: one device key may not serve two live relay connections.
The client runtime therefore needs one connection per (device, network)
key (per-workspace keys after slice 1), or device-owner multiplexing.
"""

import pytest

from plugins.hub.relay_backend import InMemoryBackend, PeerRecord, RelayLimits


def _member(key: str, session: str) -> PeerRecord:
    return PeerRecord(key, session, "node-1", f"conn-{session}")


@pytest.mark.asyncio
async def test_second_registration_same_room_is_refused_duplicate_identity():
    backend = InMemoryBackend("node-1", RelayLimits())
    room = "room-hash-a"
    first = _member("a" * 64, "session-1")
    error, records = await backend.register_room(room, first)
    assert error is None and len(records) == 1

    # The same device key registering again (a second folder re-using the
    # key, or a reconnect racing the old connection's release): refused.
    second = _member("a" * 64, "session-2")
    error, records = await backend.register_room(room, second)
    assert error == "duplicate_identity" and records == []


@pytest.mark.asyncio
async def test_same_key_second_room_is_accepted_but_presence_slot_moves():
    backend = InMemoryBackend("node-1", RelayLimits())
    key = "a" * 64
    room_a, room_b = "room-hash-a", "room-hash-b"
    error, _ = await backend.register_room(room_a, _member(key, "session-1"))
    assert error is None
    error, _ = await backend.register_room(room_b, _member(key, "session-2"))
    assert error is None  # accepted by admission...

    # ...but presence[key] is one slot: it now points at room B, so the
    # cross-room locator (linked devices, contact routes) can only find the
    # LATEST registration. The room-A connection is effectively shadowed.
    assert backend.presence.get(key) == room_b


@pytest.mark.asyncio
async def test_release_of_second_room_restores_no_presence_for_first():
    backend = InMemoryBackend("node-1", RelayLimits())
    key = "a" * 64
    room_a, room_b = "room-hash-a", "room-hash-b"
    await backend.register_room(room_a, _member(key, "session-1"))
    _, records = await backend.register_room(room_b, _member(key, "session-2"))
    member_b = records[0]

    removed, _ = await backend.remove_peer(room_b, member_b)
    assert removed
    # The presence slot is cleared when the LATEST registration leaves; it
    # is not restored to the earlier room. The first connection stays
    # registered in its room but unreachable through the presence index.
    assert key not in backend.presence
