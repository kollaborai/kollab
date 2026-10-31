"""Lifecycle notifications for approved RelayClient peer sessions."""

from __future__ import annotations

import asyncio

import pytest
from nacl.signing import SigningKey

from plugins.hub.relay_client import (
    _ApplicationPending,
    PeerSessionEvent,
    RelayClient,
    RelayError,
)


def _peer_key(seed: int) -> str:
    return SigningKey(bytes([seed]) * 32).verify_key.encode().hex()


def _snapshot(*peers: tuple[str, str]) -> dict:
    return {
        "type": "peers",
        "peers": [{"key": key, "session": session} for key, session in peers],
    }


def test_approved_peer_lifecycle_is_ordered_and_unchanged_snapshots_are_noops(
    tmp_path,
):
    client = RelayClient(tmp_path / "workspace", state_dir=tmp_path / "state")
    approved = _peer_key(1)
    other_approved = _peer_key(2)
    unapproved = _peer_key(3)
    for key in (approved, other_approved):
        client.approve(key)

    events = []
    client.add_peer_session_listener(events.append)
    client._set_peers(
        _snapshot(
            (other_approved, "b" * 32),
            (unapproved, "c" * 32),
            (approved, "a" * 32),
        )
    )
    session_by_key = {approved: "a" * 32, other_approved: "b" * 32}
    ordered = sorted(session_by_key)
    assert events == [
        PeerSessionEvent("peer_appeared", key, None, session_by_key[key])
        for key in ordered
    ]

    client._set_peers(
        _snapshot(
            (approved, "a" * 32),
            (other_approved, "b" * 32),
            (unapproved, "c" * 32),
        )
    )
    assert len(events) == 2

    replacement = "d" * 32
    client._set_peers(
        _snapshot(
            (approved, replacement),
            (other_approved, "b" * 32),
            (unapproved, "c" * 32),
        )
    )
    assert events[-1] == PeerSessionEvent(
        "peer_session_changed", approved, "a" * 32, replacement
    )

    client._set_peers(_snapshot((unapproved, "c" * 32)))
    assert events[-1] == PeerSessionEvent(
        "peer_disappeared", approved, replacement, None
    )
    assert events[-2] == PeerSessionEvent(
        "peer_disappeared", other_approved, "b" * 32, None
    )


def test_approval_of_online_peer_appears_and_revocation_is_distinct(tmp_path):
    client = RelayClient(tmp_path / "workspace", state_dir=tmp_path / "state")
    peer = _peer_key(4)
    session = "e" * 32
    events = []
    client.add_peer_session_listener(events.append)

    client._set_peers(_snapshot((peer, session)))
    assert events == []

    client.approve(peer)
    assert events == [PeerSessionEvent("peer_appeared", peer, None, session)]
    client.approve(peer)
    assert len(events) == 1

    client.revoke(peer)
    assert events[-1] == PeerSessionEvent("peer_revoked", peer, session, None)
    assert client.peers() == [{"key": peer, "session": session, "approved": False}]

    client.revoke(peer)
    assert len(events) == 2
    client.approve(peer)
    assert events[-1] == PeerSessionEvent("peer_appeared", peer, None, session)


def test_revoking_offline_approved_peer_still_notifies(tmp_path):
    client = RelayClient(tmp_path / "workspace", state_dir=tmp_path / "state")
    peer = _peer_key(5)
    client.approve(peer)
    events = []
    client.add_peer_session_listener(events.append)

    client.revoke(peer)

    assert events == [PeerSessionEvent("peer_revoked", peer, None, None)]


def test_listener_removal_is_idempotent_and_preserves_other_subscribers(tmp_path):
    client = RelayClient(tmp_path / "workspace", state_dir=tmp_path / "state")
    peer = _peer_key(6)
    client.approve(peer)
    first, second = [], []
    remove_first = client.add_peer_session_listener(first.append)
    client.add_peer_session_listener(second.append)

    remove_first()
    remove_first()
    client._set_peers(_snapshot((peer, "f" * 32)))

    expected = PeerSessionEvent("peer_appeared", peer, None, "f" * 32)
    assert first == []
    assert second == [expected]


def test_listener_failures_do_not_interrupt_disconnect_cleanup_or_ordering(
    tmp_path, caplog
):
    client = RelayClient(tmp_path / "workspace", state_dir=tmp_path / "state")
    peers = sorted((_peer_key(7), _peer_key(8)))
    for key in peers:
        client.approve(key)
    client._session_id = "9" * 32
    client._peers = {peers[1]: "b" * 32, peers[0]: "a" * 32}
    callback_order = []

    def broken_listener(_event):
        raise RuntimeError("callback detail must not escape")

    client.add_peer_session_listener(
        lambda event: callback_order.append(("first", event))
    )
    client.add_peer_session_listener(broken_listener)
    client.add_peer_session_listener(
        lambda event: callback_order.append(("last", event))
    )

    client._clear_connection()

    expected_events = [
        PeerSessionEvent("peer_disappeared", peers[0], "a" * 32, None),
        PeerSessionEvent("peer_disappeared", peers[1], "b" * 32, None),
        PeerSessionEvent("local_disconnected", None, "9" * 32, None),
    ]
    assert callback_order == [
        pair
        for event in expected_events
        for pair in (("first", event), ("last", event))
    ]
    assert client._session_id == ""
    assert client._peers == {}
    assert "relay peer-session listener failed" in caplog.text
    assert "callback detail must not escape" not in caplog.text


def test_close_emits_disconnect_once(tmp_path):
    client = RelayClient(tmp_path / "workspace", state_dir=tmp_path / "state")
    client._session_id = "1" * 32
    events = []
    client.add_peer_session_listener(events.append)

    asyncio.run(client.close())
    asyncio.run(client.close())

    assert events == [PeerSessionEvent("local_disconnected", None, "1" * 32, None)]


@pytest.mark.asyncio
async def test_rejected_peer_snapshot_keeps_connection_alive(tmp_path):
    # A malformed peers frame must fail nothing and never tear the socket
    # down: before the per-request failure fix this RelayError escaped
    # _handle_frame, the receive loop read it as a transport failure, and
    # the client reconnected (fresh session) on every bad snapshot.
    client = RelayClient(tmp_path / "workspace", state_dir=tmp_path / "state")
    peer = _peer_key(1)
    client.approve(peer)
    client._set_peers(_snapshot((peer, "b" * 32)))
    client._state = "online"
    client._session_id = "a" * 32

    await client._handle_frame(
        {"type": "peers", "peers": [{"key": "not-hex", "session": "b" * 32}]}
    )

    assert client._counts["peer_state_frame_errors"] == 1
    assert client._state == "online"
    assert client._session_id == "a" * 32
    assert client._peers == {peer: "b" * 32}


@pytest.mark.asyncio
async def test_rejected_error_frame_keeps_connection_alive(tmp_path):
    # Same regression class from the error-frame path: an invalid relay
    # error frame used to raise out of the receive loop and force a
    # reconnect; it is now counted and dropped on the floor.
    client = RelayClient(tmp_path / "workspace", state_dir=tmp_path / "state")
    client._state = "online"
    client._session_id = "a" * 32

    await client._handle_frame({"type": "error", "code": "nöt_ascii"})

    assert client._counts["peer_state_frame_errors"] == 1
    assert client._state == "online"
    assert client._session_id == "a" * 32


@pytest.mark.asyncio
async def test_peer_vanishing_mid_request_fails_requests_not_connection(tmp_path):
    # The phone-link reconnect loop: a host that vanishes from the peers
    # snapshot mid-request must fail only the requests bound to it. The
    # relay connection, its session id and every other pending request
    # survive; only a genuine transport failure may reconnect.
    client = RelayClient(tmp_path / "workspace", state_dir=tmp_path / "state")
    host = _peer_key(1)
    other = _peer_key(2)
    client.approve(host)
    client.approve(other)
    client._set_peers(_snapshot((host, "b" * 32), (other, "c" * 32)))
    client._state = "online"
    client._session_id = "a" * 32
    loop = asyncio.get_running_loop()
    ping_future = loop.create_future()
    app_future = loop.create_future()
    untouched_future = loop.create_future()
    client._pending["gone"] = (host, ping_future)
    client._application_pending["gone"] = _ApplicationPending(
        host, "a" * 32, "b" * 32, "status", app_future
    )
    client._pending["kept"] = (other, untouched_future)

    await client._handle_frame(_snapshot((other, "c" * 32)))

    assert ping_future.done() and isinstance(ping_future.exception(), RelayError)
    assert app_future.done() and isinstance(app_future.exception(), RelayError)
    assert not untouched_future.done()
    assert "gone" not in client._pending
    assert "gone" not in client._application_pending
    assert client._peers == {other: "c" * 32}
    assert client._state == "online"
    assert client._session_id == "a" * 32
    assert client._counts["peer_state_frame_errors"] == 0
