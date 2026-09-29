"""Unknown-agent contact requests stay key-addressed and outside execution."""

from __future__ import annotations

import base64
import json
import time
from types import SimpleNamespace

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer
from nacl.public import SealedBox
from nacl.signing import SigningKey

from plugins.hub import contact_requests
from plugins.hub import relay_service as service
from plugins.hub.contact_requests import ContactRequestManager
from plugins.hub.relay_backend import InMemoryBackend, PeerRecord, RelayLimits

ORIGIN = "https://relay.example"
NODE_ID = "a" * 32


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _signed(key: SigningKey, path: str, body: dict) -> dict:
    signature = key.sign(
        service.contact_signature_message(ORIGIN, "POST", path, body)
    ).signature.hex()
    return {**body, "signature": signature}


def _request_frame(
    sender: SigningKey,
    recipient: SigningKey,
    introduction: str,
    *,
    request_id: str = "1" * 32,
    nonce: str = "2" * 32,
    device_name: str = "",
    envelope: dict | None = None,
) -> dict:
    recipient_key = recipient.verify_key.encode().hex()
    sender_key = sender.verify_key.encode().hex()
    now = int(time.time())
    ciphertext = SealedBox(recipient.verify_key.to_curve25519_public_key()).encrypt(
        json.dumps(
            envelope
            if envelope is not None
            else {"introduction": introduction, "device_name": device_name},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    )
    body = {
        "v": 1,
        "recipient_identity": "ed25519:" + recipient_key,
        "recipient_key": recipient_key,
        "sender_key": sender_key,
        "request_id": request_id,
        "issued_at": now,
        "expires_at": now + contact_requests.CONTACT_MAX_TTL_SECONDS,
        "nonce": nonce,
        "envelope": _b64url(ciphertext),
    }
    return _signed(sender, service.CONTACT_REQUESTS_PATH, body)


def _recipient_frame(recipient: SigningKey, path: str, **fields) -> dict:
    key = recipient.verify_key.encode().hex()
    body = {
        "v": 1,
        "recipient_identity": "ed25519:" + key,
        "recipient_key": key,
        **fields,
        "issued_at": int(time.time()),
        "nonce": service.secrets.token_hex(16),
    }
    return _signed(recipient, path, body)


@pytest_asyncio.fixture
async def relay_client(monkeypatch):
    monkeypatch.setattr(service, "ENROLLMENT_RATE_LIMIT", 100)
    config = service.RelayConfig(ORIGIN, NODE_ID, dev_in_memory=True)
    client = TestClient(TestServer(service.create_app(config)))
    await client.start_server()
    try:
        yield client
    finally:
        await client.close()


async def _post(client: TestClient, path: str, frame: dict):
    response = await client.post(path, json=frame)
    return response.status, await response.json()


@pytest.mark.asyncio
async def test_contact_mailbox_encrypts_routes_by_full_key_and_human_decision_only(
    relay_client,
):
    sender = SigningKey.generate()
    target = SigningKey.generate()
    other = SigningKey.generate()
    message = "I would like to discuss a one-time collaboration."

    frame = _request_frame(sender, target, message)
    status, queued = await _post(relay_client, service.CONTACT_REQUESTS_PATH, frame)
    assert status == 202
    assert queued == {"status": "queued", "receipt": "1" * 32}

    backend = relay_client.app["relay_state"].backend
    stored = json.dumps(backend.contact_requests)
    assert message not in stored
    assert frame["envelope"] in stored
    assert "room_capability" not in stored
    assert "workspace_id" not in stored

    target_key = target.verify_key.encode().hex()
    inbox = _recipient_frame(target, service.CONTACT_INBOX_PATH)
    status, result = await _post(relay_client, service.CONTACT_INBOX_PATH, inbox)
    assert status == 200
    assert result["status"] == "ok"
    assert len(result["requests"]) == 1
    received = contact_requests._validate_request_frame(
        result["requests"][0],
        recipient_key=target_key,
        origin=ORIGIN,
        now=int(time.time()),
    )
    assert contact_requests._decrypt_envelope(received, target) == (message, "")
    assert (
        contact_requests.PendingContactRequest(
            received["request_id"],
            received["sender_key"],
            received["expires_at"],
            contact_requests.PrivateMessage(message),
        )
        .__repr__()
        .find(message)
        == -1
    )

    other_inbox = _recipient_frame(other, service.CONTACT_INBOX_PATH)
    status, empty = await _post(relay_client, service.CONTACT_INBOX_PATH, other_inbox)
    assert status == 200
    assert empty == {"status": "ok", "requests": []}

    decision = _recipient_frame(
        target,
        service.CONTACT_DECISIONS_PATH,
        request_id=received["request_id"],
        decision="accept",
    )
    status, accepted = await _post(
        relay_client, service.CONTACT_DECISIONS_PATH, decision
    )
    assert status == 200
    assert accepted == {"status": "accepted", "receipt": received["request_id"]}

    inbox_again = _recipient_frame(target, service.CONTACT_INBOX_PATH)
    status, empty = await _post(relay_client, service.CONTACT_INBOX_PATH, inbox_again)
    assert status == 200
    assert empty == {"status": "ok", "requests": []}
    assert backend.rooms == {}


@pytest.mark.asyncio
async def test_contact_request_replay_is_rejected_without_duplicate_queue(relay_client):
    sender = SigningKey.generate()
    target = SigningKey.generate()
    frame = _request_frame(sender, target, "A short introduction.")

    status, first = await _post(relay_client, service.CONTACT_REQUESTS_PATH, frame)
    assert status == 202
    status, replay = await _post(relay_client, service.CONTACT_REQUESTS_PATH, frame)
    assert status == 409
    assert replay == {"error": "replayed"}

    inbox = _recipient_frame(target, service.CONTACT_INBOX_PATH)
    status, listed = await _post(relay_client, service.CONTACT_INBOX_PATH, inbox)
    assert status == 200
    assert len(listed["requests"]) == 1
    assert first["receipt"] == listed["requests"][0]["request_id"]


@pytest.mark.asyncio
async def test_contact_request_backend_is_bounded_expiring_and_decisions_are_scoped():
    backend = InMemoryBackend(NODE_ID, RelayLimits())
    recipient_hash = "a" * 64

    for number in range(32):
        request_id = f"{number:032x}"
        row = {
            "request_id": request_id,
            "frame": {"request_id": request_id, "envelope": "opaque"},
            "content_digest": f"{number:064x}",
        }
        assert (
            await backend.store_contact_request(
                recipient_hash, request_id, row, ttl_ms=60_000
            )
            == "stored"
        )

    extra = {
        "request_id": "f" * 32,
        "frame": {"request_id": "f" * 32, "envelope": "opaque"},
        "content_digest": "f" * 64,
    }
    assert (
        await backend.store_contact_request(
            recipient_hash, extra["request_id"], extra, ttl_ms=60_000
        )
        == "recipient_capacity"
    )
    assert (
        await backend.decide_contact_request(recipient_hash, "0" * 32, "accepted")
        == "stored"
    )
    assert (
        await backend.decide_contact_request(recipient_hash, "0" * 32, "rejected")
        == "conflict"
    )
    assert len(await backend.list_contact_requests(recipient_hash, limit=32)) == 31

    expired_hash = "b" * 64
    assert (
        await backend.store_contact_request(
            expired_hash, "e" * 32, {"request_id": "e" * 32}, ttl_ms=1
        )
        == "stored"
    )
    await __import__("asyncio").sleep(0.01)
    assert await backend.list_contact_requests(expired_hash, limit=32) == []
    assert (
        await backend.decide_contact_request(expired_hash, "e" * 32, "accepted")
        == "unavailable"
    )


@pytest.mark.asyncio
async def test_contact_client_to_relay_to_local_review_and_decision(
    monkeypatch, relay_client
):
    sender_key = SigningKey.generate()
    recipient_key = SigningKey.generate()
    introduction = "Please contact me about a bounded collaboration."

    class _Commands:
        def __init__(self, key):
            self.client = SimpleNamespace(
                _store=SimpleNamespace(key=key),
                public_key=key.verify_key.encode().hex(),
            )

        async def _discover(self, domain):
            assert domain == "relay.example"
            return SimpleNamespace(origin=ORIGIN), "", (), False

        @staticmethod
        def _relay_url(_result):
            return "wss://relay.example/relay/v1/ws"

    async def test_post(_self, origin, path, frame, *, ca, cidrs):
        assert origin == ORIGIN
        response = await relay_client.post(path, json=frame)
        body = await response.json()
        if response.status not in {200, 201, 202}:
            raise contact_requests.ContactProtocolError(body.get("error", "transport"))
        return body

    monkeypatch.setattr(ContactRequestManager, "_post", test_post)
    sender = ContactRequestManager(_Commands(sender_key))
    receiver = ContactRequestManager(_Commands(recipient_key))

    receipt = await sender.submit(
        "relay.example", recipient_key.verify_key.encode().hex(), introduction
    )
    pending = await receiver.pending("relay.example")
    assert len(pending) == 1
    assert pending[0].receipt_id == receipt
    assert pending[0].sender_key == sender_key.verify_key.encode().hex()
    assert pending[0].introduction.reveal() == introduction
    assert introduction not in repr(pending[0])

    decision = await receiver.decide("relay.example", receipt, "accept")
    assert decision.receipt_id == receipt
    assert decision.status == "accepted"
    assert await receiver.pending("relay.example") == []


class _RouteCommands:
    """Minimal `commands` double for resolve_route: no signing key needed."""

    async def _discover(self, domain):
        assert domain == "relay.example"
        return SimpleNamespace(origin=ORIGIN), "", (), False

    @staticmethod
    def _relay_url(_result):
        return "wss://relay.example/relay/v1/ws"


@pytest.mark.asyncio
async def test_device_name_round_trips_in_the_sealed_envelope(monkeypatch, relay_client):
    sender_key = SigningKey.generate()
    recipient_key = SigningKey.generate()

    class _Commands:
        def __init__(self, key):
            self.client = SimpleNamespace(
                _store=SimpleNamespace(key=key),
                public_key=key.verify_key.encode().hex(),
            )

        async def _discover(self, domain):
            return SimpleNamespace(origin=ORIGIN), "", (), False

        @staticmethod
        def _relay_url(_result):
            return "wss://relay.example/relay/v1/ws"

    async def test_post(_self, origin, path, frame, *, ca, cidrs):
        response = await relay_client.post(path, json=frame)
        body = await response.json()
        if response.status not in {200, 201, 202}:
            raise contact_requests.ContactProtocolError(body.get("error", "transport"))
        return body

    monkeypatch.setattr(ContactRequestManager, "_post", test_post)
    sender = ContactRequestManager(_Commands(sender_key))
    receiver = ContactRequestManager(_Commands(recipient_key))

    await sender.submit(
        "relay.example",
        recipient_key.verify_key.encode().hex(),
        "hello",
        "mac-kollab",
    )
    pending = await receiver.pending("relay.example")
    assert pending[0].device_name == "mac-kollab"


@pytest.mark.asyncio
async def test_invalid_device_name_falls_back_to_the_route_fingerprint(
    monkeypatch, relay_client
):
    sender_key = SigningKey.generate()
    recipient_key = SigningKey.generate()

    class _Commands:
        def __init__(self, key):
            self.client = SimpleNamespace(
                _store=SimpleNamespace(key=key),
                public_key=key.verify_key.encode().hex(),
            )

        async def _discover(self, domain):
            return SimpleNamespace(origin=ORIGIN), "", (), False

        @staticmethod
        def _relay_url(_result):
            return "wss://relay.example/relay/v1/ws"

    async def test_post(_self, origin, path, frame, *, ca, cidrs):
        response = await relay_client.post(path, json=frame)
        body = await response.json()
        if response.status not in {200, 201, 202}:
            raise contact_requests.ContactProtocolError(body.get("error", "transport"))
        return body

    monkeypatch.setattr(ContactRequestManager, "_post", test_post)
    sender = ContactRequestManager(_Commands(sender_key))
    receiver = ContactRequestManager(_Commands(recipient_key))

    # submit() itself blanks an invalid name before sealing it, so seal the
    # envelope directly to exercise the receiving side's own fallback too.
    await sender.submit(
        "relay.example", recipient_key.verify_key.encode().hex(), "hello", ""
    )
    pending = await receiver.pending("relay.example")
    sender_key_hex = sender_key.verify_key.encode().hex()
    assert pending[0].device_name == contact_requests.contact_route_hex(sender_key_hex)[:8]


@pytest.mark.asyncio
async def test_resolve_route_returns_the_key_registered_at_that_route(
    monkeypatch, relay_client
):
    key = SigningKey.generate()
    key_hex = key.verify_key.encode().hex()
    backend = relay_client.app["relay_state"].backend
    await backend.register_room(
        "room-x",
        PeerRecord(
            key=key_hex, session="1" * 32, node_id=NODE_ID, connection_id="2" * 32
        ),
    )
    route = contact_requests.contact_route_hex(key_hex)

    async def real_post(_self, origin, path, frame, *, ca, cidrs):
        response = await relay_client.post(path, json=frame)
        body = await response.json()
        if response.status not in {200, 201, 202}:
            raise contact_requests.ContactProtocolError(body.get("error", "transport"))
        return body

    monkeypatch.setattr(ContactRequestManager, "_post", real_post)
    manager = ContactRequestManager(_RouteCommands())
    resolved = await manager.resolve_route("relay.example", route)
    assert resolved == key_hex


@pytest.mark.asyncio
async def test_resolve_route_refuses_a_relay_answer_that_does_not_match_the_route(
    monkeypatch,
):
    async def dishonest_post(_self, _origin, _path, _frame, *, ca, cidrs):
        return {"key": "9" * 64}

    monkeypatch.setattr(ContactRequestManager, "_post", dishonest_post)
    manager = ContactRequestManager(_RouteCommands())
    with pytest.raises(contact_requests.ContactProtocolError):
        await manager.resolve_route("relay.example", "a" * 16)


@pytest.mark.asyncio
async def test_one_malformed_knock_does_not_hide_the_good_ones(monkeypatch, relay_client):
    """The sealed envelope is sender-controlled: a knock with no device_name (or
    any extra key) must be skipped, not blank the whole inbox."""
    recipient_key = SigningKey.generate()
    good_sender, bad_sender = SigningKey.generate(), SigningKey.generate()

    class _Commands:
        def __init__(self, key):
            self.client = SimpleNamespace(
                _store=SimpleNamespace(key=key),
                public_key=key.verify_key.encode().hex(),
            )

        async def _discover(self, domain):
            return SimpleNamespace(origin=ORIGIN), "", (), False

        @staticmethod
        def _relay_url(_result):
            return "wss://relay.example/relay/v1/ws"

    async def test_post(_self, origin, path, frame, *, ca, cidrs):
        response = await relay_client.post(path, json=frame)
        body = await response.json()
        if response.status not in {200, 201, 202}:
            raise contact_requests.ContactProtocolError(body.get("error", "transport"))
        return body

    monkeypatch.setattr(ContactRequestManager, "_post", test_post)
    for sender, request_id, nonce, envelope in (
        (bad_sender, "3" * 32, "4" * 32, {"introduction": "hi"}),
        (bad_sender, "5" * 32, "6" * 32, {"introduction": "hi", "device_name": "x", "extra": 1}),
        (good_sender, "1" * 32, "2" * 32, None),
    ):
        frame = _request_frame(
            sender,
            recipient_key,
            "hello from ana",
            request_id=request_id,
            nonce=nonce,
            device_name="ana-laptop",
            envelope=envelope,
        )
        status, _ = await _post(relay_client, service.CONTACT_REQUESTS_PATH, frame)
        assert status == 202

    pending = await ContactRequestManager(_Commands(recipient_key)).pending("relay.example")

    assert [(row.device_name, row.introduction.reveal()) for row in pending] == [
        ("ana-laptop", "hello from ana")
    ]
