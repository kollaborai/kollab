"""Focused tests for the TLS 1.3 relay session boundary."""

from __future__ import annotations

import ssl

import pytest
from nacl.signing import SigningKey

import plugins.hub.secure_session as secure_session
from plugins.hub.secure_session import (
    ALPN,
    MAX_PACKET_BYTES,
    MAX_SEQUENCE,
    SecureSession,
    SecureSessionError,
    TLSRecordPacket,
    identity_certificate,
    identity_public_key,
)

ALICE_SEED = bytes(range(32))
BOB_SEED = bytes(reversed(range(32)))
MALLORY_SEED = bytes([91]) * 32
ALICE_KEY = SigningKey(ALICE_SEED).verify_key.encode()
BOB_KEY = SigningKey(BOB_SEED).verify_key.encode()
MALLORY_KEY = SigningKey(MALLORY_SEED).verify_key.encode()
ALICE_CERT = identity_certificate(ALICE_SEED)
BOB_CERT = identity_certificate(BOB_SEED)
MALLORY_CERT = identity_certificate(MALLORY_SEED)


def _new_pair():
    client = SecureSession(
        ALICE_SEED,
        BOB_KEY,
        BOB_CERT,
        role="client",
    )
    server = SecureSession(
        BOB_SEED,
        ALICE_KEY,
        ALICE_CERT,
        role="server",
    )
    return client, server


def _complete_handshake(client: SecureSession, server: SecureSession):
    """Pump each bounded TLS packet once, in order, between the two endpoints."""
    client_packet = client.start()
    assert client_packet is not None
    assert client_packet.sequence == 0
    assert server.start() is None
    assert server.session_id is None

    pending = [("server", client_packet)]
    delivered: dict[str, list[bytes]] = {"client": [], "server": []}
    for _ in range(16):
        if not pending:
            break
        receiver_name, packet = pending.pop(0)
        receiver = server if receiver_name == "server" else client
        update = receiver.receive(packet)
        if update.plaintext:
            delivered[receiver_name].append(update.plaintext)
        if update.packet is not None:
            pending.append(
                ("client" if receiver_name == "server" else "server", update.packet)
            )
    assert not pending, "TLS handshake packet loop did not quiesce"
    assert client.established
    assert server.established
    assert client.session_id is not None
    assert client.session_id == server.session_id
    assert client.authenticated_peer_public_key == BOB_KEY
    assert server.authenticated_peer_public_key == ALICE_KEY
    assert client.transcript_id is not None
    assert client.transcript_id == server.transcript_id
    assert client.transcript_id != client.session_id
    return delivered


def test_existing_identity_seed_derives_exact_ed25519_key_and_stable_certificate():
    assert identity_public_key(ALICE_SEED) == ALICE_KEY
    assert identity_public_key(BOB_SEED) == BOB_KEY
    assert identity_certificate(ALICE_SEED) == ALICE_CERT


@pytest.mark.parametrize("seed", [b"short", bytearray(ALICE_SEED), "00" * 32])
def test_identity_seed_must_be_exact_raw_32_byte_value(seed):
    with pytest.raises(SecureSessionError, match="32 bytes"):
        identity_certificate(seed)


def test_mutual_tls_13_uses_pinned_identity_keys_and_sequenced_packets():
    client, server = _new_pair()
    _complete_handshake(client, server)

    assert client._tls is not None and client._tls.version() == "TLSv1.3"
    assert server._tls is not None and server._tls.version() == "TLSv1.3"
    assert client._tls.selected_alpn_protocol() == ALPN
    assert server._tls.selected_alpn_protocol() == ALPN
    assert not client._tls.session_reused
    assert not server._tls.session_reused

    request = client.send(b"opaque relay request")
    assert request.session_id == client.session_id == server.session_id
    assert request.sequence == 2
    delivered = server.receive(request)
    assert delivered.plaintext == b"opaque relay request"
    assert delivered.established

    reply = server.send(b"correlated reply")
    assert reply.sequence == 1
    assert client.receive(reply).plaintext == b"correlated reply"


def test_wrong_server_certificate_is_rejected_before_plaintext():
    client = SecureSession(ALICE_SEED, BOB_KEY, BOB_CERT, role="client")
    impostor_server = SecureSession(
        MALLORY_SEED,
        ALICE_KEY,
        ALICE_CERT,
        role="server",
    )
    client_packet = client.start()
    assert client_packet is not None
    assert impostor_server.start() is None

    server_flight = impostor_server.receive(client_packet).packet
    assert server_flight is not None
    with pytest.raises(SecureSessionError, match="authentication failed"):
        client.receive(server_flight)
    assert client.closed
    assert client.authenticated_peer_public_key is None


def test_wrong_client_certificate_is_rejected_by_server_before_plaintext():
    impostor_client = SecureSession(
        MALLORY_SEED,
        BOB_KEY,
        BOB_CERT,
        role="client",
    )
    server = SecureSession(
        BOB_SEED,
        ALICE_KEY,
        ALICE_CERT,
        role="server",
    )
    client_hello = impostor_client.start()
    assert client_hello is not None
    assert server.start() is None
    server_flight = server.receive(client_hello).packet
    assert server_flight is not None
    client_flight = impostor_client.receive(server_flight).packet
    assert client_flight is not None

    with pytest.raises(SecureSessionError):
        server.receive(client_flight)
    assert server.closed
    assert server.authenticated_peer_public_key is None


def test_expected_certificate_must_match_approved_raw_key():
    with pytest.raises(SecureSessionError, match="approved key"):
        SecureSession(ALICE_SEED, MALLORY_KEY, BOB_CERT, role="client")


def test_duplicate_packet_fails_closed_after_first_delivery():
    client, server = _new_pair()
    _complete_handshake(client, server)
    packet = client.send(b"one delivery")

    assert server.receive(packet).plaintext == b"one delivery"
    with pytest.raises(SecureSessionError, match="duplicate or out of order"):
        server.receive(packet)
    assert server.closed


def test_tampered_tls_record_fails_closed_without_plaintext():
    client, server = _new_pair()
    _complete_handshake(client, server)
    packet = client.send(b"authenticated data")
    changed = bytes([packet.data[0] ^ 1]) + packet.data[1:]
    tampered = TLSRecordPacket(packet.session_id, packet.sequence, changed)

    with pytest.raises(SecureSessionError):
        server.receive(tampered)
    assert server.closed


def test_sequence_gap_fails_closed_without_feeding_tls():
    client, server = _new_pair()
    _complete_handshake(client, server)
    packet = client.send(b"sequence zero for this flight")
    invalid = TLSRecordPacket(packet.session_id, packet.sequence + 1, packet.data)

    with pytest.raises(SecureSessionError, match="duplicate or out of order"):
        server.receive(invalid)
    assert server.closed


def test_sequence_counter_overflow_fails_closed():
    client, server = _new_pair()
    _complete_handshake(client, server)
    packet = client.send(b"data beyond sequence space")
    server._next_incoming_sequence = MAX_SEQUENCE + 1

    exhausted = TLSRecordPacket(packet.session_id, MAX_SEQUENCE, packet.data)
    with pytest.raises(SecureSessionError, match="sequence exhausted"):
        server.receive(exhausted)
    assert server.closed


def test_last_json_safe_sequence_is_allowed_but_next_outgoing_fails_closed():
    client, server = _new_pair()
    _complete_handshake(client, server)
    client._next_outgoing_sequence = MAX_SEQUENCE

    last_packet = client.send(b"last safe sequence")
    assert last_packet.sequence == MAX_SEQUENCE
    with pytest.raises(SecureSessionError, match="sequence exhausted"):
        client.send(b"sequence outside JSON safe integer range")
    assert client.closed


@pytest.mark.parametrize(
    "packet_factory, error",
    [
        (
            lambda packet: TLSRecordPacket(b"x" * 16, packet.sequence, packet.data),
            "another session",
        ),
        (
            lambda packet: TLSRecordPacket(
                packet.session_id, packet.sequence, b"x" * (MAX_PACKET_BYTES + 1)
            ),
            "packet size",
        ),
        (
            lambda packet: TLSRecordPacket(packet.session_id, True, packet.data),
            "sequence is invalid",
        ),
        (
            lambda packet: TLSRecordPacket(
                packet.session_id, packet.sequence, b"not TLS"
            ),
            "TLS handshake",
        ),
    ],
)
def test_malformed_oversized_wrong_session_or_invalid_sequence_packets_fail_closed(
    packet_factory, error
):
    client, server = _new_pair()
    packet = client.start()
    assert packet is not None
    assert server.start() is None
    receiver = server
    basis = packet
    if error == "another session":
        response = server.receive(packet).packet
        assert response is not None
        receiver = client
        basis = response
    invalid = packet_factory(basis)

    with pytest.raises(SecureSessionError, match=error):
        receiver.receive(invalid)
    assert receiver.closed


def test_tls_transport_eof_is_treated_as_truncation():
    client, _ = _new_pair()
    assert client.start() is not None
    with pytest.raises(
        SecureSessionError, match="without an authenticated close alert"
    ):
        client.end_of_stream()
    assert client.closed


def test_transient_private_key_file_is_removed_immediately(monkeypatch):
    written_paths = []
    write_private_file = secure_session._write_private_file

    def record_path(path, contents):
        written_paths.append(path)
        write_private_file(path, contents)

    monkeypatch.setattr(secure_session, "_write_private_file", record_path)
    session = SecureSession(ALICE_SEED, BOB_KEY, BOB_CERT, role="client")

    assert len(written_paths) == 2
    assert all(not path.exists() for path in written_paths)
    session.close()


def test_session_reconnect_starts_new_random_id_and_sequence_space():
    first_client, first_server = _new_pair()
    _complete_handshake(first_client, first_server)
    old_id = first_client.session_id
    old_transcript = first_client.transcript_id
    first_client.close()
    first_server.close()

    second_client, second_server = _new_pair()
    first_packet = second_client.start()
    assert first_packet is not None
    assert first_packet.sequence == 0
    assert first_packet.session_id != old_id
    assert second_server.start() is None
    second_flight = second_server.receive(first_packet).packet
    assert second_flight is not None
    client_flight = second_client.receive(second_flight).packet
    assert client_flight is not None
    final_update = second_server.receive(client_flight)
    assert final_update.established
    assert second_client.established
    assert second_client.transcript_id is not None
    assert second_client.transcript_id == second_server.transcript_id
    assert second_client.transcript_id != old_transcript
    assert second_client.transcript_id != second_client.session_id


def test_no_ticket_resumption_is_configured_for_both_roles():
    client, server = _new_pair()
    for session in (client, server):
        assert session._context is not None
        assert session._context.minimum_version is ssl.TLSVersion.TLSv1_3
        assert session._context.maximum_version is ssl.TLSVersion.TLSv1_3
        if hasattr(ssl, "OP_NO_TICKET"):
            assert session._context.options & ssl.OP_NO_TICKET
    assert server._context is not None
    assert server._context.num_tickets == 0
