from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from nacl.signing import SigningKey

from kollabor_config.provisioned_state import ProvisionedStateFile
from plugins.hub import enrollment_client
from plugins.hub.dns.private_directory import (
    PrivateDirectory,
    prove_pairing,
    public_key_id,
)
from plugins.hub.enrollment_client import (
    EnrollmentHTTPClient,
    EnrollmentIssuer,
    EnrollmentProtocolError,
    _ActiveEnrollmentOffer,
    decrypt_enrollment_envelope,
    encrypt_enrollment_envelope,
    enroll_device,
    sign_enrollment_payload,
    signed_enrollment_frame,
)
from plugins.hub.enrollment_codes import (
    derive_enrollment_code_verifier,
    derive_enrollment_envelope_key,
    enrollment_verifier_hash,
    generate_enrollment_code,
)
from plugins.hub.enrollment_delegations import EnrollmentDelegationStore
from plugins.hub.enrollment_recovery import (
    EnrollmentRecoveryJournal,
    derive_enrollment_recovery_key,
)
from plugins.hub.provisioning import (
    NetworkPreferences,
    ProvisioningExpectation,
    ProvisioningPayload,
    ProvisioningScope,
    install_provisioning_bundle,
    seal_provisioning_bundle,
)
from plugins.hub.provisioning_store import FilesystemProvisioningStore
from plugins.hub.relay_client import RelayClient
from plugins.hub.relay_state import parse_invite


def _fake_openai_profile(secret: str = "owner-provider-secret"):
    return SimpleNamespace(
        name="owner-provider-profile",
        auth_type="api_key",
        is_provisioned=False,
        context_window=32768,
        organization=None,
        api_version=None,
        azure_endpoint=None,
        deployment_id=None,
        http_referer=None,
        x_title=None,
        project_id=None,
        location=None,
        store_responses=None,
        get_provider=lambda: "openai",
        get_model=lambda: "gpt-4.1",
        get_max_tokens=lambda: 4096,
        get_endpoint=lambda: "",
        get_temperature=lambda: 0.4,
        get_top_p=lambda: None,
        get_effort=lambda: "",
        get_api_key=lambda: secret,
    )


class _FakeResponseContent:
    def __init__(self, body: bytes):
        self.body = body

    async def iter_chunked(self, size: int):
        for offset in range(0, len(self.body), size):
            yield self.body[offset : offset + size]

    async def read(self, size: int = -1) -> bytes:
        return self.body if size < 0 else self.body[:size]


class _FakeResponse:
    def __init__(
        self,
        url: str,
        body: bytes,
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ):
        self.url = url
        self.status = status
        self.content_type = "application/json"
        self.headers = {"Content-Encoding": "identity", **(headers or {})}
        self.content = _FakeResponseContent(body)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None


class _FakeSession:
    def __init__(self, response: _FakeResponse):
        self.response = response
        self.request_bodies: list[bytes] = []

    def post(self, _url: str, *, data: bytes, **_kwargs):
        self.request_bodies.append(data)
        return self.response


def test_phase_envelope_requires_an_integer_protocol_version():
    payload = {
        "v": True,
        "offer_id": "a" * 32,
        "round_id": "b" * 32,
        "phase": "request",
        "destination_key": "c" * 64,
    }

    with pytest.raises(EnrollmentProtocolError, match="invalid_response"):
        enrollment_client.validate_phase_envelope(
            payload,
            offer_id="a" * 32,
            round_id="b" * 32,
            phase="request",
            destination_key="c" * 64,
        )


def test_provisioning_scope_accepts_one_allowlisted_provider_credential():
    scope = enrollment_client._provisioning_scope_from_payload(
        {
            "enrollment_id": "a" * 32,
            "network_ids": ["network:one"],
            "audience": "b" * 32,
            "profile_name": "kollab-device",
            "allowed_credential_categories": ["provider:openai:api_key"],
            "allowed_agent_names": [],
            "allowed_skill_names": [],
        },
        enrollment_id="a" * 32,
        workspace_id="b" * 32,
    )

    assert scope.profile_name == "kollab-device"
    assert scope.allowed_credential_categories == frozenset({"provider:openai:api_key"})
    with pytest.raises(EnrollmentProtocolError, match="invalid_response"):
        enrollment_client._provisioning_scope_from_payload(
            {
                "enrollment_id": "a" * 32,
                "network_ids": ["network:one"],
                "audience": "b" * 32,
                "profile_name": "kollab-device",
                "allowed_credential_categories": ["provider:aws:secret"],
                "allowed_agent_names": [],
                "allowed_skill_names": [],
            },
            enrollment_id="a" * 32,
            workspace_id="b" * 32,
        )


@pytest.mark.asyncio
async def test_openai_oauth_profile_plan_keeps_reusable_tokens_out_of_offer_metadata(
    monkeypatch,
):
    tokens = SimpleNamespace(
        access_token="private-access-token",
        refresh_token="private-refresh-token",
        expires_at=4_102_444_800.0,
        account_id="private-account-id",
    )

    token_reads = []

    class FakeOAuthTokenStorage:
        async def load_tokens(self, provider, auto_refresh, *, profile_name=None):
            assert provider == "openai"
            assert auto_refresh is False
            assert profile_name is None
            token_reads.append(provider)
            return tokens

    from kollabor_ai.oauth import token_storage

    monkeypatch.setattr(token_storage, "OAuthTokenStorage", FakeOAuthTokenStorage)
    profile = _fake_openai_profile()
    profile.name = "owner-oauth-profile"
    profile.provider = "openai_responses"
    profile.auth_type = "oauth"
    profile.get_provider = lambda: "openai_responses"
    profile.get_endpoint = lambda: "https://chatgpt.com/backend-api/codex"
    plugin = SimpleNamespace(
        event_bus=SimpleNamespace(
            get_service=lambda _name: SimpleNamespace(
                get_active_profile=lambda: profile,
                get_profile=lambda name: profile if name == profile.name else None,
            )
        )
    )
    issuer = EnrollmentIssuer(SimpleNamespace(plugin=plugin))

    plan = await issuer._make_provisioning_plan("a" * 32)
    assert plan is not None
    assert plan.credential_category == "provider:openai:oauth_tokens"
    assert token_reads == []
    assert "private-access-token" not in repr(plan)
    assert "private-refresh-token" not in repr(plan)
    assert "private-account-id" not in repr(plan)

    _profile, credentials = await issuer._accepted_profile_payload(SimpleNamespace(provisioning_plan=plan))
    assert credentials[0].secret.access_token == "private-access-token"
    assert credentials[0].secret.refresh_token == "private-refresh-token"
    assert credentials[0].secret.account_id == "private-account-id"
    assert token_reads == ["openai"]

    tokens.access_token = "a" * 5000
    tokens.refresh_token = "b" * 5000
    with pytest.raises(EnrollmentProtocolError, match="unavailable"):
        await issuer._accepted_profile_payload(SimpleNamespace(provisioning_plan=plan))
    assert token_reads == ["openai", "openai"]


def test_embedded_maximum_provisioning_bundle_exceeds_decision_envelope_limit():
    code = generate_enrollment_code("0123456789abcdef0123456789abcdef")
    key = derive_enrollment_envelope_key(code)
    decision = {
        "v": 1,
        "offer_id": "0123456789abcdef0123456789abcdef",
        "round_id": "1" * 32,
        "phase": "decision",
        "destination_key": "2" * 64,
        "provisioning_bundle": "A" * 27_307,
    }
    try:
        with pytest.raises(EnrollmentProtocolError, match="request_too_large"):
            encrypt_enrollment_envelope(key, decision)
    finally:
        code.wipe()
        key.wipe()


@pytest.mark.asyncio
async def test_idempotent_post_retries_transport_with_identical_phase_fields(
    monkeypatch,
):
    client = EnrollmentHTTPClient("https://example.test")
    calls = []

    async def post_signed(_key, path, offer_id, fields, **_kwargs):
        calls.append((path, offer_id, dict(fields)))
        if len(calls) == 1:
            raise EnrollmentProtocolError("transport")
        return {"status": "stored"}

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(client, "post_signed", post_signed)
    monkeypatch.setattr(enrollment_client.asyncio, "sleep", no_sleep)
    fields = {"round_id": "d" * 32, "envelope": "ciphertext"}

    result = await client.post_signed_retry(
        SigningKey.generate(),
        "/relay/v1/enrollment/offers/" + "a" * 32 + "/challenge",
        "a" * 32,
        fields,
    )

    assert result == {"status": "stored"}
    assert len(calls) == 2
    assert calls[0] == calls[1]


@pytest.mark.asyncio
async def test_http_client_accepts_the_relays_maximum_envelope_size():
    url = "https://example.test/relay/v1/enrollment/offers/" + "a" * 32 + "/request"
    envelope = "A" * (4 * (24 * 1024) // 3)
    response_body = json.dumps({"envelope": envelope}, separators=(",", ":")).encode()
    client = EnrollmentHTTPClient("https://example.test")
    session = _FakeSession(_FakeResponse(url, response_body))
    client._session = session

    result = await client.post(url.removeprefix("https://example.test"), {"envelope": envelope})

    assert result == {"envelope": envelope}
    assert len(session.request_bodies) == 1
    assert len(session.request_bodies[0]) <= enrollment_client._MAX_HTTP_BODY_BYTES
    assert len(response_body) <= enrollment_client._MAX_RESPONSE_BYTES


@pytest.mark.asyncio
async def test_http_client_rejects_bodies_above_its_bounded_limit():
    url = "https://example.test/relay/v1/enrollment/offers/" + "a" * 32 + "/request"
    client = EnrollmentHTTPClient("https://example.test")
    session = _FakeSession(_FakeResponse(url, b'{"status":"ok"}'))
    client._session = session

    with pytest.raises(EnrollmentProtocolError, match="request_too_large"):
        await client.post(
            "/relay/v1/enrollment/offers/" + "a" * 32 + "/request",
            {"envelope": "A" * 40_960},
        )

    assert session.request_bodies == []


@pytest.mark.asyncio
async def test_http_client_reads_bounded_retry_after_header():
    path = "/relay/v1/enrollment/offers/" + "a" * 32 + "/request"
    client = EnrollmentHTTPClient("https://example.test")
    client._session = _FakeSession(
        _FakeResponse(
            "https://example.test" + path,
            b'{"error":"rate_limited"}',
            status=429,
            headers={"Retry-After": "45"},
        )
    )

    with pytest.raises(EnrollmentProtocolError) as captured:
        await client.post(path, {})

    assert captured.value.code == "rate_limited"
    assert captured.value.retry_after_seconds == 45


@pytest.mark.asyncio
async def test_phase_retry_observes_server_retry_after(monkeypatch):
    client = EnrollmentHTTPClient("https://example.test")
    calls = 0
    delays = []

    async def post_signed(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise EnrollmentProtocolError("rate_limited", retry_after_seconds=37)
        return {"status": "stored"}

    async def record_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(client, "post_signed", post_signed)
    monkeypatch.setattr(enrollment_client.asyncio, "sleep", record_sleep)
    result = await client.post_signed_retry(
        SigningKey.generate(),
        "/relay/v1/enrollment/offers/" + "a" * 32 + "/challenge",
        "a" * 32,
        {"round_id": "b" * 32, "envelope": "ciphertext"},
    )

    assert result == {"status": "stored"}
    assert delays == [37]


def test_destination_recovery_retry_delay_is_capped():
    assert [enrollment_client._destination_retry_delay(attempt) for attempt in range(1, 9)] == [
        5,
        10,
        20,
        40,
        80,
        160,
        300,
        300,
    ]
    assert enrollment_client._destination_retry_delay(10_000) == 300
    for invalid_attempt in (0, -1, True, 1.5):
        with pytest.raises(ValueError, match="positive integer"):
            enrollment_client._destination_retry_delay(invalid_attempt)


def test_issuer_recovery_retry_is_bounded_and_uses_fixed_codes(tmp_path):
    offer_id = "0123456789abcdef0123456789abcdef"
    journal = EnrollmentRecoveryJournal(
        tmp_path / "issuer-recovery.json",
        derive_enrollment_recovery_key(b"i" * 32),
    )
    record = {
        "offer_id": offer_id,
        "retry_attempts": 0,
        "retry_after": 0,
        "last_error_code": None,
    }

    for _ in range(12):
        enrollment_client._schedule_issuer_recovery_retry(
            journal, record, "conflict", now=1000
        )

    saved = journal.get(offer_id)
    assert saved["retry_attempts"] == enrollment_client._DESTINATION_RETRY_MAX_ATTEMPTS
    assert saved["retry_after"] == 1000 + enrollment_client._DESTINATION_RETRY_MAX_SECONDS
    assert saved["last_error_code"] == "conflict"

    enrollment_client._schedule_issuer_recovery_retry(
        journal, saved, [], now=2000
    )
    assert journal.get(offer_id)["last_error_code"] == "transport"


async def _run_enrollment(
    tmp_path,
    monkeypatch,
    *,
    tamper_challenge=False,
    wrong_challenge_audience=False,
    expired_challenge=False,
    lose_ack_response=False,
    publisher_key=None,
    claim_other_owner=False,
):
    origin = "https://kollabor.ai"
    offer_id = "0123456789abcdef0123456789abcdef"
    # This helper exercises the deep pairing protocol (challenge/proof/
    # decision/ack); FakeTransport.post below resolves the short code's
    # lookup round trip so enroll_device can find this fixed offer id.
    code = generate_enrollment_code(offer_id)
    code_text = code.for_private_display()
    verifier = derive_enrollment_code_verifier(code)
    envelope_key = derive_enrollment_envelope_key(code)
    owner_signing_key = SigningKey.generate()
    owner_public_key = owner_signing_key.verify_key.encode()

    owner_relay = RelayClient(
        tmp_path / "owner-workspace",
        state_dir=tmp_path / "owner-network",
        label="owner",
    )
    owner_relay.state.origin = origin
    owner_relay._store.save()
    destination = RelayClient(
        tmp_path / "destination-workspace",
        state_dir=tmp_path / "destination-network",
        label="destination",
    )
    owner_directory = PrivateDirectory(
        tmp_path / "owner-network" / "private-directory.json",
        owner_public_key=owner_public_key,
        workspace_id=owner_relay.state.workspace_id,
    )
    pairing_challenge = owner_directory.begin_pairing(
        owner_signing_key,
        expected_device_public_key=bytes.fromhex(destination.public_key),
        expires_in_seconds=300,
    )
    discovery = SimpleNamespace(
        origin=origin,
        manifest={
            "coordinator": {"public_key": (publisher_key or owner_public_key).hex()},
            "endpoints": {"control": origin + "/relay/v1"},
        },
    )
    commands = SimpleNamespace(
        client=destination,
        _provisioning_store=FilesystemProvisioningStore(
            ProvisionedStateFile(destination.state_dir / "provisioned-state.json")
        ),
        _discover=AsyncMock(return_value=(discovery, "", (), False)),
        _relay_url=lambda _discovery: "wss://kollabor.ai/relay/v1/ws",
    )

    async def attach(_discovery, _ca, _cidrs):
        commands.client.state.origin = origin
        commands.client.state.enabled = True
        commands.client._state = "online"
        commands.client._session_id = "destination-session"
        return "connected"

    commands._attach = AsyncMock(side_effect=attach)

    class FakeTransport:
        def __init__(self):
            self.reply_polls = 0
            self.owner_relay = owner_relay
            self.envelope_key = envelope_key
            self.code_text = code_text
            self.challenge_payload = {
                "v": 1,
                "offer_id": offer_id,
                "round_id": "",
                "phase": "challenge",
                "destination_key": destination.public_key,
                "owner_key": owner_public_key.hex(),
                "issuer_relay_key": owner_relay.public_key,
                "origin": origin,
                "expires_at": int(time.time()) - 1 if expired_challenge else pairing_challenge.expires_at,
                "pairing_challenge": pairing_challenge.token,
                "provisioning_scope": {
                    "enrollment_id": "",
                    "network_ids": ["network-alpha"],
                    "audience": ("a" * 32 if wrong_challenge_audience else destination.state.workspace_id),
                    "profile_name": None,
                    "allowed_credential_categories": [],
                    "allowed_agent_names": [],
                    "allowed_skill_names": [],
                },
            }
            self.decision_envelope = ""
            self.requests = []
            self.ack_envelopes = []
            self.lose_ack_response = lose_ack_response

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, path, _frame, **_kwargs):
            assert path == enrollment_client.ENROLLMENT_LOOKUP_PATH
            return {"offer_id": offer_id}

        async def post_signed_retry(self, *args, **kwargs):
            return await self.post_signed(*args, **kwargs)

        async def post_signed(self, _signing_key, path, _offer_id, fields, **_kwargs):
            self.requests.append((path, dict(fields)))
            if path.endswith("/request"):
                assert fields["code_verifier"] == verifier.for_protocol()
                journaled = enrollment_client._destination_recovery_journal(
                    commands.client
                ).get(offer_id)
                assert journaled["status"] == "request_ready"
                assert journaled["round_id"] == fields["round_id"]
                assert journaled["request_envelope"] == fields["envelope"]
                request = decrypt_enrollment_envelope(self.envelope_key, fields["envelope"])
                assert request == {
                    "v": 1,
                    "offer_id": offer_id,
                    "round_id": fields["round_id"],
                    "phase": "request",
                    "destination_key": destination.public_key,
                    "workspace_id": destination.state.workspace_id,
                }
                self.challenge_payload["round_id"] = fields["round_id"]
                self.challenge_payload["provisioning_scope"]["enrollment_id"] = fields["round_id"]
                return {"status": "pending", "receipt": fields["round_id"]}
            if path.endswith("/reply/poll"):
                self.reply_polls += 1
                if self.reply_polls == 1:
                    challenge_payload = dict(self.challenge_payload)
                    if claim_other_owner:
                        challenge_payload["owner_key"] = SigningKey.generate().verify_key.encode().hex()
                    payload = sign_enrollment_payload(owner_signing_key, challenge_payload)
                    if tamper_challenge:
                        payload["owner_signature"] = "0" * 128
                    return {
                        "status": "ready",
                        "phase": "challenge",
                        "round_id": self.challenge_payload["round_id"],
                        "envelope": encrypt_enrollment_envelope(self.envelope_key, payload),
                    }
                return {
                    "status": "ready",
                    "phase": "decision",
                    "round_id": self.challenge_payload["round_id"],
                    "envelope": self.decision_envelope,
                }
            if path.endswith("/proof"):
                proof = decrypt_enrollment_envelope(self.envelope_key, fields["envelope"])
                owner_directory.record_pairing_proof(pairing_challenge.token, proof["proof_token"])
                credential = owner_directory.approve_pairing(
                    pairing_challenge.token,
                    proof["proof_token"],
                    owner_signing_key,
                    approved_by_human=True,
                    scopes=("conversation:send",),
                )
                scope = ProvisioningScope(
                    enrollment_id=self.challenge_payload["round_id"],
                    network_ids=("network-alpha",),
                    audience=destination.state.workspace_id,
                    profile_name=None,
                )
                provisioning_bundle = seal_provisioning_bundle(
                    ProvisioningPayload(
                        profile=None,
                        networks=(NetworkPreferences("network-alpha", "kollabor.ai"),),
                    ),
                    scope=scope,
                    issuer_key=owner_signing_key,
                    recipient_public_key=bytes.fromhex(destination.public_key),
                    revision=1,
                    expires_at=int(time.time()) + 300,
                )
                decision = sign_enrollment_payload(
                    owner_signing_key,
                    {
                        "v": 1,
                        "offer_id": offer_id,
                        "round_id": self.challenge_payload["round_id"],
                        "phase": "decision",
                        "destination_key": destination.public_key,
                        "owner_key": owner_public_key.hex(),
                        "issuer_relay_key": owner_relay.public_key,
                        "origin": origin,
                        "status": "approved",
                        "credential": credential.token,
                        "invite": owner_relay.invite(),
                        "provisioning_bundle": base64.urlsafe_b64encode(provisioning_bundle)
                        .rstrip(b"=")
                        .decode("ascii"),
                    },
                )
                self.decision_envelope = encrypt_enrollment_envelope(self.envelope_key, decision)
                return {"status": "stored", "receipt": fields["round_id"]}
            if path.endswith("/ack"):
                self.ack_envelopes.append(fields["envelope"])
                payload = decrypt_enrollment_envelope(self.envelope_key, fields["envelope"])
                assert payload["phase"] == "installation_ack"
                assert payload["workspace_id"] == destination.state.workspace_id
                assert payload["status"] == "installed"
                assert payload["revision"] == 1
                assert len(payload["digest"]) == 64
                assert enrollment_client.verify_installation_receipt(destination.public_key, payload)
                destination_directory = PrivateDirectory(
                    destination.state_dir / "private-directory.json",
                    owner_public_key=owner_public_key,
                    workspace_id=destination.state.workspace_id,
                )
                assert destination_directory.members() == ()
                if self.lose_ack_response:
                    self.lose_ack_response = False
                    raise EnrollmentProtocolError("transport")
                return {"status": "stored", "receipt": fields["round_id"]}
            raise AssertionError(f"unexpected endpoint: {path}")

    fake_transport = FakeTransport()

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(
        enrollment_client,
        "EnrollmentHTTPClient",
        lambda *_args, **_kwargs: fake_transport,
    )
    monkeypatch.setattr(enrollment_client.asyncio, "sleep", no_sleep)
    try:
        result = await enroll_device(commands, "kollabor.ai", code_text)
        return result, commands, destination, fake_transport, owner_directory
    finally:
        for secret in (code, verifier, envelope_key):
            secret.wipe()


async def _restart_and_recover_destination(
    tmp_path, commands, transport, offer_id, *, before_recovery=None
):
    restarted = RelayClient(
        tmp_path / "destination-workspace",
        state_dir=tmp_path / "destination-network",
        label="destination-restarted",
    )
    commands.client = restarted
    if before_recovery is not None:
        before_recovery(restarted)
    journal = enrollment_client._destination_recovery_journal(restarted)
    record = journal.get(offer_id)
    record["retry_after"] = 0
    journal.put(offer_id, record)
    transport.envelope_key = enrollment_client.EnrollmentEnvelopeKey(
        offer_id,
        enrollment_client._decode_recovery_value(record["envelope_key"], maximum=64),
    )
    bridge = SimpleNamespace(
        commands=commands,
        _closed=False,
        owner=SimpleNamespace(state_dir=tmp_path / "destination-network"),
    )
    issuer = EnrollmentIssuer(bridge)
    await issuer._restore_pending_destination_enrollments()
    tasks = tuple(issuer._destination_tasks.values())
    if tasks:
        await asyncio.gather(*tasks)
    transport.envelope_key.wipe()
    return restarted, issuer


@pytest.mark.asyncio
async def test_device_enrollment_completes_signed_encrypted_pairing(tmp_path, monkeypatch):
    result, commands, destination, transport, owner_directory = await _run_enrollment(tmp_path, monkeypatch)

    assert result == {"status": "approved"}, [path for path, _fields in transport.requests]
    assert destination.state.origin == "https://kollabor.ai"
    assert destination.state.inviter
    assert destination.state.approvals == [destination.state.inviter]
    commands._attach.assert_awaited_once()
    assert owner_directory.members()
    assert commands._provisioning_store._state_file.get_network_preferences() == {"network-alpha": "kollabor.ai"}
    assert [
        (
            "request"
            if path.endswith("/request")
            else ("reply/poll" if path.endswith("/reply/poll") else "ack" if path.endswith("/ack") else "proof")
        )
        for path, _ in transport.requests
    ] == [
        "request",
        "reply/poll",
        "proof",
        "reply/poll",
        "ack",
    ]


@pytest.mark.asyncio
async def test_destination_replays_exact_ack_after_lost_response_and_restart(tmp_path, monkeypatch):
    result, commands, destination, transport, owner_directory = await _run_enrollment(
        tmp_path, monkeypatch, lose_ack_response=True
    )
    assert result == {"error": "transport"}
    assert len(transport.ack_envelopes) == 1
    saved_ack = transport.ack_envelopes[0]
    assert owner_directory.members()
    assert commands._attach.await_count == 0

    journal = enrollment_client._destination_recovery_journal(destination)
    offer_id = "0123456789abcdef0123456789abcdef"
    saved_record = journal.get(offer_id)
    assert saved_record is not None
    assert saved_record["status"] == "ack_ready"
    assert saved_record["ack_envelope"] == saved_ack
    assert saved_record["retry_attempts"] == 1
    assert saved_record["last_error_code"] == "transport"
    assert saved_record["retry_after"] > int(time.time())
    assert saved_record["retry_after"] <= int(time.time()) + 300
    journal_bytes = journal._path.read_bytes()
    assert transport.code_text.encode("ascii") not in journal_bytes
    assert saved_record["round_id"]

    bridge = SimpleNamespace(
        commands=commands,
        _closed=False,
        owner=SimpleNamespace(state_dir=destination.state_dir),
    )
    status_issuer = EnrollmentIssuer(bridge)
    assert await status_issuer._restore_pending_destination_enrollments() == 0
    recovery_status = status_issuer.destination_recovery_status()
    assert recovery_status["pending"] == 1
    assert recovery_status["active"] == 0
    assert recovery_status["last_error_code"] == "transport"
    assert 0 < recovery_status["retry_in_seconds"] <= 300
    status_text = json.dumps(recovery_status)
    assert offer_id not in status_text
    assert transport.code_text not in status_text

    saved_invite = parse_invite(transport.owner_relay.invite())
    extra_approval = SigningKey.generate().verify_key.encode().hex()

    def restore_matching_invite(restarted_client):
        restarted_client.state.origin = saved_invite["origin"]
        restarted_client.state.room = saved_invite["room"]
        restarted_client.state.inviter = saved_invite["key"]
        restarted_client.state.approvals = [saved_invite["key"], extra_approval]
        restarted_client._store.save()

    # Exercise startup replay after the invite write. The existing exact invite
    # is retained, including unrelated local approvals.
    restarted, _issuer = await _restart_and_recover_destination(
        tmp_path,
        commands,
        transport,
        offer_id,
        before_recovery=restore_matching_invite,
    )

    assert transport.ack_envelopes == [saved_ack, saved_ack]
    assert restarted.state.origin == "https://kollabor.ai"
    assert restarted.state.inviter == saved_invite["key"]
    assert extra_approval in restarted.state.approvals
    assert owner_directory.members()
    commands._attach.assert_awaited_once()
    assert journal.get(offer_id) is None


@pytest.mark.asyncio
async def test_destination_recovery_rejects_conflicting_invite_without_ack_or_attach(
    tmp_path, monkeypatch
):
    result, commands, _destination, transport, _owner_directory = await _run_enrollment(
        tmp_path, monkeypatch, lose_ack_response=True
    )
    assert result == {"error": "transport"}
    saved_ack = transport.ack_envelopes[0]
    ack_count = len(transport.ack_envelopes)

    restarted = RelayClient(
        tmp_path / "destination-workspace",
        state_dir=tmp_path / "destination-network",
        label="destination-restarted",
    )
    restarted.state.origin = "https://other.example"
    restarted.state.room = "c" * 64
    restarted.state.inviter = transport.owner_relay.public_key
    restarted.state.approvals = [transport.owner_relay.public_key]
    restarted._store.save()
    retry_record = enrollment_client._destination_recovery_journal(restarted).get(
        "0123456789abcdef0123456789abcdef"
    )
    retry_record["retry_after"] = 0
    enrollment_client._destination_recovery_journal(restarted).put(
        retry_record["offer_id"], retry_record
    )
    commands.client = restarted
    bridge = SimpleNamespace(
        commands=commands,
        _closed=False,
        owner=SimpleNamespace(state_dir=tmp_path / "destination-network"),
    )
    issuer = EnrollmentIssuer(bridge)
    await issuer._restore_pending_destination_enrollments()
    tasks = tuple(issuer._destination_tasks.values())
    await asyncio.gather(*tasks)

    assert restarted.state.origin == "https://other.example"
    assert len(transport.ack_envelopes) == ack_count
    assert transport.ack_envelopes[0] == saved_ack
    commands._attach.assert_not_awaited()
    assert enrollment_client._destination_recovery_journal(restarted).get(
        "0123456789abcdef0123456789abcdef"
    )["status"] == "ack_ready"


@pytest.mark.asyncio
async def test_device_enrollment_rejects_expired_signed_challenge(tmp_path, monkeypatch):
    result, commands, destination, transport, owner_directory = await _run_enrollment(
        tmp_path, monkeypatch, expired_challenge=True
    )

    assert result == {"error": "invalid_response"}
    assert not any(path.endswith("/proof") for path, _fields in transport.requests)
    assert destination.state.origin == ""
    assert destination.state.inviter == ""
    assert destination.state.approvals == []
    commands._attach.assert_not_awaited()
    assert owner_directory.members() == ()


@pytest.mark.asyncio
async def test_device_enrollment_rejects_invalid_owner_signature_before_state_change(tmp_path, monkeypatch):
    result, commands, destination, _transport, owner_directory = await _run_enrollment(
        tmp_path, monkeypatch, tamper_challenge=True
    )

    assert result == {"error": "invalid_response"}
    assert destination.state.origin == ""
    assert destination.state.inviter == ""
    assert destination.state.approvals == []
    commands._attach.assert_not_awaited()
    assert owner_directory.members() == ()


@pytest.mark.asyncio
async def test_device_enrollment_trusts_code_authenticated_issuer_not_discovery_publisher(tmp_path, monkeypatch):
    result, commands, destination, _transport, owner_directory = await _run_enrollment(
        tmp_path, monkeypatch, publisher_key=SigningKey.generate().verify_key.encode()
    )

    assert result == {"status": "approved"}
    assert destination.state.approvals == [destination.state.inviter]
    commands._attach.assert_awaited_once()
    assert owner_directory.members()


@pytest.mark.asyncio
async def test_device_enrollment_rejects_challenge_signed_for_another_owner_key(tmp_path, monkeypatch):
    result, commands, destination, _transport, owner_directory = await _run_enrollment(
        tmp_path, monkeypatch, claim_other_owner=True
    )

    assert result == {"error": "invalid_response"}
    assert destination.state.origin == ""
    assert destination.state.approvals == []
    commands._attach.assert_not_awaited()
    assert owner_directory.members() == ()


@pytest.mark.asyncio
async def test_device_enrollment_rejects_owner_scope_for_another_workspace(tmp_path, monkeypatch):
    result, commands, destination, transport, owner_directory = await _run_enrollment(
        tmp_path, monkeypatch, wrong_challenge_audience=True
    )

    assert result == {"error": "invalid_response"}
    assert destination.state.origin == ""
    assert destination.state.inviter == ""
    assert destination.state.approvals == []
    commands._attach.assert_not_awaited()
    assert owner_directory.members() == ()
    assert len(transport.requests) == 2
    assert transport.requests[0][0].endswith("/request")
    assert transport.requests[1][0].endswith("/reply/poll")


@pytest.mark.asyncio
async def test_issuer_offer_binds_one_human_action_and_revokes_it_on_close(tmp_path, monkeypatch):
    origin = "https://kollabor.ai"
    principal = "did:key:z6Mkg8c7ExamplePrincipal"
    owner_signing_key = SigningKey.generate()
    identity_manager = SimpleNamespace(
        get_or_create_keypair=lambda _designation: (
            owner_signing_key.encode().hex(),
            owner_signing_key.verify_key.encode().hex(),
        )
    )
    relay = RelayClient(
        tmp_path / "owner-workspace",
        state_dir=tmp_path / "owner-network",
        label="owner",
    )
    relay.state.origin = origin
    relay._store.save()
    relay._state = "online"
    relay._session_id = "1" * 32
    # The beacon's publisher is another identity: any trusted agent issues
    # codes for its own network (agent-device-pairing.md).
    discovery = SimpleNamespace(
        origin=origin,
        publisher_principal_id=principal,
        manifest={
            "coordinator": {
                "designation": "discovery",
                "public_key": SigningKey.generate().verify_key.encode().hex(),
            }
        },
    )

    class FakeTransport:
        request = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post_signed_retry(self, _key, path, offer_id, fields, **_kwargs):
            assert path == "/relay/v1/enrollment/offers"
            self.request = (offer_id, dict(fields))
            return {
                "status": "offered",
                "offer_id": offer_id,
                "expires_at": fields["expires_at"],
            }

    transport = FakeTransport()
    monkeypatch.setattr(
        enrollment_client,
        "EnrollmentHTTPClient",
        lambda *_args, **_kwargs: transport,
    )
    commands = SimpleNamespace(
        client=relay,
        _discover=AsyncMock(return_value=(discovery, "", (), False)),
        _relay_url=lambda _result: "wss://kollabor.ai/relay/v1/ws",
    )
    identity = SimpleNamespace(
        is_coordinator=True,
        identity="owner-agent",
        agent_id="owner-agent",
        profile="provider/openai:test",
    )
    provider_secret = "a" * 8192
    provider_profile = _fake_openai_profile(provider_secret)
    profile_manager = SimpleNamespace(
        get_active_profile=lambda: provider_profile,
        get_profile=lambda name: provider_profile if name == provider_profile.name else None,
    )
    bridge = SimpleNamespace(
        _turn=SimpleNamespace(get=lambda: None),
        commands=commands,
        identity=identity,
        plugin=SimpleNamespace(
            _dns_identity=identity_manager,
            event_bus=SimpleNamespace(get_service=lambda _name: profile_manager),
        ),
        owner=SimpleNamespace(state_dir=tmp_path),
        _closed=False,
    )
    issuer = EnrollmentIssuer(bridge)

    result = await issuer.create_offer("kollabor.ai")

    assert result["status"] == "offered"
    assert result["offer_id"] == transport.request[0]
    secret = enrollment_client.parse_short_enrollment_code(result["code"])
    parsed = enrollment_client.EnrollmentCode(result["offer_id"], secret)
    verifier = derive_enrollment_code_verifier(parsed)
    try:
        assert transport.request[1]["code_verifier_hash"] == enrollment_verifier_hash(result["offer_id"], verifier)
        assert transport.request[1]["lookup_hash"] == enrollment_client.enrollment_lookup_hash(
            parsed.for_lookup_tag(discovery.origin)
        )
    finally:
        parsed.wipe()
        verifier.wipe()

    state = json.loads((tmp_path / "enrollment-delegations.json").read_text())
    assert len(state["delegations"]) == 1
    record = next(iter(state["delegations"].values()))
    assert record["max_new_devices"] == 1
    assert record["credential_categories"] == [
        "conversation:send",
        "provider:openai:api_key",
    ]
    assert record["authorized_agent_id"] == "owner-agent"
    assert record["authorized_session_id"] == relay._session_id
    assert record["issuer"] == enrollment_client.public_key_id(owner_signing_key.verify_key.encode())
    assert record["configuration_profile"] == enrollment_client._profile_reference(provider_profile.name)
    assert "a" * 128 not in json.dumps(state)
    assert all("/" not in network_id for network_id in record["network_ids"])
    assert any(network_id.startswith("origin:") for network_id in record["network_ids"])
    assert record["issuer"] in record["network_ids"]
    assert principal not in record["network_ids"]

    offer = issuer._offers[result["offer_id"]]
    # This test simulates the post-acceptance worker read of the provider key.
    profile_preferences, credentials = await issuer._accepted_profile_payload(offer)
    recipient_key = SigningKey.generate()
    destination_profile_name = offer.provisioning_plan.destination_profile_name
    scope = ProvisioningScope(
        enrollment_id="f" * 32,
        network_ids=offer.network_ids,
        audience="b" * 32,
        profile_name=destination_profile_name,
        allowed_credential_categories=frozenset({offer.provisioning_plan.credential_category}),
    )
    bundle = seal_provisioning_bundle(
        ProvisioningPayload(
            profile=profile_preferences,
            networks=tuple(NetworkPreferences(network_id, "kollabor.ai") for network_id in offer.network_ids),
            credentials=credentials,
        ),
        scope=scope,
        issuer_key=owner_signing_key,
        recipient_public_key=recipient_key.verify_key,
        revision=1,
        expires_at=int(time.time()) + 60,
    )
    decision = enrollment_client.sign_enrollment_payload(
        owner_signing_key,
        {
            "v": 1,
            "offer_id": result["offer_id"],
            "round_id": "f" * 32,
            "phase": "decision",
            "destination_key": recipient_key.verify_key.encode().hex(),
            "owner_key": owner_signing_key.verify_key.encode().hex(),
            "issuer_relay_key": relay.public_key,
            "origin": origin,
            "status": "approved",
            "credential": "c" * 1024,
            "invite": "kollab-invite-" + "i" * 256,
            "provisioning_bundle": base64.urlsafe_b64encode(bundle).rstrip(b"=").decode("ascii"),
        },
    )
    encrypted_decision = enrollment_client.encrypt_enrollment_envelope(offer.envelope_key, decision)
    assert enrollment_client.decrypt_enrollment_envelope(offer.envelope_key, encrypted_decision) == decision

    relay.state.origin = "https://kollabor.ai:8443"
    relay._store.save()
    custom_port_discovery = SimpleNamespace(
        origin=relay.state.origin,
        publisher_principal_id=principal,
        manifest=discovery.manifest,
    )
    commands._discover.return_value = (custom_port_discovery, "", (), False)
    with pytest.raises(EnrollmentProtocolError, match="unavailable"):
        await issuer.create_offer("kollabor.ai")
    state = json.loads((tmp_path / "enrollment-delegations.json").read_text())
    assert len(state["delegations"]) == 1

    await issuer.close()

    state = json.loads((tmp_path / "enrollment-delegations.json").read_text())
    assert next(iter(state["delegations"].values()))["revoked"] is True


@pytest.mark.parametrize(
    (
        "decision_name",
        "expected_status",
        "tamper_ack",
        "wrong_ack_digest",
        "crash_before_peer_approval",
        "revoke_after_receipt_before_peer_approval",
    ),
    (
        ("accept", "accepted", False, False, False, False),
        ("accept", "accepted", True, False, False, False),
        ("accept", "accepted", False, True, False, False),
        ("accept", "accepted", False, False, True, False),
        ("accept", "accepted", False, False, True, True),
        ("reject", "rejected", False, False, False, False),
    ),
)
@pytest.mark.asyncio
async def test_issuer_requires_explicit_decision_after_proof(
    tmp_path,
    monkeypatch,
    decision_name,
    expected_status,
    tamper_ack,
    wrong_ack_digest,
    crash_before_peer_approval,
    revoke_after_receipt_before_peer_approval,
):
    origin = "https://kollabor.ai"
    offer_id = "0123456789abcdef0123456789abcdef"
    round_id = "f" * 32
    human_action_id = "human-action-123"
    session_id = "a" * 32
    owner_signing_key = SigningKey.generate()
    # The issuer principal is the issuing agent's own key, not the publisher.
    principal = enrollment_client.public_key_id(owner_signing_key.verify_key.encode())
    code = generate_enrollment_code(offer_id)
    envelope_key = derive_enrollment_envelope_key(code)
    destination_key = SigningKey.generate()
    destination_public_key = destination_key.verify_key.encode().hex()

    relay = RelayClient(
        tmp_path / "issuer-workspace",
        state_dir=tmp_path / "issuer-network",
        label="issuer",
    )
    relay.state.origin = origin
    relay._store.save()
    relay._state = "online"
    relay._session_id = session_id
    owner_directory = PrivateDirectory(
        tmp_path / "issuer-network" / "private-directory.json",
        owner_public_key=owner_signing_key.verify_key.encode(),
        workspace_id=relay.state.workspace_id,
    )
    origin_id = "origin:" + hashlib.sha256(b"kollab-relay-network-origin-v1\0" + origin.encode("ascii")).hexdigest()
    room_id = "room:" + hashlib.sha256(b"kollab-relay-network-id-v1\0" + bytes.fromhex(relay.state.room)).hexdigest()
    network_ids = tuple(sorted({origin_id, principal, room_id}))
    provider_profile = _fake_openai_profile()
    profile_manager = SimpleNamespace(
        get_active_profile=lambda: provider_profile,
        get_profile=lambda name: provider_profile if name == provider_profile.name else None,
    )
    identity_manager = SimpleNamespace(
        get_or_create_keypair=lambda _designation: (
            owner_signing_key.encode().hex(),
            owner_signing_key.verify_key.encode().hex(),
        )
    )
    bridge = SimpleNamespace(
        commands=SimpleNamespace(client=relay),
        owner=SimpleNamespace(state_dir=tmp_path),
        identity=SimpleNamespace(
            agent_id="owner-agent", identity="owner-agent", is_coordinator=True
        ),
        plugin=SimpleNamespace(
            event_bus=SimpleNamespace(get_service=lambda _name: profile_manager),
            _dns_identity=identity_manager,
        ),
        _closed=False,
    )
    issuer = EnrollmentIssuer(bridge)
    provisioning_plan = await issuer._make_provisioning_plan(offer_id)
    assert provisioning_plan is not None
    profile_reference = enrollment_client._profile_reference(provisioning_plan.source_profile_name)
    credential_categories = (
        "conversation:send",
        provisioning_plan.credential_category,
    )
    delegation_store = EnrollmentDelegationStore(tmp_path / "enrollment-delegations.json")
    delegation_store.create(
        human_action_id=human_action_id,
        authorized_agent_id="owner-agent",
        authorized_session_id=session_id,
        issuer=principal,
        network_ids=network_ids,
        configuration_profile=profile_reference,
        credential_categories=credential_categories,
        max_new_devices=1,
        expires_at=int(time.time()) + 300,
    )
    discovery = SimpleNamespace(
        requested_authority="kollabor.ai",
        origin=origin,
        publisher_principal_id=principal,
        manifest={
            "coordinator": {
                "designation": "owner-agent",
                "public_key": owner_signing_key.verify_key.encode().hex(),
            }
        },
    )
    offer = _ActiveEnrollmentOffer(
        offer_id=offer_id,
        expires_at=int(time.time()) + 300,
        human_action_id=human_action_id,
        session_id=session_id,
        issuer_key=relay.public_key,
        room_capability=relay.state.room,
        origin=origin,
        issuer_principal_id=principal,
        network_ids=network_ids,
        profile=profile_reference,
        envelope_key=envelope_key,
        owner_signing_key=owner_signing_key,
        discovery=discovery,
        ca="",
        private_cidrs=(),
        provisioning_plan=provisioning_plan,
        credential_categories=credential_categories,
    )
    request_envelope = encrypt_enrollment_envelope(
        envelope_key,
        {
            "v": 1,
            "offer_id": offer_id,
            "round_id": round_id,
            "phase": "request",
            "destination_key": destination_public_key,
            "workspace_id": "b" * 32,
        },
    )

    class FakeTransport:
        phase = "request"
        proof_envelope = ""
        decision = None
        decision_envelope = None
        ack_frame = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post_signed_retry(self, *args, **kwargs):
            return await self.post_signed(*args, **kwargs)

        async def post_signed(self, _key, path, _offer_id, fields, **_kwargs):
            if path == f"/relay/v1/enrollment/offers/{offer_id}/poll":
                if self.phase == "request":
                    return {
                        "status": "claimed",
                        "phase": "request",
                        "round_id": round_id,
                        "destination_key": destination_public_key,
                        "envelope": request_envelope,
                        "device_name": "",
                    }
                return {
                    "status": "claimed",
                    "phase": "proof",
                    "round_id": round_id,
                    "destination_key": destination_public_key,
                    "envelope": self.proof_envelope,
                    "device_name": "",
                }
            if path.endswith("/challenge"):
                challenge = decrypt_enrollment_envelope(envelope_key, fields["envelope"])
                challenge = enrollment_client.verify_enrollment_payload(
                    owner_signing_key.verify_key.encode(), challenge
                )
                assert challenge["provisioning_scope"] == {
                    "enrollment_id": round_id,
                    "network_ids": list(network_ids),
                    "audience": "b" * 32,
                    "profile_name": provisioning_plan.destination_profile_name,
                    "allowed_credential_categories": [provisioning_plan.credential_category],
                    "allowed_agent_names": [],
                    "allowed_skill_names": [],
                }
                proof = prove_pairing(
                    challenge["pairing_challenge"],
                    destination_key,
                    owner_public_key=owner_signing_key.verify_key.encode(),
                )
                self.proof_envelope = encrypt_enrollment_envelope(
                    envelope_key,
                    {
                        "v": 1,
                        "offer_id": offer_id,
                        "round_id": round_id,
                        "phase": "proof",
                        "destination_key": destination_public_key,
                        "proof_token": proof.token,
                    },
                )
                self.phase = "proof"
                return {"status": "stored", "receipt": round_id}
            if path.endswith("/decision"):
                self.decision = decrypt_enrollment_envelope(envelope_key, fields["envelope"])
                self.decision_envelope = fields["envelope"]
                enrollment_client.verify_enrollment_payload(owner_signing_key.verify_key.encode(), self.decision)
                if self.decision["status"] == "approved":
                    bundle = base64.urlsafe_b64decode(
                        self.decision["provisioning_bundle"] + "=" * (-len(self.decision["provisioning_bundle"]) % 4)
                    )
                    scope = ProvisioningScope(
                        enrollment_id=round_id,
                        network_ids=network_ids,
                        audience="b" * 32,
                        profile_name=provisioning_plan.destination_profile_name,
                        allowed_credential_categories=frozenset({provisioning_plan.credential_category}),
                    )
                    destination_directory = tmp_path / "destination-private"
                    destination_directory.mkdir(mode=0o700, exist_ok=True)
                    receipt = install_provisioning_bundle(
                        bundle,
                        recipient_key=destination_key,
                        expectation=ProvisioningExpectation(
                            issuer_public_key=owner_signing_key.verify_key.encode(),
                            scope=scope,
                        ),
                        store=FilesystemProvisioningStore(
                            ProvisionedStateFile(destination_directory / "provisioned-state.json")
                        ),
                    )
                    ack_payload = {
                        "v": 1,
                        "offer_id": offer_id,
                        "round_id": round_id,
                        "phase": "installation_ack",
                        "destination_key": destination_public_key,
                        "workspace_id": "b" * 32,
                        "status": receipt.status,
                        "revision": receipt.revision,
                        "digest": "0" * 64 if wrong_ack_digest else receipt.digest,
                    }
                    signed_ack_payload = enrollment_client.sign_installation_receipt(destination_key, ack_payload)
                    ack_envelope = encrypt_enrollment_envelope(envelope_key, signed_ack_payload)
                    ack_path = f"/relay/v1/enrollment/offers/{offer_id}/ack"
                    self.ack_frame = signed_enrollment_frame(
                        destination_key,
                        origin,
                        ack_path,
                        offer_id,
                        {
                            "destination_key": destination_public_key,
                            "round_id": round_id,
                            "envelope": ack_envelope,
                        },
                    )
                if tamper_ack:
                    self.ack_frame["signature"] = "0" * 128
                return {"status": "stored", "receipt": round_id}
            if path.endswith("/ack/poll"):
                if self.ack_frame is None:
                    return {"status": "pending"}
                return {"status": "ready", "frame": self.ack_frame}
            raise AssertionError(f"unexpected endpoint: {path}")

    transport = FakeTransport()

    real_sleep = asyncio.sleep

    async def yield_sleep(_delay):
        await real_sleep(0)

    monkeypatch.setattr(
        enrollment_client,
        "EnrollmentHTTPClient",
        lambda *_args, **_kwargs: transport,
    )
    monkeypatch.setattr(enrollment_client.asyncio, "sleep", yield_sleep)
    if crash_before_peer_approval:

        def fail_peer_approval(_destination_key):
            raise RuntimeError("simulated process interruption after receipt")

        monkeypatch.setattr(relay, "approve", fail_peer_approval)
    task = asyncio.create_task(issuer._serve_offer(offer))
    # Real wall-clock deadline, not a fixed iteration count: a fixed count of
    # 1ms sleeps assumes the event loop gets scheduled promptly, which is not
    # true under full-suite CPU contention and was the source of a flaky
    # "verified proof did not become a pending request" failure (issue #121).
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        requests = issuer.pending_requests()
        if requests:
            break
        await real_sleep(0.001)
    else:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        raise AssertionError("verified proof did not become a pending request")

    pending_request = requests[0]
    assert pending_request.enrollment_id == round_id
    assert pending_request.device_key_fingerprint == enrollment_client._device_key_fingerprint(destination_public_key)
    assert pending_request.decision_available is True
    assert pending_request.credential_categories == credential_categories
    assert pending_request.profile_summary == provisioning_plan.display
    assert "proof_token" not in repr(pending_request)
    assert transport.decision is None
    assert relay.state.approvals == []
    assert owner_directory.members() == ()
    persisted_pending = EnrollmentDelegationStore(tmp_path / "enrollment-delegations.json").get_enrollment_request(
        round_id, agent_id="owner-agent", session_id=session_id
    )
    assert persisted_pending.status == "pending"
    assert delegation_store.get(human_action_id).consumed_new_devices == 0

    restarted_issuer = EnrollmentIssuer(bridge)
    restarted_pending = restarted_issuer.pending_requests()
    assert len(restarted_pending) == 1
    assert restarted_pending[0].decision_available is False
    with pytest.raises(EnrollmentProtocolError, match="unavailable"):
        await restarted_issuer.decide(round_id, decision="accept")

    bridge.identity.agent_id = "wrong-agent"
    with pytest.raises(EnrollmentProtocolError, match="unauthorized"):
        await issuer.decide(round_id, decision="accept")
    bridge.identity.agent_id = "owner-agent"
    relay._session_id = "different-session"
    # A reconnect under a new relay session still cannot decide, but says why.
    with pytest.raises(EnrollmentProtocolError, match="session_changed"):
        await issuer.decide(round_id, decision="accept")
    relay._session_id = session_id

    await real_sleep(0.01)
    assert transport.decision is None
    assert owner_directory.members() == ()
    assert relay.state.approvals == []

    result = await issuer.decide(round_id, decision=decision_name)
    assert result == {"status": expected_status, "receipt_id": round_id}
    await task

    assert transport.decision["status"] == ("approved" if decision_name == "accept" else "rejected")
    assert transport.decision["issuer_relay_key"] == relay.public_key
    assert transport.decision["destination_key"] == destination_public_key
    if decision_name == "accept":
        persisted_state_text = (tmp_path / "enrollment-delegations.json").read_text()
        persisted_request = json.loads(persisted_state_text)["enrollment_requests"][round_id]
        assert persisted_request["delivery_intent"]["expected_digest"]
        assert persisted_request["delivery_intent"]["owner_signature"]
        assert "owner-provider-secret" not in persisted_state_text
        assert transport.decision["credential"] not in persisted_state_text
        assert transport.decision["provisioning_bundle"] not in persisted_state_text
        invitation = parse_invite(transport.decision["invite"])
        assert invitation["origin"] == origin
        assert invitation["key"] == relay.public_key
        encoded_claims = transport.decision["credential"].split(".")[1]
        credential_claims = json.loads(base64.urlsafe_b64decode(encoded_claims + "=" * (-len(encoded_claims) % 4)))
        assert credential_claims["sub"] == public_key_id(bytes.fromhex(destination_public_key))
        assert credential_claims["scope"] == ["conversation:send"]
        if tamper_ack or wrong_ack_digest:
            # Human approval and the exact decision envelope stay durable so
            # a transient/lost ACK can be retried. The device is not admitted
            # to the relay until a valid installation receipt is reconciled.
            assert relay.state.approvals == []
            assert len(owner_directory.members()) == 1
            assert delegation_store.get(human_action_id).revoked is False
            state = json.loads((tmp_path / "enrollment-delegations.json").read_text())
            assert state["enrollment_requests"][round_id]["installation_receipt"] is None
            recovery = EnrollmentRecoveryJournal(
                tmp_path / "enrollment-recovery.json",
                derive_enrollment_recovery_key(relay._store.key.encode()),
            ).get(offer_id)
            assert recovery is not None
            assert recovery["status"] == "approved"
            assert recovery["issued_credential"] == transport.decision["credential"]
            assert recovery["decision_envelope"]

            restarted_relay = RelayClient(
                tmp_path / "issuer-workspace",
                state_dir=tmp_path / "issuer-network",
                label="issuer-restarted",
            )
            restarted_relay._state = "online"
            restarted_relay._session_id = "b" * 32
            restarted_bridge = SimpleNamespace(
                commands=SimpleNamespace(
                    client=restarted_relay,
                    _discover=AsyncMock(return_value=(discovery, "", (), False)),
                    _relay_url=lambda _result: "https://kollabor.ai/relay/v1",
                ),
                owner=bridge.owner,
                identity=bridge.identity,
                plugin=bridge.plugin,
                _closed=False,
            )
            restarted_issuer = EnrollmentIssuer(restarted_bridge)
            assert await restarted_issuer._restore_approved_offers() == 1
            await restarted_issuer._tasks[offer_id]
            assert transport.decision_envelope == recovery["decision_envelope"]

            changed_scope = dict(recovery)
            changed_scope["network_ids"] = ["origin:" + "0" * 64]
            recovery_journal = EnrollmentRecoveryJournal(
                tmp_path / "enrollment-recovery.json",
                derive_enrollment_recovery_key(restarted_relay._store.key.encode()),
            )
            recovery_journal.put(offer_id, changed_scope)
            assert await restarted_issuer._restore_approved_offers() == 0
            retry_record = recovery_journal.get(offer_id)
            assert retry_record["last_error_code"] == "conflict"
            assert retry_record["retry_attempts"] == 1
            recovery_status = restarted_issuer.destination_recovery_status()
            assert recovery_status["issuer_pending"] == 1
            assert recovery_status["last_error_code"] == "conflict"
            assert 0 < recovery_status["retry_in_seconds"] <= 300
            status_text = json.dumps(recovery_status)
            assert offer_id not in status_text
            assert transport.decision["credential"] not in status_text
            recovery_journal.put(offer_id, recovery)

            encoded_claims = transport.decision["credential"].split(".")[1]
            issued_claims = json.loads(
                base64.urlsafe_b64decode(
                    encoded_claims + "=" * (-len(encoded_claims) % 4)
                )
            )
            with monkeypatch.context() as context:
                context.setattr(
                    enrollment_client.time,
                    "time",
                    lambda: recovery["expires_at"] + 1,
                )
                assert await restarted_issuer._restore_approved_offers() == 0
            assert recovery_journal.get(offer_id) is None
            assert owner_directory.members() == ()
            directory_state = json.loads(
                (
                    tmp_path
                    / "issuer-network"
                    / "private-directory.json"
                ).read_text()
            )
            credential_revocations = [
                owner_directory._verify_owner_jws(token, "revocation")
                for token in directory_state["revocations"].values()
            ]
            assert len(credential_revocations) == 1
            assert credential_revocations[0]["target_type"] == "credential"
            assert credential_revocations[0]["target_id"] == issued_claims["jti"]

            # The revoked delegation is then retired idempotently; it cannot
            # add a second or broader revocation.
            recovery_journal.put(offer_id, recovery)
            delegation_store.revoke(human_action_id)
            assert await restarted_issuer._restore_approved_offers() == 0
            assert recovery_journal.get(offer_id) is None
            assert owner_directory.members() == ()
            directory_state = json.loads(
                (
                    tmp_path
                    / "issuer-network"
                    / "private-directory.json"
                ).read_text()
            )
            credential_revocations = [
                owner_directory._verify_owner_jws(token, "revocation")
                for token in directory_state["revocations"].values()
            ]
            assert len(credential_revocations) == 1
            assert credential_revocations[0]["target_type"] == "credential"
            assert credential_revocations[0]["target_id"] == issued_claims["jti"]
            await restarted_issuer.close()
        elif crash_before_peer_approval:
            assert relay.state.approvals == []
            assert len(owner_directory.members()) == 1
            assert delegation_store.get(human_action_id).revoked is False
            pending_receipt = delegation_store.get_enrollment_request(
                round_id,
                agent_id="owner-agent",
                session_id=session_id,
            )
            assert pending_receipt.installation_digest == (pending_receipt.expected_install_digest)
            assert pending_receipt.installation_signature
            assert pending_receipt.peer_approved is False

            restarted_relay = RelayClient(
                tmp_path / "issuer-workspace",
                state_dir=tmp_path / "issuer-network",
                label="issuer",
            )
            restarted_relay._state = "online"
            restarted_relay._session_id = "b" * 32
            restarted_bridge = SimpleNamespace(
                commands=SimpleNamespace(client=restarted_relay),
                owner=bridge.owner,
                identity=SimpleNamespace(
                    agent_id="owner-agent-new-runtime",
                    identity="owner-agent",
                    is_coordinator=True,
                ),
                plugin=bridge.plugin,
                _closed=False,
            )
            wrong_owner_key = SigningKey.generate()
            restarted_bridge.plugin._dns_identity = SimpleNamespace(
                get_or_create_keypair=lambda _designation: (
                    wrong_owner_key.encode().hex(),
                    wrong_owner_key.verify_key.encode().hex(),
                )
            )
            wrong_owner_issuer = EnrollmentIssuer(restarted_bridge)
            assert wrong_owner_issuer.pending_requests() == ()
            assert restarted_relay.state.approvals == []

            restarted_bridge.plugin._dns_identity = identity_manager
            context_mismatches = (
                ("public_key", restarted_relay, "public_key", "9" * 64),
                ("origin", restarted_relay.state, "origin", "https://other.example"),
                ("room", restarted_relay.state, "room", "8" * 64),
                ("workspace", restarted_relay.state, "workspace_id", "7" * 32),
            )
            for _name, target, attribute, mismatch in context_mismatches:
                original = getattr(target, attribute)
                setattr(target, attribute, mismatch)
                try:
                    mismatched_issuer = EnrollmentIssuer(restarted_bridge)
                    assert mismatched_issuer.pending_requests() == ()
                    assert restarted_relay.state.approvals == []
                    assert (
                        delegation_store.get_enrollment_request(
                            round_id,
                            agent_id="owner-agent",
                            session_id=session_id,
                        ).peer_approved
                        is False
                    )
                finally:
                    setattr(target, attribute, original)

            restarted_issuer = EnrollmentIssuer(restarted_bridge)
            if revoke_after_receipt_before_peer_approval:
                # A durable device receipt without completed peer approval is
                # ambiguous after revocation, so keep both credential and row.
                restarted_bridge.identity.agent_id = "owner-agent"
                recovery_journal = restarted_issuer._recovery_journal(
                    restarted_relay
                )
                due_record = recovery_journal.get(offer_id)
                due_record.update(
                    retry_attempts=0, retry_after=0, last_error_code=None
                )
                recovery_journal.put(offer_id, due_record)
                delegation_store.revoke(human_action_id)
                assert await restarted_issuer._restore_approved_offers() == 0
                preserved = recovery_journal.get(offer_id)
                assert preserved is not None
                assert preserved["last_error_code"] == "conflict"
                assert 0 < preserved["retry_after"] - int(time.time()) <= 300
                assert owner_directory.members()
                assert restarted_relay.state.approvals == []
                status_text = json.dumps(
                    restarted_issuer.destination_recovery_status()
                )
                assert offer_id not in status_text
                assert transport.decision["credential"] not in status_text
                directory_state = json.loads(
                    (
                        tmp_path
                        / "issuer-network"
                        / "private-directory.json"
                    ).read_text()
                )
                assert directory_state["revocations"] == {}
            else:
                assert restarted_issuer.pending_requests() == ()
                assert restarted_relay.state.approvals == [destination_public_key]
                reconciled = delegation_store.get_enrollment_request(
                    round_id,
                    agent_id="owner-agent",
                    session_id=session_id,
                )
                assert reconciled.peer_approved is True

                # A stale recovery row for a completed peer is retired without
                # revoking the credential or the relay approval.
                restarted_bridge.identity.agent_id = "owner-agent"
                recovery_journal = restarted_issuer._recovery_journal(
                    restarted_relay
                )
                assert recovery_journal.get(offer_id) is not None
                encoded_claims = transport.decision["credential"].split(".")[1]
                issued_claims = json.loads(
                    base64.urlsafe_b64decode(
                        encoded_claims + "=" * (-len(encoded_claims) % 4)
                    )
                )
                due_record = recovery_journal.get(offer_id)
                due_record.update(
                    retry_attempts=0, retry_after=0, last_error_code=None
                )
                recovery_journal.put(offer_id, due_record)
                delegation_store.revoke(human_action_id)
                assert await restarted_issuer._restore_approved_offers() == 0
                assert recovery_journal.get(offer_id) is None
                assert owner_directory.members()
                assert restarted_relay.state.approvals == [destination_public_key]
                directory_state = json.loads(
                    (
                        tmp_path
                        / "issuer-network"
                        / "private-directory.json"
                    ).read_text()
                )
                revocations = [
                    owner_directory._verify_owner_jws(token, "revocation")
                    for token in directory_state["revocations"].values()
                ]
                assert not any(
                    item["target_type"] == "credential"
                    and item["target_id"] == issued_claims["jti"]
                    for item in revocations
                )
        else:
            assert relay.state.approvals == [destination_public_key]
            members = PrivateDirectory(
                tmp_path / "issuer-network" / "private-directory.json",
                owner_public_key=owner_signing_key.verify_key.encode(),
                workspace_id=relay.state.workspace_id,
            ).members()
            assert len(members) == 1
            assert delegation_store.get(human_action_id).remaining_new_devices == 0
            assert delegation_store.get(human_action_id).revoked is False
        provisioned = ProvisionedStateFile(tmp_path / "destination-private" / "provisioned-state.json").read()[
            "installs"
        ][round_id]
        assert provisioned["profile"]["name"] == (provisioning_plan.destination_profile_name)
        assert provisioned["credentials"] == [
            {
                "category": provisioning_plan.credential_category,
                "profile_name": provisioning_plan.destination_profile_name,
                "secret": "owner-provider-secret",
            }
        ]
    else:
        assert "credential" not in transport.decision
        assert "invite" not in transport.decision
        assert relay.state.approvals == []
        assert owner_directory.members() == ()
        assert delegation_store.get(human_action_id).consumed_new_devices == 0


@pytest.mark.asyncio
async def test_lookup_enrollment_offer_resolves_via_the_lookup_route(monkeypatch):
    client = SimpleNamespace(
        _store=SimpleNamespace(key=SigningKey.generate()),
        public_key="a" * 64,
    )
    discovery = SimpleNamespace(origin="https://kollabor.ai")
    secret = bytearray(b"ABCD1234")
    offer_id = "1" * 32
    seen = {}

    class FakeTransport:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, path, frame, **_kwargs):
            seen["path"] = path
            seen["frame"] = frame
            return {"offer_id": offer_id}

    monkeypatch.setattr(
        enrollment_client, "EnrollmentHTTPClient", lambda *_a, **_k: FakeTransport()
    )

    result = await enrollment_client._lookup_enrollment_offer(client, discovery, "", (), secret)

    assert result == offer_id
    assert seen["path"] == enrollment_client.ENROLLMENT_LOOKUP_PATH
    assert seen["frame"]["destination_key"] == client.public_key
    assert seen["frame"]["lookup"] == enrollment_client.derive_enrollment_lookup_tag(
        secret, discovery.origin
    )


@pytest.mark.asyncio
async def test_lookup_enrollment_offer_returns_none_on_a_miss_or_bad_response(monkeypatch):
    client = SimpleNamespace(
        _store=SimpleNamespace(key=SigningKey.generate()),
        public_key="a" * 64,
    )
    discovery = SimpleNamespace(origin="https://kollabor.ai")
    secret = bytearray(b"ABCD1234")

    class MissTransport:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _path, _frame, **_kwargs):
            raise EnrollmentProtocolError("unavailable")

    monkeypatch.setattr(
        enrollment_client, "EnrollmentHTTPClient", lambda *_a, **_k: MissTransport()
    )
    assert await enrollment_client._lookup_enrollment_offer(
        client, discovery, "", (), secret
    ) is None

    class MalformedTransport:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _path, _frame, **_kwargs):
            return {"offer_id": "not-hex"}

    monkeypatch.setattr(
        enrollment_client, "EnrollmentHTTPClient", lambda *_a, **_k: MalformedTransport()
    )
    assert await enrollment_client._lookup_enrollment_offer(
        client, discovery, "", (), secret
    ) is None


@pytest.mark.asyncio
async def test_enroll_device_short_code_looks_up_then_submits_the_request(tmp_path, monkeypatch):
    """A short code has no offer id embedded: enroll_device must resolve one
    through the lookup route before it can journal or submit anything."""
    origin = "https://kollabor.ai"
    offer_id = "2" * 32
    code = generate_enrollment_code(offer_id)
    code_text = code.for_private_display()
    verifier = derive_enrollment_code_verifier(code)
    expected_verifier = verifier.for_protocol()

    destination = RelayClient(
        tmp_path / "destination-workspace",
        state_dir=tmp_path / "destination-network",
        label="destination",
    )
    discovery = SimpleNamespace(
        origin=origin,
        manifest={
            "coordinator": {"public_key": "0" * 64},
            "endpoints": {"control": origin + "/relay/v1"},
        },
    )
    commands = SimpleNamespace(
        client=destination,
        _discover=AsyncMock(return_value=(discovery, "", (), False)),
        _relay_url=lambda _discovery: "wss://kollabor.ai/relay/v1/ws",
    )

    class LookupOnlyTransport:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, path, _frame, **_kwargs):
            assert path == enrollment_client.ENROLLMENT_LOOKUP_PATH
            return {"offer_id": offer_id}

    monkeypatch.setattr(
        enrollment_client, "EnrollmentHTTPClient", lambda *_a, **_k: LookupOnlyTransport()
    )
    drive = AsyncMock(return_value={"status": "stubbed"})
    monkeypatch.setattr(enrollment_client, "_drive_destination_enrollment", drive)

    try:
        result = await enroll_device(commands, "kollabor.ai", code_text)
    finally:
        code.wipe()
        verifier.wipe()

    assert result == {"status": "stubbed"}
    drive.assert_awaited_once()
    _commands, journaled_record, _journal, _envelope_key = drive.await_args.args
    assert journaled_record["offer_id"] == offer_id
    assert journaled_record["code_verifier"] == expected_verifier
    assert journaled_record["device_name"]
