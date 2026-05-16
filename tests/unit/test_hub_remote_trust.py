from plugins.hub.remote_envelope import RemoteEnvelope, RemoteEnvelopeVerifier


def test_unsigned_remote_message_is_rejected():
    verifier = RemoteEnvelopeVerifier(approved_keys={"remote-lapis": "pubkey"})

    result = verifier.verify(
        RemoteEnvelope(
            sender="remote-lapis",
            authority="kollabor.ai",
            message_id="msg-1",
            timestamp=123.0,
            body_hash="abc",
            signature="",
        )
    )

    assert result.accepted is False
    assert result.reason == "missing signature"


def test_unknown_remote_sender_is_quarantined():
    verifier = RemoteEnvelopeVerifier(approved_keys={})

    result = verifier.verify(
        RemoteEnvelope(
            sender="remote-lapis",
            authority="remote.example",
            message_id="msg-2",
            timestamp=123.0,
            body_hash="abc",
            signature="sig",
        )
    )

    assert result.accepted is False
    assert result.reason == "unknown remote sender"
    assert result.quarantine is True


def test_approved_remote_sender_is_accepted():
    verifier = RemoteEnvelopeVerifier(approved_keys={"remote-lapis": "pubkey"})

    result = verifier.verify(
        RemoteEnvelope(
            sender="remote-lapis",
            authority="remote.example",
            message_id="msg-3",
            timestamp=123.0,
            body_hash="abc",
            signature="sig",
        )
    )

    assert result.accepted is True
    assert result.reason == "remote sender approved"
    assert result.quarantine is False
