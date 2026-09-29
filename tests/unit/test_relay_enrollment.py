"""Relay pending-enrollment mailbox contracts; payloads stay opaque to the relay."""

from __future__ import annotations

import asyncio
import base64
import re
import shutil
import socket
import tempfile
import time

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer
from nacl.signing import SigningKey

from plugins.hub import relay_backend as backend_module
from plugins.hub import relay_service as service
from plugins.hub.relay_backend import (
    ENROLLMENT_CAPACITY_INDEX_TTL_MS,
    ENROLLMENT_MAX_FAILED_CODES,
    InMemoryBackend,
    PeerRecord,
    RedisRelayBackend,
    RelayBackendError,
    RelayLimits,
)

ORIGIN = "https://relay.example"
NODE_ID = "a" * 32
ROOM_CAPABILITY = "b" * 64
SESSION = "c" * 32


def b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _issuer_fields(
    signer: SigningKey,
    offer_id: str,
    path: str,
    **extra,
):
    body = {
        "v": 1,
        "offer_id": offer_id,
        "issuer_key": bytes(signer.verify_key).hex(),
        "room_capability": ROOM_CAPABILITY,
        "session": SESSION,
        "issued_at": int(time.time()),
        "nonce": service.secrets.token_hex(16),
        **extra,
    }
    return service.sign_enrollment_request(signer, ORIGIN, "POST", path, body)


def _destination_fields(signer: SigningKey, offer_id: str, path: str, **extra):
    body = {
        "v": 1,
        "offer_id": offer_id,
        "destination_key": bytes(signer.verify_key).hex(),
        "issued_at": int(time.time()),
        "nonce": service.secrets.token_hex(16),
        **extra,
    }
    return service.sign_enrollment_request(signer, ORIGIN, "POST", path, body)


def _lookup_fields(signer: SigningKey, path: str, **extra):
    body = {
        "v": 1,
        "destination_key": bytes(signer.verify_key).hex(),
        "issued_at": int(time.time()),
        "nonce": service.secrets.token_hex(16),
        **extra,
    }
    return service.sign_enrollment_request(signer, ORIGIN, "POST", path, body)


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


async def register_issuer(
    client: TestClient, signer: SigningKey, room: str, session: str
):
    state = client.app["relay_state"]
    await state.backend.register_room(
        service.room_digest(room),
        PeerRecord(bytes(signer.verify_key).hex(), session, NODE_ID, "1" * 32),
    )


async def post_json(client: TestClient, path: str, body: dict):
    response = await client.post(path, json=body)
    return response.status, await response.json()


@pytest.mark.asyncio
async def test_enrollment_rate_limit_precedes_parsing_and_uses_endpoint_buckets(
    relay_client, monkeypatch
):
    monkeypatch.setattr(service, "ENROLLMENT_RATE_LIMIT", 2)
    offer_id = "1" * 32
    request_path = f"/relay/v1/enrollment/offers/{offer_id}/request"

    for _ in range(2):
        response = await relay_client.post(
            request_path,
            data=b"{",
            headers={"Content-Type": "application/json"},
        )
        assert response.status == 400
        assert await response.json() == {"error": "invalid_request"}

    limited = await relay_client.post(
        request_path,
        data=b"{",
        headers={"Content-Type": "application/json"},
    )
    assert limited.status == 429
    assert limited.headers["Retry-After"] == "60"
    assert await limited.json() == {"error": "rate_limited"}

    # The same source may still use another bounded endpoint; a long offer poll
    # does not consume the bucket used to submit a device request.
    poll_path = f"/relay/v1/enrollment/offers/{offer_id}/poll"
    other_endpoint = await relay_client.post(
        poll_path,
        data=b"{",
        headers={"Content-Type": "application/json"},
    )
    assert other_endpoint.status == 400
    assert await other_endpoint.json() == {"error": "invalid_request"}


@pytest.mark.asyncio
async def test_private_metrics_report_enrollment_admission_usage(relay_client):
    response = await relay_client.get("/relay/v1/metrics")

    assert response.status == 200
    body = await response.text()
    assert "relay_enrollment_admission_metrics_available 1" in body
    assert "relay_enrollment_rate_buckets_tracked 0" in body
    assert (
        f"relay_enrollment_rate_buckets_limit {backend_module.ENROLLMENT_MAX_RATE_SOURCES}"
        in body
    )
    assert "relay_enrollment_nonce_records_tracked 0" in body
    assert (
        f"relay_enrollment_nonce_records_limit {backend_module.ENROLLMENT_MAX_NONCES}"
        in body
    )


@pytest.mark.asyncio
async def test_in_memory_enrollment_admission_has_global_source_and_nonce_caps(
    monkeypatch,
):
    monkeypatch.setattr(backend_module, "ENROLLMENT_MAX_RATE_SOURCES", 2)
    monkeypatch.setattr(backend_module, "ENROLLMENT_MAX_NONCES", 2)
    backend = InMemoryBackend(NODE_ID, RelayLimits())

    assert await backend.consume_enrollment_rate("1" * 64, limit=10, window_ms=60_000)
    assert await backend.consume_enrollment_rate("2" * 64, limit=10, window_ms=60_000)
    assert not await backend.consume_enrollment_rate(
        "3" * 64, limit=10, window_ms=60_000
    )

    assert await backend.consume_enrollment_nonce(
        "a" * 64, "1" * 64, ttl_ms=1_000, capacity=4096
    )
    assert await backend.consume_enrollment_nonce(
        "b" * 64, "2" * 64, ttl_ms=1_000, capacity=4096
    )
    with pytest.raises(RelayBackendError, match="nonce capacity is full"):
        await backend.consume_enrollment_nonce(
            "c" * 64, "3" * 64, ttl_ms=1_000, capacity=4096
        )
    assert backend.enrollment_nonce_count == 2

    backend._prune_enrollment_nonces(time.monotonic() + 2)
    assert backend.enrollment_nonce_count == 0
    assert await backend.consume_enrollment_nonce(
        "c" * 64, "3" * 64, ttl_ms=1_000, capacity=4096
    )


@pytest.mark.asyncio
async def test_enrollment_code_and_signature_contract():
    offer_id = "1" * 32
    code = service.generate_enrollment_code(offer_id)
    assert re.fullmatch(r"[0-9A-HJKMNP-TV-Z]{4}-[0-9A-HJKMNP-TV-Z]{4}", code)
    derived_id, verifier = service.derive_enrollment_code_verifier(code.lower(), offer_id)
    assert derived_id == offer_id
    assert len(base64.urlsafe_b64decode(verifier + "=")) == 32
    assert service.enrollment_verifier_hash(
        offer_id, verifier
    ) == service.enrollment_verifier_hash(offer_id, verifier)
    assert service.enrollment_verifier_hash(
        "2" * 32, verifier
    ) != service.enrollment_verifier_hash(offer_id, verifier)

    signer = SigningKey.generate()
    body = {"v": 1, "offer_id": offer_id, "issued_at": 1, "nonce": "2" * 32}
    path = f"/relay/v1/enrollment/offers/{offer_id}/reply/poll"
    signed = service.sign_enrollment_request(signer, ORIGIN, "POST", path, body)
    assert service.verify_enrollment_request_signature(
        bytes(signer.verify_key).hex(), ORIGIN, "POST", path, signed
    )
    assert not service.verify_enrollment_request_signature(
        bytes(signer.verify_key).hex(), ORIGIN, "POST", path + "x", signed
    )
    assert service.ENROLLMENT_SIGNATURE_DOMAIN == b"kollab-relay-enrollment-http/1\x00"
    assert set(service.ENROLLMENT_JSON_SCHEMAS["submit_request"]["required"]) == {
        "v",
        "offer_id",
        "destination_key",
        "issued_at",
        "nonce",
        "signature",
        "round_id",
        "code_verifier",
        "envelope",
    }


@pytest.mark.asyncio
async def test_full_pairing_mailbox_is_key_pinned_idempotent_and_non_authorizing(
    relay_client,
):
    client = relay_client
    issuer = SigningKey.generate()
    device = SigningKey.generate()
    wrong_device = SigningKey.generate()
    await register_issuer(client, issuer, ROOM_CAPABILITY, SESSION)

    offer_id = "1" * 32
    code = service.generate_enrollment_code(offer_id)
    parsed_offer_id, verifier = service.derive_enrollment_code_verifier(code, offer_id)
    assert parsed_offer_id == offer_id
    verifier_hash = service.enrollment_verifier_hash(offer_id, verifier)
    create = _issuer_fields(
        issuer,
        offer_id,
        service.ENROLLMENT_OFFERS_PATH,
        nonce="e" * 32,
        expires_at=int(time.time()) + 300,
        code_verifier_hash=verifier_hash,
    )
    status, result = await post_json(client, service.ENROLLMENT_OFFERS_PATH, create)
    assert status == 201
    assert result == {
        "status": "offered",
        "offer_id": offer_id,
        "expires_at": create["expires_at"],
    }
    assert code not in str(result) and verifier not in str(result)

    status, replayed = await post_json(client, service.ENROLLMENT_OFFERS_PATH, create)
    assert status == 409
    assert replayed == {"error": "replayed"}

    fresh_retry = _issuer_fields(
        issuer,
        offer_id,
        service.ENROLLMENT_OFFERS_PATH,
        nonce="f" * 32,
        expires_at=create["expires_at"],
        code_verifier_hash=verifier_hash,
    )
    status, duplicate_offer = await post_json(
        client, service.ENROLLMENT_OFFERS_PATH, fresh_retry
    )
    assert status == 200
    assert duplicate_offer == result

    # A different live room peer is authenticated at the relay, but does not
    # own this offer and cannot claim its pending envelope.
    other_issuer = SigningKey.generate()
    await register_issuer(client, other_issuer, ROOM_CAPABILITY, "d" * 32)

    request_path = service.ENROLLMENT_REQUEST_PATH.format(offer_id=offer_id)
    request_envelope = b64url(b"x" * service.ENROLLMENT_MAX_ENVELOPE_BYTES)
    assert len(request_envelope) == service.ENROLLMENT_MAX_ENVELOPE_CHARS
    request_body = _destination_fields(
        device,
        offer_id,
        request_path,
        round_id="2" * 32,
        code_verifier=verifier,
        envelope=request_envelope,
    )
    status, result = await post_json(client, request_path, request_body)
    assert status == 202
    assert result == {"status": "pending", "receipt": "2" * 32}
    assert (
        code not in str(result)
        and verifier not in str(result)
        and request_envelope not in str(result)
    )

    status, duplicate = await post_json(
        client,
        request_path,
        _destination_fields(
            device,
            offer_id,
            request_path,
            round_id="2" * 32,
            code_verifier=verifier,
            envelope=request_envelope,
        ),
    )
    assert status == 200
    assert duplicate == result

    status, bound = await post_json(
        client,
        request_path,
        _destination_fields(
            wrong_device,
            offer_id,
            request_path,
            round_id="3" * 32,
            code_verifier=verifier,
            envelope=b64url(b"second device"),
        ),
    )
    assert status == 409 and bound == {"error": "bound"}

    poll_path = service.ENROLLMENT_ISSUER_POLL_PATH.format(offer_id=offer_id)
    status, wrong_issuer_result = await post_json(
        client,
        poll_path,
        _issuer_fields(
            other_issuer,
            offer_id,
            poll_path,
            session="d" * 32,
            claim_id="e" * 32,
        ),
    )
    assert status == 404 and wrong_issuer_result == {"error": "unavailable"}
    poll_body = _issuer_fields(issuer, offer_id, poll_path, claim_id="4" * 32)
    status, claimed = await post_json(client, poll_path, poll_body)
    assert status == 200
    assert claimed == {
        "status": "claimed",
        "phase": "request",
        "round_id": "2" * 32,
        "destination_key": bytes(device.verify_key).hex(),
        "envelope": request_envelope,
        "device_name": "",
    }
    status, retry_claim = await post_json(
        client,
        poll_path,
        _issuer_fields(issuer, offer_id, poll_path, claim_id="4" * 32),
    )
    assert status == 200 and retry_claim == claimed
    status, second_claim = await post_json(
        client,
        poll_path,
        _issuer_fields(issuer, offer_id, poll_path, claim_id="5" * 32),
    )
    assert status == 409 and second_claim == {"error": "claimed"}

    challenge_path = service.ENROLLMENT_CHALLENGE_PATH.format(offer_id=offer_id)
    challenge_envelope = b64url(b"opaque owner-signed challenge encrypted to device")
    challenge_body = _issuer_fields(
        issuer,
        offer_id,
        challenge_path,
        round_id="6" * 32,
        destination_key=bytes(device.verify_key).hex(),
        envelope=challenge_envelope,
    )
    status, challenge_receipt = await post_json(client, challenge_path, challenge_body)
    assert status == 202
    assert challenge_receipt == {"status": "stored", "receipt": "6" * 32}

    reply_poll_path = service.ENROLLMENT_REPLY_POLL_PATH.format(offer_id=offer_id)
    status, wrong_poll = await post_json(
        client,
        reply_poll_path,
        _destination_fields(wrong_device, offer_id, reply_poll_path),
    )
    assert status == 404 and wrong_poll == {"error": "unavailable"}
    status, challenge = await post_json(
        client,
        reply_poll_path,
        _destination_fields(device, offer_id, reply_poll_path),
    )
    assert status == 200
    assert challenge == {
        "status": "ready",
        "phase": "challenge",
        "round_id": "6" * 32,
        "envelope": challenge_envelope,
    }

    proof_path = service.ENROLLMENT_PROOF_PATH.format(offer_id=offer_id)
    proof_envelope = b64url(b"opaque existing PrivateDirectory proof")
    proof_body = _destination_fields(
        device,
        offer_id,
        proof_path,
        round_id="7" * 32,
        envelope=proof_envelope,
    )
    status, proof_receipt = await post_json(client, proof_path, proof_body)
    assert status == 202
    assert proof_receipt == {"status": "stored", "receipt": "7" * 32}

    proof_claim_body = _issuer_fields(issuer, offer_id, poll_path, claim_id="8" * 32)
    status, proof_claim = await post_json(client, poll_path, proof_claim_body)
    assert status == 200
    assert proof_claim == {
        "status": "claimed",
        "phase": "proof",
        "round_id": "7" * 32,
        "destination_key": bytes(device.verify_key).hex(),
        "envelope": proof_envelope,
        "device_name": "",
    }

    decision_path = service.ENROLLMENT_DECISION_PATH.format(offer_id=offer_id)
    decision_envelope = b64url(b"opaque encrypted credential/config decision")
    decision_body = _issuer_fields(
        issuer,
        offer_id,
        decision_path,
        round_id="9" * 32,
        destination_key=bytes(device.verify_key).hex(),
        envelope=decision_envelope,
    )
    status, decision_receipt = await post_json(client, decision_path, decision_body)
    assert status == 202
    assert decision_receipt == {"status": "stored", "receipt": "9" * 32}
    status, ready = await post_json(
        client,
        reply_poll_path,
        _destination_fields(device, offer_id, reply_poll_path),
    )
    assert status == 200
    assert ready == {
        "status": "ready",
        "phase": "decision",
        "round_id": "9" * 32,
        "envelope": decision_envelope,
    }

    repeated_decision = _issuer_fields(
        issuer,
        offer_id,
        decision_path,
        round_id="9" * 32,
        destination_key=bytes(device.verify_key).hex(),
        envelope=decision_envelope,
    )
    status, repeated = await post_json(client, decision_path, repeated_decision)
    assert status == 200 and repeated == decision_receipt
    conflicting_decision = _issuer_fields(
        issuer,
        offer_id,
        decision_path,
        round_id="a" * 32,
        destination_key=bytes(device.verify_key).hex(),
        envelope=b64url(b"different decision"),
    )
    status, conflict = await post_json(client, decision_path, conflicting_decision)
    assert status == 409 and conflict == {"error": "conflict"}

    ack_path = service.ENROLLMENT_ACK_PATH.format(offer_id=offer_id)
    ack_envelope = b64url(b"encrypted device install receipt")
    ack_body = _destination_fields(
        device,
        offer_id,
        ack_path,
        round_id="9" * 32,
        envelope=ack_envelope,
    )
    status, ack_receipt = await post_json(client, ack_path, ack_body)
    assert status == 202
    assert ack_receipt == {"status": "stored", "receipt": "9" * 32}
    status, duplicate_ack = await post_json(
        client,
        ack_path,
        _destination_fields(
            device,
            offer_id,
            ack_path,
            round_id="9" * 32,
            envelope=ack_envelope,
        ),
    )
    assert status == 200 and duplicate_ack == ack_receipt
    status, conflicting_ack = await post_json(
        client,
        ack_path,
        _destination_fields(
            device,
            offer_id,
            ack_path,
            round_id="9" * 32,
            envelope=b64url(b"different install receipt"),
        ),
    )
    assert status == 409 and conflicting_ack == {"error": "conflict"}
    status, wrong_round_ack = await post_json(
        client,
        ack_path,
        _destination_fields(
            device,
            offer_id,
            ack_path,
            round_id="a" * 32,
            envelope=ack_envelope,
        ),
    )
    assert status == 404 and wrong_round_ack == {"error": "unavailable"}
    status, wrong_device_ack = await post_json(
        client,
        ack_path,
        _destination_fields(
            wrong_device,
            offer_id,
            ack_path,
            round_id="9" * 32,
            envelope=ack_envelope,
        ),
    )
    assert status == 404 and wrong_device_ack == {"error": "unavailable"}

    ack_poll_path = service.ENROLLMENT_ACK_POLL_PATH.format(offer_id=offer_id)
    status, ack_result = await post_json(
        client,
        ack_poll_path,
        _issuer_fields(issuer, offer_id, ack_poll_path),
    )
    assert status == 200
    assert ack_result == {"status": "ready", "frame": ack_body}
    status, wrong_issuer_ack = await post_json(
        client,
        ack_poll_path,
        _issuer_fields(
            other_issuer,
            offer_id,
            ack_poll_path,
            session="d" * 32,
        ),
    )
    assert status == 404 and wrong_issuer_ack == {"error": "unavailable"}

    backend = client.app["relay_state"].backend
    stored = backend.enrollment_offers[offer_id]
    assert code not in str(stored)
    assert verifier not in str(stored)
    assert ROOM_CAPABILITY not in str(stored)
    assert (
        bytes(device.verify_key).hex()
        not in backend.rooms[service.room_digest(ROOM_CAPABILITY)]
    )
    assert (
        len([key for key in stored if key.endswith("_envelope")])
        == service.ENROLLMENT_MAX_ROUNDS
    )
    assert "workspace_id" not in str(stored["install_ack_frame"])


@pytest.mark.asyncio
async def test_issuer_key_alone_and_plaintext_code_are_not_accepted(relay_client):
    client = relay_client
    issuer = SigningKey.generate()
    offer_id = "d" * 32
    code = service.generate_enrollment_code(offer_id)
    _, verifier = service.derive_enrollment_code_verifier(code, offer_id)
    path = service.ENROLLMENT_OFFERS_PATH
    body = _issuer_fields(
        issuer,
        offer_id,
        path,
        expires_at=int(time.time()) + 300,
        code_verifier_hash=service.enrollment_verifier_hash(offer_id, verifier),
    )
    status, response = await post_json(client, path, body)
    assert status == 403 and response == {"error": "unauthorized"}
    assert not client.app["relay_state"].backend.enrollment_offers

    device = SigningKey.generate()
    request_path = service.ENROLLMENT_REQUEST_PATH.format(offer_id=offer_id)
    request = _destination_fields(
        device,
        offer_id,
        request_path,
        round_id="e" * 32,
        code_verifier=verifier,
        envelope=b64url(b"opaque"),
    )
    request["code"] = code
    status, response = await post_json(client, request_path, request)
    assert status == 400 and response == {"error": "invalid_request"}
    assert code not in str(response)


@pytest.mark.asyncio
async def test_memory_backend_bounds_capacity_failed_codes_and_expiry():
    backend = InMemoryBackend(NODE_ID, RelayLimits())
    offer_id = "f" * 32
    code = service.generate_enrollment_code(offer_id)
    _, verifier = service.derive_enrollment_code_verifier(code, offer_id)
    correct_hash = service.enrollment_verifier_hash(offer_id, verifier)
    fields = {
        "issuer_key": "1" * 64,
        "code_verifier_hash": correct_hash,
        "create_digest": "2" * 64,
    }
    assert (
        await backend.create_enrollment_offer(offer_id, fields, ttl_ms=1000, capacity=1)
        == "created"
    )
    assert (
        await backend.create_enrollment_offer(
            "0" * 32, fields, ttl_ms=10_000, capacity=1
        )
        == "capacity"
    )
    wrong = service.enrollment_verifier_hash(offer_id, b64url(b"z" * 32))
    for attempt in range(ENROLLMENT_MAX_FAILED_CODES):
        result = await backend.submit_enrollment_request(
            offer_id,
            round_id=f"{attempt + 1:032x}",
            destination_key="3" * 64,
            candidate_hash=wrong,
            envelope=b64url(b"opaque"),
            content_digest=f"{attempt + 1:064x}",
        )
        # At the cap the offer is burned outright, not merely rate limited, so
        # a guesser sees the same generic error a missing offer would give.
        assert result == (
            "unavailable"
            if attempt + 1 == ENROLLMENT_MAX_FAILED_CODES
            else "invalid_code"
        )
    assert (
        await backend.submit_enrollment_request(
            offer_id,
            round_id="4" * 32,
            destination_key="3" * 64,
            candidate_hash=correct_hash,
            envelope=b64url(b"opaque"),
            content_digest="4" * 64,
        )
        == "unavailable"
    )
    # Burning deletes the offer, freeing capacity immediately -- no TTL wait.
    assert (
        await backend.create_enrollment_offer(
            "0" * 32, fields, ttl_ms=10_000, capacity=1
        )
        == "created"
    )


@pytest.mark.asyncio
async def test_redis_backend_lua_mailbox_transitions_are_atomic(monkeypatch):
    redis_server = shutil.which("redis-server")
    redis_package = pytest.importorskip("redis.asyncio")
    if redis_server is None:
        pytest.skip("redis-server is unavailable")

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    with tempfile.TemporaryDirectory(prefix="relay-enrollment-redis-") as directory:
        process = await asyncio.create_subprocess_exec(
            redis_server,
            "--bind",
            "127.0.0.1",
            "--port",
            str(port),
            "--protected-mode",
            "yes",
            "--save",
            "",
            "--appendonly",
            "no",
            "--dir",
            directory,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        redis = redis_package.Redis.from_url(
            f"redis://127.0.0.1:{port}/0", decode_responses=True
        )
        other_redis = None
        try:
            deadline = asyncio.get_running_loop().time() + 5
            while True:
                try:
                    await redis.ping()
                    break
                except Exception:
                    if asyncio.get_running_loop().time() >= deadline:
                        pytest.fail("temporary Redis server did not become ready")
                    await asyncio.sleep(0.025)

            backend = RedisRelayBackend(
                f"redis://127.0.0.1:{port}/0",
                NODE_ID,
                cluster=False,
                limits=RelayLimits(),
            )
            other_backend = RedisRelayBackend(
                f"redis://127.0.0.1:{port}/0",
                "b" * 32,
                cluster=False,
                limits=RelayLimits(),
            )
            backend._redis = redis
            other_redis = redis_package.Redis.from_url(
                f"redis://127.0.0.1:{port}/0", decode_responses=True
            )
            other_backend._redis = other_redis
            assert await backend.consume_enrollment_nonce(
                "1" * 64,
                "2" * 64,
                ttl_ms=240_000,
                capacity=4096,
            )
            assert not await other_backend.consume_enrollment_nonce(
                "1" * 64,
                "2" * 64,
                ttl_ms=240_000,
                capacity=4096,
            )
            assert await other_backend.consume_enrollment_nonce(
                "1" * 64,
                "3" * 64,
                ttl_ms=240_000,
                capacity=4096,
            )
            monkeypatch.setattr(backend_module, "ENROLLMENT_MAX_NONCES", 2)
            with pytest.raises(RelayBackendError, match="nonce capacity is full"):
                await backend.consume_enrollment_nonce(
                    "2" * 64,
                    "4" * 64,
                    ttl_ms=240_000,
                    capacity=4096,
                )
            assert await redis.zcard(backend._enrollment_nonce_index_key()) == 2
            assert "{mailbox}" in backend._enrollment_nonce_key("1" * 64)
            assert "{mailbox}" in backend._enrollment_nonce_index_key()
            offer_id = "9" * 32
            code = service.generate_enrollment_code(offer_id)
            _, verifier = service.derive_enrollment_code_verifier(code, offer_id)
            verifier_hash = service.enrollment_verifier_hash(offer_id, verifier)
            fields = {
                "issuer_key": "1" * 64,
                "expires_at": str(int(time.time()) + 300),
                "code_verifier_hash": verifier_hash,
                "create_digest": "2" * 64,
            }
            assert (
                await backend.create_enrollment_offer(
                    offer_id, fields, ttl_ms=30_000, capacity=4
                )
                == "created"
            )
            assert (
                await other_backend.create_enrollment_offer(
                    offer_id, fields, ttl_ms=30_000, capacity=4
                )
                == "duplicate"
            )
            capacity_race = await asyncio.gather(
                backend.create_enrollment_offer(
                    "0" * 32, fields, ttl_ms=30_000, capacity=2
                ),
                other_backend.create_enrollment_offer(
                    "f" * 32, fields, ttl_ms=30_000, capacity=2
                ),
            )
            assert sorted(capacity_race) == ["capacity", "created"]
            assert (
                await redis.pttl(backend._enrollment_capacity_key())
                >= ENROLLMENT_CAPACITY_INDEX_TTL_MS - 5_000
            )
            assert (
                await other_backend.submit_enrollment_request(
                    offer_id,
                    round_id="3" * 32,
                    destination_key="4" * 64,
                    candidate_hash=verifier_hash,
                    envelope=b64url(b"request"),
                    content_digest="5" * 64,
                )
                == "accepted"
            )
            first, second = await asyncio.gather(
                backend.claim_enrollment_round(
                    offer_id, issuer_key="1" * 64, claim_id="6" * 32
                ),
                other_backend.claim_enrollment_round(
                    offer_id, issuer_key="1" * 64, claim_id="7" * 32
                ),
            )
            assert sum("envelope" in value for value in (first, second)) == 1
            claim = first if "envelope" in first else second
            assert claim["phase"] == "request"
            assert (
                await other_backend.publish_enrollment_challenge(
                    offer_id,
                    issuer_key="1" * 64,
                    destination_key="4" * 64,
                    round_id="8" * 32,
                    envelope=b64url(b"challenge"),
                    content_digest="9" * 64,
                )
                == "stored"
            )
            assert (
                await backend.poll_enrollment_reply(offer_id, destination_key="4" * 64)
            )["phase"] == "challenge"
            assert (
                await other_backend.submit_enrollment_proof(
                    offer_id,
                    round_id="a" * 32,
                    destination_key="4" * 64,
                    envelope=b64url(b"proof"),
                    content_digest="b" * 64,
                )
                == "stored"
            )
            proof_claim = await other_backend.claim_enrollment_round(
                offer_id, issuer_key="1" * 64, claim_id="c" * 32
            )
            assert proof_claim["phase"] == "proof"
            assert (
                await backend.publish_enrollment_decision(
                    offer_id,
                    issuer_key="1" * 64,
                    destination_key="4" * 64,
                    round_id="d" * 32,
                    envelope=b64url(b"decision"),
                    content_digest="e" * 64,
                )
                == "stored"
            )
            assert (
                await other_backend.poll_enrollment_reply(
                    offer_id, destination_key="4" * 64
                )
            )["phase"] == "decision"
            assert (
                await other_backend.publish_enrollment_decision(
                    offer_id,
                    issuer_key="1" * 64,
                    destination_key="4" * 64,
                    round_id="d" * 32,
                    envelope=b64url(b"decision"),
                    content_digest="e" * 64,
                )
                == "duplicate"
            )
            ack_frame = {
                "v": 1,
                "offer_id": offer_id,
                "destination_key": "4" * 64,
                "round_id": "d" * 32,
                "envelope": b64url(b"encrypted receipt"),
                "issued_at": 1,
                "nonce": "2" * 32,
                "signature": "3" * 128,
            }
            assert (
                await other_backend.submit_enrollment_install_ack(
                    offer_id,
                    round_id="d" * 32,
                    destination_key="4" * 64,
                    frame=ack_frame,
                    content_digest="6" * 64,
                )
                == "stored"
            )
            assert (
                await backend.submit_enrollment_install_ack(
                    offer_id,
                    round_id="d" * 32,
                    destination_key="4" * 64,
                    frame=ack_frame,
                    content_digest="6" * 64,
                )
                == "duplicate"
            )
            assert await backend.poll_enrollment_install_ack(
                offer_id, issuer_key="1" * 64
            ) == {"status": "ready", "frame": ack_frame}
            assert await backend.poll_enrollment_install_ack(
                offer_id, issuer_key="2" * 64
            ) == {"status": "unavailable"}
            monkeypatch.setattr(backend_module, "ENROLLMENT_MAX_RATE_SOURCES", 1)
            assert await backend.consume_enrollment_rate(
                "f" * 64, limit=2, window_ms=60_000
            )
            assert not await backend.consume_enrollment_rate(
                "g" * 64, limit=2, window_ms=60_000
            )
            assert await redis.zcard(backend._enrollment_rate_index_key()) == 1
            assert "{mailbox}" in backend._enrollment_rate_key("f" * 64)
            assert "{mailbox}" in backend._enrollment_rate_index_key()
            usage = await backend.enrollment_admission_usage()
            assert usage == {"rate_sources": 1, "nonce_records": 2}
        finally:
            if other_redis is not None:
                await other_redis.aclose()
            await redis.aclose()
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()


@pytest.mark.asyncio
async def test_lookup_route_resolves_offer_and_is_origin_bound(relay_client):
    issuer = SigningKey.generate()
    device = SigningKey.generate()
    await register_issuer(relay_client, issuer, ROOM_CAPABILITY, SESSION)

    offer_id = "1" * 32
    code = service.generate_enrollment_code(offer_id)
    _, verifier = service.derive_enrollment_code_verifier(code, offer_id)
    lookup_tag = service.derive_enrollment_lookup_tag(code, ORIGIN)
    lookup_hash = service.enrollment_lookup_hash(lookup_tag)
    create = _issuer_fields(
        issuer,
        offer_id,
        service.ENROLLMENT_OFFERS_PATH,
        expires_at=int(time.time()) + 300,
        code_verifier_hash=service.enrollment_verifier_hash(offer_id, verifier),
        lookup_hash=lookup_hash,
    )
    status, result = await post_json(relay_client, service.ENROLLMENT_OFFERS_PATH, create)
    assert status == 201

    lookup_body = _lookup_fields(device, service.ENROLLMENT_LOOKUP_PATH, lookup=lookup_tag)
    status, found = await post_json(relay_client, service.ENROLLMENT_LOOKUP_PATH, lookup_body)
    assert status == 200
    assert found == {"offer_id": offer_id}

    # A different relay origin derives a different tag; it does not resolve.
    other_origin_tag = service.derive_enrollment_lookup_tag(code, "https://other.example")
    other_body = _lookup_fields(device, service.ENROLLMENT_LOOKUP_PATH, lookup=other_origin_tag)
    status, miss = await post_json(relay_client, service.ENROLLMENT_LOOKUP_PATH, other_body)
    assert status == 404
    assert miss == {"error": "unavailable"}

    # A wrong guess gets the exact same generic error as a miss.
    wrong_tag = service.derive_enrollment_lookup_tag(
        service.generate_enrollment_code(offer_id), ORIGIN
    )
    wrong_body = _lookup_fields(device, service.ENROLLMENT_LOOKUP_PATH, lookup=wrong_tag)
    status, wrong = await post_json(relay_client, service.ENROLLMENT_LOOKUP_PATH, wrong_body)
    assert status == 404
    assert wrong == miss == {"error": "unavailable"}


@pytest.mark.asyncio
async def test_lookup_route_keeps_the_k1_path_working_without_it(relay_client):
    """0.10.7 clients never call /lookup; their offer id is in the code."""
    issuer = SigningKey.generate()
    device = SigningKey.generate()
    await register_issuer(relay_client, issuer, ROOM_CAPABILITY, SESSION)

    offer_id = "2" * 32
    legacy_code = f"K1-{offer_id}-ABCD-EFGH-JKMN-PQRS-TVWX"
    _, verifier = service.derive_enrollment_code_verifier(legacy_code)
    verifier_hash = service.enrollment_verifier_hash(offer_id, verifier)
    create = _issuer_fields(
        issuer,
        offer_id,
        service.ENROLLMENT_OFFERS_PATH,
        expires_at=int(time.time()) + 300,
        code_verifier_hash=verifier_hash,
    )
    status, result = await post_json(relay_client, service.ENROLLMENT_OFFERS_PATH, create)
    assert status == 201

    request_path = f"/relay/v1/enrollment/offers/{offer_id}/request"
    request = _destination_fields(
        device,
        offer_id,
        request_path,
        round_id="3" * 32,
        code_verifier=verifier,
        envelope=b64url(b"legacy request"),
    )
    status, accepted = await post_json(relay_client, request_path, request)
    assert status == 202
    assert accepted == {"status": "pending", "receipt": "3" * 32}


@pytest.mark.asyncio
async def test_submit_request_carries_validates_and_defaults_device_name(relay_client):
    issuer = SigningKey.generate()
    device_a = SigningKey.generate()
    device_b = SigningKey.generate()
    await register_issuer(relay_client, issuer, ROOM_CAPABILITY, SESSION)

    async def offer(offer_id: str) -> str:
        code = service.generate_enrollment_code(offer_id)
        _, verifier = service.derive_enrollment_code_verifier(code, offer_id)
        create = _issuer_fields(
            issuer,
            offer_id,
            service.ENROLLMENT_OFFERS_PATH,
            expires_at=int(time.time()) + 300,
            code_verifier_hash=service.enrollment_verifier_hash(offer_id, verifier),
        )
        status, _ = await post_json(relay_client, service.ENROLLMENT_OFFERS_PATH, create)
        assert status == 201
        return verifier

    # A named device: the relay validates and stores it, the issuer sees it.
    offer_id = "4" * 32
    verifier = await offer(offer_id)
    request_path = f"/relay/v1/enrollment/offers/{offer_id}/request"
    request = _destination_fields(
        device_a,
        offer_id,
        request_path,
        round_id="5" * 32,
        code_verifier=verifier,
        envelope=b64url(b"req"),
        device_name="mac-kollab",
    )
    status, _ = await post_json(relay_client, request_path, request)
    assert status == 202
    poll_path = service.ENROLLMENT_ISSUER_POLL_PATH.format(offer_id=offer_id)
    status, claimed = await post_json(
        relay_client, poll_path, _issuer_fields(issuer, offer_id, poll_path, claim_id="6" * 32)
    )
    assert status == 200
    assert claimed["device_name"] == "mac-kollab"

    # An invalid name is rejected outright.
    offer_id = "7" * 32
    verifier = await offer(offer_id)
    request_path = f"/relay/v1/enrollment/offers/{offer_id}/request"
    bad_request = _destination_fields(
        device_b,
        offer_id,
        request_path,
        round_id="8" * 32,
        code_verifier=verifier,
        envelope=b64url(b"req"),
        device_name="Not Valid!",
    )
    status, result = await post_json(relay_client, request_path, bad_request)
    assert status == 400
    assert result == {"error": "invalid_request"}

    # An older joiner that never sends the field still works; it defaults "".
    offer_id = "9" * 32
    verifier = await offer(offer_id)
    request_path = f"/relay/v1/enrollment/offers/{offer_id}/request"
    absent_request = _destination_fields(
        device_b,
        offer_id,
        request_path,
        round_id="a" * 32,
        code_verifier=verifier,
        envelope=b64url(b"req"),
    )
    status, _ = await post_json(relay_client, request_path, absent_request)
    assert status == 202
    poll_path = service.ENROLLMENT_ISSUER_POLL_PATH.format(offer_id=offer_id)
    status, claimed = await post_json(
        relay_client, poll_path, _issuer_fields(issuer, offer_id, poll_path, claim_id="b" * 32)
    )
    assert claimed["device_name"] == ""


@pytest.mark.asyncio
async def test_offer_is_burned_after_five_failures_and_lookup_stops_working(relay_client):
    issuer = SigningKey.generate()
    device = SigningKey.generate()
    await register_issuer(relay_client, issuer, ROOM_CAPABILITY, SESSION)

    offer_id = "c" * 32
    code = service.generate_enrollment_code(offer_id)
    _, verifier = service.derive_enrollment_code_verifier(code, offer_id)
    lookup_tag = service.derive_enrollment_lookup_tag(code, ORIGIN)
    lookup_hash = service.enrollment_lookup_hash(lookup_tag)
    create = _issuer_fields(
        issuer,
        offer_id,
        service.ENROLLMENT_OFFERS_PATH,
        expires_at=int(time.time()) + 300,
        code_verifier_hash=service.enrollment_verifier_hash(offer_id, verifier),
        lookup_hash=lookup_hash,
    )
    status, _ = await post_json(relay_client, service.ENROLLMENT_OFFERS_PATH, create)
    assert status == 201

    wrong_verifier = b64url(b"z" * 32)
    request_path = f"/relay/v1/enrollment/offers/{offer_id}/request"
    for attempt in range(backend_module.ENROLLMENT_MAX_FAILED_CODES):
        request = _destination_fields(
            device,
            offer_id,
            request_path,
            round_id=f"{attempt + 1:032x}",
            code_verifier=wrong_verifier,
            envelope=b64url(b"req"),
        )
        status, result = await post_json(relay_client, request_path, request)
        if attempt + 1 == backend_module.ENROLLMENT_MAX_FAILED_CODES:
            assert status == 404
            assert result == {"error": "unavailable"}
        else:
            assert status == 401
            assert result == {"error": "invalid_contact"}

    # Burned outright: the lookup it was registered under no longer resolves.
    lookup_body = _lookup_fields(device, service.ENROLLMENT_LOOKUP_PATH, lookup=lookup_tag)
    status, missing = await post_json(relay_client, service.ENROLLMENT_LOOKUP_PATH, lookup_body)
    assert status == 404
    assert missing == {"error": "unavailable"}


@pytest.mark.asyncio
async def test_lookup_rate_limit_is_the_existing_per_source_bucket(relay_client, monkeypatch):
    monkeypatch.setattr(service, "ENROLLMENT_RATE_LIMIT", 1)
    device = SigningKey.generate()
    first = _lookup_fields(device, service.ENROLLMENT_LOOKUP_PATH, lookup=b64url(b"x" * 32))
    status, _ = await post_json(relay_client, service.ENROLLMENT_LOOKUP_PATH, first)
    assert status in (401, 404)  # not a real offer; rate limiting is what's tested

    second = _lookup_fields(device, service.ENROLLMENT_LOOKUP_PATH, lookup=b64url(b"y" * 32))
    status, limited = await post_json(relay_client, service.ENROLLMENT_LOOKUP_PATH, second)
    assert status == 429
    assert limited == {"error": "rate_limited"}
