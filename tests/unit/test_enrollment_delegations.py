"""Persistence, scope enforcement, and concurrency tests for enrollment grants."""

import hashlib
import json
import stat
from concurrent.futures import ThreadPoolExecutor

import pytest
from nacl.signing import SigningKey

from plugins.hub import enrollment_delegations as delegation_module
from plugins.hub.enrollment_client import sign_enrollment_payload
from plugins.hub.enrollment_delegations import (
    DelegationAuthorizationError,
    DelegationCapacityError,
    DelegationPersistenceError,
    EnrollmentDelegationStore,
    sign_installation_receipt,
)

NOW = 1_800_000_000
ACTION_ID = "human-action-01"
AGENT_ID = "agent:ed25519:" + "a" * 64
SESSION_ID = "session-01"
ISSUER = "ed25519:" + "b" * 64
NETWORKS = ("network-alpha", "network-beta")
PROFILE = "standard"
CATEGORIES = ("mcp:linear", "provider:openai")
DEVICE_FINGERPRINT = hashlib.sha256(b"test-device-key").hexdigest()
ISSUER_WORKSPACE_ID = "f" * 32


def _store(tmp_path, name="delegations.json"):
    private_dir = tmp_path / "private"
    private_dir.mkdir(mode=0o700, exist_ok=True)
    return EnrollmentDelegationStore(private_dir / name)


def _create(store, *, action_id=ACTION_ID, max_devices=2, expires_at=NOW + 600):
    return store.create(
        human_action_id=action_id,
        authorized_agent_id=AGENT_ID,
        authorized_session_id=SESSION_ID,
        issuer=ISSUER,
        network_ids=NETWORKS,
        configuration_profile=PROFILE,
        credential_categories=CATEGORIES,
        max_new_devices=max_devices,
        expires_at=expires_at,
        now=NOW,
    )


def _request(**overrides):
    request = {
        "agent_id": AGENT_ID,
        "session_id": SESSION_ID,
        "issuer": ISSUER,
        "network_ids": NETWORKS,
        "configuration_profile": PROFILE,
        "credential_categories": CATEGORIES,
        "now": NOW,
    }
    request.update(overrides)
    return request


def _consume(store, enrollment_id, **overrides):
    _record_pending(store, enrollment_id)
    return store.consume(
        ACTION_ID,
        enrollment_id=enrollment_id,
        device_key_fingerprint=DEVICE_FINGERPRINT,
        **_request(**overrides),
    )


def _record_pending(store, enrollment_id, *, device_fingerprint=DEVICE_FINGERPRINT):
    store.record_pending(
        ACTION_ID,
        enrollment_id=enrollment_id,
        device_key_fingerprint=device_fingerprint,
        agent_id=AGENT_ID,
        session_id=SESSION_ID,
        issuer=ISSUER,
        network_ids=NETWORKS,
        configuration_profile=PROFILE,
        credential_categories=CATEGORIES,
        expires_at=NOW + 300,
        now=NOW,
    )


def _record_install_intent(store, enrollment_id, workspace_id, device_key, owner_key, digest):
    offer_id = "c" * 32
    destination_public_key = device_key.verify_key.encode().hex()
    device_fingerprint = hashlib.sha256(
        b"kollab-relay-enrollment-device-fingerprint-v1\0" + device_key.verify_key.encode()
    ).hexdigest()
    request = store.get_enrollment_request(enrollment_id, agent_id=AGENT_ID, session_id=SESSION_ID, now=NOW)
    intent = sign_enrollment_payload(
        owner_key,
        {
            "v": 1,
            "offer_id": offer_id,
            "round_id": enrollment_id,
            "phase": "installation_intent",
            "destination_key": destination_public_key,
            "workspace_id": workspace_id,
            "expected_digest": digest,
            "issuer_relay_key": "1" * 64,
            "issuer_origin": "https://kollabor.ai",
            "room_fingerprint": "2" * 64,
            "issuer_workspace_id": ISSUER_WORKSPACE_ID,
            "owner_public_key": owner_key.verify_key.encode().hex(),
            "scope_fingerprint": request.scope_fingerprint,
        },
    )
    store.record_delivery_intent(
        enrollment_id,
        agent_id=AGENT_ID,
        session_id=SESSION_ID,
        offer_id=offer_id,
        destination_public_key=destination_public_key,
        expected_digest=digest,
        issuer_relay_key="1" * 64,
        issuer_origin="https://kollabor.ai",
        room_fingerprint="2" * 64,
        issuer_workspace_id=ISSUER_WORKSPACE_ID,
        owner_public_key=owner_key.verify_key.encode().hex(),
        owner_signature=intent["owner_signature"],
        now=NOW,
    )
    return offer_id, destination_public_key, device_fingerprint


def test_delegation_is_private_durable_and_contains_only_authorization_state(tmp_path):
    store = _store(tmp_path)
    created = _create(store)
    path = tmp_path / "private" / "delegations.json"
    lock_path = path.with_name(path.name + ".lock")

    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700

    persisted = json.loads(path.read_text(encoding="utf-8"))
    row = persisted["delegations"][ACTION_ID]
    assert set(row) == {
        "human_action_id",
        "authorized_agent_id",
        "authorized_session_id",
        "issuer",
        "network_ids",
        "configuration_profile",
        "credential_categories",
        "max_new_devices",
        "expires_at",
        "revoked",
        "consumed_new_devices",
        "consumed_enrollments",
    }
    serialized = path.read_text(encoding="utf-8")
    assert not any(
        forbidden in serialized.lower()
        for forbidden in (
            "workspace_path",
            "private_key",
            "credential_value",
            "token",
            "code",
        )
    )

    restarted = EnrollmentDelegationStore(path)
    assert restarted.get(ACTION_ID) == created
    _record_pending(restarted, "enrollment-01")
    consumed = restarted.consume(
        ACTION_ID,
        enrollment_id="enrollment-01",
        device_key_fingerprint=DEVICE_FINGERPRINT,
        **_request(),
    )
    after_restart = EnrollmentDelegationStore(path).get(ACTION_ID)
    assert after_restart == consumed
    assert after_restart.consumed_new_devices == 1
    assert after_restart.remaining_new_devices == 1


def test_default_expiry_is_ten_minutes_and_lifetime_is_bounded(tmp_path):
    store = _store(tmp_path)
    record = store.create(
        human_action_id=ACTION_ID,
        authorized_agent_id=AGENT_ID,
        authorized_session_id=SESSION_ID,
        issuer=ISSUER,
        network_ids=NETWORKS,
        configuration_profile=PROFILE,
        credential_categories=CATEGORIES,
        max_new_devices=2,
        now=NOW,
    )
    assert record.expires_at == NOW + 600

    with pytest.raises(DelegationAuthorizationError):
        store.create(
            human_action_id="action-too-long",
            authorized_agent_id=AGENT_ID,
            authorized_session_id=SESSION_ID,
            issuer=ISSUER,
            network_ids=NETWORKS,
            configuration_profile=PROFILE,
            max_new_devices=1,
            expires_at=NOW + delegation_module.MAX_DELEGATION_TTL_SECONDS + 1,
            now=NOW,
        )


def test_wrong_agent_session_issuer_or_scope_is_rejected_without_consumption(tmp_path):
    store = _store(tmp_path)
    _create(store)
    wrong_requests = (
        {"agent_id": "another-agent"},
        {"session_id": "another-session"},
        {"issuer": "ed25519:" + "c" * 64},
        {"network_ids": ("network-unknown",)},
        {"configuration_profile": "unapproved-profile"},
        {"credential_categories": ("provider:aws",)},
    )

    for changes in wrong_requests:
        with pytest.raises(DelegationAuthorizationError):
            store.check_eligible(ACTION_ID, **_request(**changes))
        with pytest.raises(DelegationAuthorizationError):
            _consume(store, "enrollment-01", **changes)

    assert store.get(ACTION_ID).consumed_new_devices == 0


def test_changed_relay_session_is_reported_distinctly(tmp_path):
    store = _store(tmp_path)
    _create(store)
    _consume(store, "enrollment-01")

    with pytest.raises(delegation_module.DelegationSessionChangedError):
        store.get_enrollment_request("enrollment-01", agent_id=AGENT_ID, session_id="another-session")
    with pytest.raises(DelegationAuthorizationError) as other_agent:
        store.get_enrollment_request("enrollment-01", agent_id="another-agent", session_id=SESSION_ID)
    assert not isinstance(other_agent.value, delegation_module.DelegationSessionChangedError)


def test_requested_scope_can_be_narrower_but_never_wider(tmp_path):
    store = _store(tmp_path)
    _create(store)
    offer = store.check_eligible(
        ACTION_ID,
        **_request(
            network_ids=("network-alpha",),
            credential_categories=("provider:openai",),
        ),
    )
    assert offer.remaining_new_devices == 2
    assert store.get(ACTION_ID).consumed_new_devices == 0


def test_expiry_and_revocation_fail_closed_and_survive_restart(tmp_path):
    expired_store = _store(tmp_path, "expired.json")
    _create(expired_store, action_id="expired-action", expires_at=NOW + 5)
    expired_store.record_pending(
        "expired-action",
        enrollment_id="expired-request",
        device_key_fingerprint=DEVICE_FINGERPRINT,
        **{key: value for key, value in _request().items() if key != "now"},
        expires_at=NOW + 5,
        now=NOW,
    )
    with pytest.raises(DelegationAuthorizationError, match="expired"):
        expired_store.check_eligible("expired-action", **_request(now=NOW + 5))
    assert expired_store.pending_requests(agent_id=AGENT_ID, session_id=SESSION_ID, now=NOW + 5) == ()
    with pytest.raises(DelegationAuthorizationError, match="no longer active"):
        expired_store.get_enrollment_request(
            "expired-request",
            agent_id=AGENT_ID,
            session_id=SESSION_ID,
            now=NOW + 5,
        )

    revoked_store = _store(tmp_path, "revoked.json")
    _create(revoked_store, action_id="revoked-action")
    revoked_store.record_pending(
        "revoked-action",
        enrollment_id="revoked-request",
        device_key_fingerprint=DEVICE_FINGERPRINT,
        **{key: value for key, value in _request().items() if key != "now"},
        expires_at=NOW + 300,
        now=NOW,
    )
    revoked = revoked_store.revoke("revoked-action", now=NOW)
    assert revoked.revoked is True
    restarted = EnrollmentDelegationStore(tmp_path / "private" / "revoked.json")
    assert restarted.get("revoked-action").revoked is True
    assert restarted.pending_requests(agent_id=AGENT_ID, session_id=SESSION_ID, now=NOW) == ()
    with pytest.raises(DelegationAuthorizationError, match="no longer active"):
        restarted.get_enrollment_request("revoked-request", agent_id=AGENT_ID, session_id=SESSION_ID, now=NOW)
    with pytest.raises(DelegationAuthorizationError, match="revoked"):
        restarted.consume(
            "revoked-action",
            enrollment_id="revoked-request",
            device_key_fingerprint=DEVICE_FINGERPRINT,
            **_request(),
        )
    assert restarted.get("revoked-action").consumed_new_devices == 0


def test_consumption_is_bounded_and_idempotent_by_enrollment_and_scope(tmp_path):
    store = _store(tmp_path)
    _create(store, max_devices=2)

    offer = store.check_eligible(ACTION_ID, **_request())
    assert offer.consumed_new_devices == 0
    first = _consume(store, "enrollment-01")
    retry = _consume(store, "enrollment-01")
    assert first.consumed_new_devices == retry.consumed_new_devices == 1

    with pytest.raises(DelegationAuthorizationError, match="different scope"):
        _consume(store, "enrollment-01", network_ids=("network-alpha",))

    final = _consume(store, "enrollment-02")
    assert final.consumed_new_devices == 2
    assert final.remaining_new_devices == 0
    with pytest.raises(DelegationCapacityError):
        store.check_eligible(ACTION_ID, **_request())
    with pytest.raises(DelegationCapacityError):
        _consume(store, "enrollment-03")


def test_concurrent_distinct_approvals_cannot_exceed_allowance(tmp_path):
    store = _store(tmp_path)
    _create(store, max_devices=1)
    stores = [EnrollmentDelegationStore(tmp_path / "private" / "delegations.json") for _ in range(8)]

    def attempt(index):
        try:
            return _consume(stores[index], f"enrollment-{index:02d}").consumed_new_devices
        except DelegationCapacityError:
            return "full"

    with ThreadPoolExecutor(max_workers=len(stores)) as pool:
        results = list(pool.map(attempt, range(len(stores))))

    assert results.count(1) == 1
    assert results.count("full") == len(stores) - 1
    assert EnrollmentDelegationStore(tmp_path / "private" / "delegations.json").get(ACTION_ID).consumed_new_devices == 1


def test_concurrent_retry_for_same_enrollment_consumes_once(tmp_path):
    store = _store(tmp_path)
    _create(store, max_devices=2)
    stores = [EnrollmentDelegationStore(tmp_path / "private" / "delegations.json") for _ in range(8)]

    with ThreadPoolExecutor(max_workers=len(stores)) as pool:
        results = list(pool.map(lambda item: _consume(item, "same-enrollment"), stores))

    assert all(result.consumed_new_devices == 1 for result in results)
    assert EnrollmentDelegationStore(tmp_path / "private" / "delegations.json").get(ACTION_ID).consumed_new_devices == 1


def test_pending_proof_is_durable_but_does_not_consume_or_approve(tmp_path):
    store = _store(tmp_path)
    delegation = _create(store, max_devices=1)
    pending = store.record_pending(
        ACTION_ID,
        enrollment_id="proof-round-01",
        device_key_fingerprint=DEVICE_FINGERPRINT,
        **{key: value for key, value in _request().items() if key != "now"},
        expires_at=NOW + 300,
        now=NOW,
    )

    assert pending.status == "pending"
    assert pending.remaining_new_devices == 1
    assert store.get(ACTION_ID).consumed_new_devices == 0
    serialized = (tmp_path / "private" / "delegations.json").read_text()
    assert "proof-round-01" in serialized
    assert DEVICE_FINGERPRINT in serialized
    assert not any(
        forbidden in serialized.lower()
        for forbidden in (
            "proof_token",
            "private_key",
            "credential_value",
            "provider_secret",
            "enrollment_code",
        )
    )

    restarted = EnrollmentDelegationStore(tmp_path / "private" / "delegations.json")
    requests = restarted.pending_requests(agent_id=AGENT_ID, session_id=SESSION_ID, now=NOW)
    assert len(requests) == 1
    assert requests[0].enrollment_id == pending.enrollment_id
    assert requests[0].device_key_fingerprint == DEVICE_FINGERPRINT
    assert requests[0].status == "pending"
    assert restarted.get(ACTION_ID) == delegation


@pytest.mark.parametrize("revoke_before_reconciliation", (False, True))
def test_workspace_is_bound_to_approval_and_signed_install_receipt(tmp_path, revoke_before_reconciliation):
    store = _store(tmp_path)
    _create(store, max_devices=1)
    workspace_id = "a" * 32
    device_key = SigningKey.generate()
    owner_key = SigningKey.generate()
    device_fingerprint = hashlib.sha256(
        b"kollab-relay-enrollment-device-fingerprint-v1\0" + device_key.verify_key.encode()
    ).hexdigest()
    store.record_pending(
        ACTION_ID,
        enrollment_id="workspace-round-01",
        device_key_fingerprint=device_fingerprint,
        **{key: value for key, value in _request(workspace_id=workspace_id).items() if key != "now"},
        expires_at=NOW + 300,
        now=NOW,
    )
    store.consume(
        ACTION_ID,
        enrollment_id="workspace-round-01",
        device_key_fingerprint=device_fingerprint,
        **_request(workspace_id=workspace_id),
    )

    digest = hashlib.sha256(b"installed network mapping").hexdigest()
    offer_id, destination_public_key, device_fingerprint = _record_install_intent(
        store, "workspace-round-01", workspace_id, device_key, owner_key, digest
    )
    signed_receipt = sign_installation_receipt(
        device_key,
        {
            "v": 1,
            "offer_id": offer_id,
            "round_id": "workspace-round-01",
            "phase": "installation_ack",
            "destination_key": destination_public_key,
            "workspace_id": workspace_id,
            "status": "installed",
            "revision": 1,
            "digest": digest,
        },
    )
    installed = store.record_install_ack(
        "workspace-round-01",
        agent_id=AGENT_ID,
        session_id=SESSION_ID,
        device_key_fingerprint=device_fingerprint,
        workspace_id=workspace_id,
        install_status="installed",
        revision=1,
        digest=digest,
        offer_id=offer_id,
        device_signature=signed_receipt["device_signature"],
        now=NOW + 1,
    )
    assert installed.workspace_id == workspace_id
    assert installed.installation_status == "installed"
    assert installed.installation_revision == 1
    assert installed.installation_digest == digest
    assert installed.expected_install_digest == digest
    assert installed.installation_signature == signed_receipt["device_signature"]
    assert installed.peer_approved is False

    recovery_context = {
        "owner_public_key": owner_key.verify_key.encode().hex(),
        "issuer_relay_key": "1" * 64,
        "issuer_origin": "https://kollabor.ai",
        "room_fingerprint": "2" * 64,
        "issuer_workspace_id": ISSUER_WORKSPACE_ID,
    }
    mismatched_contexts = (
        {**recovery_context, "owner_public_key": "9" * 64},
        {**recovery_context, "issuer_relay_key": "8" * 64},
        {**recovery_context, "issuer_origin": "https://other.example"},
        {**recovery_context, "room_fingerprint": "7" * 64},
        {**recovery_context, "issuer_workspace_id": "d" * 32},
    )
    for mismatched_context in mismatched_contexts:
        assert store.install_receipts_for_reconciliation(**mismatched_context) == ()
        with pytest.raises(DelegationAuthorizationError, match="matching signed"):
            store.mark_peer_approved(
                "workspace-round-01",
                destination_public_key=destination_public_key,
                **mismatched_context,
                now=NOW + 2,
            )
    if revoke_before_reconciliation:
        store.revoke(ACTION_ID, now=NOW + 2)
        assert store.install_receipts_for_reconciliation(**recovery_context) == ()
        with pytest.raises(DelegationAuthorizationError, match="matching signed"):
            store.mark_peer_approved(
                "workspace-round-01",
                destination_public_key=destination_public_key,
                **recovery_context,
                now=NOW + 2,
            )
        reconciled = installed
    else:
        assert store.install_receipts_for_reconciliation(**recovery_context) == (installed,)
        reconciled = store.mark_peer_approved(
            "workspace-round-01",
            destination_public_key=destination_public_key,
            **recovery_context,
            # The receipt was durably stored before expiry. Expiry ends new
            # acceptance/delivery; it must not discard an already signed ACK.
            now=NOW + 601,
        )
        assert reconciled.peer_approved is True
    assert store.install_receipts_for_reconciliation(**recovery_context) == ()

    restarted = EnrollmentDelegationStore(tmp_path / "private" / "delegations.json")
    if revoke_before_reconciliation:
        with pytest.raises(DelegationAuthorizationError, match="no longer active"):
            restarted.get_enrollment_request(
                "workspace-round-01", agent_id=AGENT_ID, session_id=SESSION_ID, now=NOW + 3
            )
        stored = json.loads((tmp_path / "private" / "delegations.json").read_text(encoding="utf-8"))
        stored_request = stored["enrollment_requests"]["workspace-round-01"]
        assert stored["delegations"][ACTION_ID]["revoked"] is True
        assert stored_request["installation_receipt"]["device_signature"] == signed_receipt["device_signature"]
        assert stored_request["installation_receipt"]["peer_approved_at"] is None
    else:
        with pytest.raises(DelegationAuthorizationError, match="no longer active"):
            restarted.get_enrollment_request(
                "workspace-round-01", agent_id=AGENT_ID, session_id=SESSION_ID, now=NOW + 601
            )
        stored = json.loads((tmp_path / "private" / "delegations.json").read_text(encoding="utf-8"))
        stored_receipt = stored["enrollment_requests"]["workspace-round-01"]["installation_receipt"]
        assert stored_receipt["device_signature"] == signed_receipt["device_signature"]
        assert stored_receipt["peer_approved_at"] == NOW + 601

    path = tmp_path / "private" / "delegations.json"
    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["enrollment_requests"]["workspace-round-01"]["delivery_intent"]["expected_digest"] = hashlib.sha256(
        b"different issued payload"
    ).hexdigest()
    path.write_text(json.dumps(tampered), encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(DelegationPersistenceError, match="malformed"):
        EnrollmentDelegationStore(path)


def test_install_receipt_rejects_unbound_workspace_and_wrong_device(tmp_path):
    store = _store(tmp_path)
    _create(store, max_devices=1)
    workspace_id = "c" * 32
    device_key = SigningKey.generate()
    owner_key = SigningKey.generate()
    destination_public_key = device_key.verify_key.encode().hex()
    device_fingerprint = hashlib.sha256(
        b"kollab-relay-enrollment-device-fingerprint-v1\0" + device_key.verify_key.encode()
    ).hexdigest()
    store.record_pending(
        ACTION_ID,
        enrollment_id="workspace-negative-01",
        device_key_fingerprint=device_fingerprint,
        **{key: value for key, value in _request(workspace_id=workspace_id).items() if key != "now"},
        expires_at=NOW + 300,
        now=NOW,
    )
    store.consume(
        ACTION_ID,
        enrollment_id="workspace-negative-01",
        device_key_fingerprint=device_fingerprint,
        **_request(workspace_id=workspace_id),
    )

    digest = hashlib.sha256(b"mapping").hexdigest()
    offer_id, destination_public_key, device_fingerprint = _record_install_intent(
        store,
        "workspace-negative-01",
        workspace_id,
        device_key,
        owner_key,
        digest,
    )
    signed_receipt = sign_installation_receipt(
        device_key,
        {
            "v": 1,
            "offer_id": offer_id,
            "round_id": "workspace-negative-01",
            "phase": "installation_ack",
            "destination_key": destination_public_key,
            "workspace_id": workspace_id,
            "status": "installed",
            "revision": 1,
            "digest": digest,
        },
    )
    receipt = {
        "agent_id": AGENT_ID,
        "session_id": SESSION_ID,
        "device_key_fingerprint": device_fingerprint,
        "install_status": "installed",
        "revision": 1,
        "digest": digest,
        "offer_id": offer_id,
        "device_signature": signed_receipt["device_signature"],
        "now": NOW + 1,
    }
    with pytest.raises(DelegationAuthorizationError, match="approved scope"):
        store.record_install_ack("workspace-negative-01", workspace_id="d" * 32, **receipt)
    with pytest.raises(DelegationAuthorizationError, match="approved scope"):
        store.record_install_ack(
            "workspace-negative-01",
            workspace_id=workspace_id,
            **{
                **receipt,
                "device_key_fingerprint": hashlib.sha256(b"wrong device").hexdigest(),
            },
        )
    assert (
        store.get_enrollment_request(
            "workspace-negative-01",
            agent_id=AGENT_ID,
            session_id=SESSION_ID,
            now=NOW + 1,
        ).installation_status
        is None
    )


def test_v2_request_without_install_receipt_remains_readable(tmp_path):
    store = _store(tmp_path)
    _create(store)
    _record_pending(store, "old-v2-request")
    path = tmp_path / "private" / "delegations.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state["version"] = 2
    state["enrollment_requests"]["old-v2-request"].pop("delivery_intent")
    state["enrollment_requests"]["old-v2-request"].pop("installation_receipt")
    path.write_text(json.dumps(state), encoding="utf-8")
    path.chmod(0o600)

    restarted = EnrollmentDelegationStore(path)
    request = restarted.get_enrollment_request("old-v2-request", agent_id=AGENT_ID, session_id=SESSION_ID, now=NOW)
    assert request.status == "pending"
    assert request.workspace_id is None
    assert request.installation_status is None
    assert json.loads(path.read_text(encoding="utf-8"))["version"] == delegation_module.STATE_VERSION


def test_v3_delivery_receipt_migrates_without_unbound_recovery_authority(tmp_path):
    store = _store(tmp_path)
    _create(store, max_devices=1)
    workspace_id = "a" * 32
    enrollment_id = "old-v3-request"
    device_key = SigningKey.generate()
    owner_key = SigningKey.generate()
    device_fingerprint = hashlib.sha256(
        b"kollab-relay-enrollment-device-fingerprint-v1\0" + device_key.verify_key.encode()
    ).hexdigest()
    store.record_pending(
        ACTION_ID,
        enrollment_id=enrollment_id,
        device_key_fingerprint=device_fingerprint,
        **{key: value for key, value in _request(workspace_id=workspace_id).items() if key != "now"},
        expires_at=NOW + 300,
        now=NOW,
    )
    store.consume(
        ACTION_ID,
        enrollment_id=enrollment_id,
        device_key_fingerprint=device_fingerprint,
        **_request(workspace_id=workspace_id),
    )
    digest = hashlib.sha256(b"legacy v3 install").hexdigest()
    offer_id, destination_public_key, _ = _record_install_intent(
        store, enrollment_id, workspace_id, device_key, owner_key, digest
    )
    receipt = sign_installation_receipt(
        device_key,
        {
            "v": 1,
            "offer_id": offer_id,
            "round_id": enrollment_id,
            "phase": "installation_ack",
            "destination_key": destination_public_key,
            "workspace_id": workspace_id,
            "status": "installed",
            "revision": 1,
            "digest": digest,
        },
    )
    store.record_install_ack(
        enrollment_id,
        agent_id=AGENT_ID,
        session_id=SESSION_ID,
        device_key_fingerprint=device_fingerprint,
        workspace_id=workspace_id,
        install_status="installed",
        revision=1,
        digest=digest,
        offer_id=offer_id,
        device_signature=receipt["device_signature"],
        now=NOW + 1,
    )

    path = tmp_path / "private" / "delegations.json"
    state = json.loads(path.read_text(encoding="utf-8"))
    state["version"] = 3
    request = state["enrollment_requests"][enrollment_id]
    intent = request["delivery_intent"]
    intent.pop("issuer_workspace_id")
    old_signed_intent = sign_enrollment_payload(
        owner_key,
        {
            "v": 1,
            "offer_id": offer_id,
            "round_id": enrollment_id,
            "phase": "installation_intent",
            "destination_key": destination_public_key,
            "workspace_id": workspace_id,
            "expected_digest": digest,
            "issuer_relay_key": "1" * 64,
            "issuer_origin": "https://kollabor.ai",
            "room_fingerprint": "2" * 64,
            "owner_public_key": owner_key.verify_key.encode().hex(),
            "scope_fingerprint": request["scope_fingerprint"],
        },
    )
    intent["owner_signature"] = old_signed_intent["owner_signature"]
    path.write_text(json.dumps(state), encoding="utf-8")
    path.chmod(0o600)

    migrated = EnrollmentDelegationStore(path)
    request_record = migrated.get_enrollment_request(
        enrollment_id, agent_id=AGENT_ID, session_id=SESSION_ID, now=NOW + 2
    )
    assert request_record.status == "approved"
    assert request_record.installation_status == "installed"
    assert request_record.installation_digest == digest
    assert request_record.installation_signature == receipt["device_signature"]
    assert request_record.expected_install_digest == digest
    assert request_record.delivery_signature == intent["owner_signature"]
    assert request_record.issuer_workspace_id is None
    assert (
        migrated.install_receipts_for_reconciliation(
            owner_public_key=owner_key.verify_key.encode().hex(),
            issuer_relay_key="1" * 64,
            issuer_origin="https://kollabor.ai",
            room_fingerprint="2" * 64,
            issuer_workspace_id=ISSUER_WORKSPACE_ID,
        )
        == ()
    )
    migrated_state = json.loads(path.read_text(encoding="utf-8"))
    assert migrated_state["version"] == delegation_module.STATE_VERSION
    migrated_intent = migrated_state["enrollment_requests"][enrollment_id]["delivery_intent"]
    assert "issuer_workspace_id" not in migrated_intent


def test_rejection_is_durable_and_never_consumes_allowance(tmp_path):
    store = _store(tmp_path)
    _create(store, max_devices=1)
    _record_pending(store, "reject-round-01")
    rejected = store.reject_pending(
        ACTION_ID,
        enrollment_id="reject-round-01",
        device_key_fingerprint=DEVICE_FINGERPRINT,
        **{key: value for key, value in _request().items() if key != "now"},
        now=NOW,
    )

    assert rejected.status == "rejected"
    assert rejected.remaining_new_devices == 1
    assert store.get(ACTION_ID).consumed_new_devices == 0
    restarted = EnrollmentDelegationStore(tmp_path / "private" / "delegations.json")
    assert (
        restarted.get_enrollment_request("reject-round-01", agent_id=AGENT_ID, session_id=SESSION_ID, now=NOW).status
        == "rejected"
    )
    with pytest.raises(DelegationAuthorizationError, match="no longer pending"):
        _consume(restarted, "reject-round-01")
    assert restarted.get(ACTION_ID).consumed_new_devices == 0


def test_consume_requires_matching_pending_device_and_scope(tmp_path):
    store = _store(tmp_path)
    _create(store, max_devices=2)
    with pytest.raises(DelegationAuthorizationError, match="not pending"):
        store.consume(
            ACTION_ID,
            enrollment_id="unrecorded-round",
            device_key_fingerprint=DEVICE_FINGERPRINT,
            **_request(),
        )

    _record_pending(store, "bound-round")
    with pytest.raises(DelegationAuthorizationError, match="not pending"):
        store.consume(
            ACTION_ID,
            enrollment_id="bound-round",
            device_key_fingerprint=hashlib.sha256(b"different-device").hexdigest(),
            **_request(),
        )
    assert store.get(ACTION_ID).consumed_new_devices == 0


def test_legacy_store_migrates_without_losing_consumed_authority(tmp_path):
    store = _store(tmp_path)
    _create(store, max_devices=2)
    _record_pending(store, "legacy-approved-round")
    store.consume(
        ACTION_ID,
        enrollment_id="legacy-approved-round",
        device_key_fingerprint=DEVICE_FINGERPRINT,
        **_request(),
    )
    path = tmp_path / "private" / "delegations.json"
    state = json.loads(path.read_text())
    state["version"] = 1
    state.pop("enrollment_requests")
    path.write_text(json.dumps({"version": 1, "delegations": state["delegations"]}))
    path.chmod(0o600)

    migrated = EnrollmentDelegationStore(path)
    assert migrated.get(ACTION_ID).consumed_new_devices == 1
    assert json.loads(path.read_text())["version"] == delegation_module.STATE_VERSION


def test_duplicate_consumed_enrollment_id_in_state_fails_closed(tmp_path):
    store = _store(tmp_path)
    _create(store, max_devices=1)
    _create(store, action_id="second-action", max_devices=1)
    _consume(store, "duplicate-round")

    path = tmp_path / "private" / "delegations.json"
    state = json.loads(path.read_text())
    source = state["delegations"][ACTION_ID]
    target = state["delegations"]["second-action"]
    target["consumed_enrollments"] = dict(source["consumed_enrollments"])
    target["consumed_new_devices"] = 1
    path.write_text(json.dumps(state), encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(DelegationPersistenceError, match="duplicated across delegations"):
        store.get(ACTION_ID)


def test_corrupt_or_duplicate_json_fails_closed(tmp_path):
    store = _store(tmp_path)
    path = tmp_path / "private" / "delegations.json"
    path.write_text('{"version":1,"version":1,"delegations":{}}', encoding="utf-8")
    path.chmod(0o600)

    with pytest.raises(DelegationPersistenceError):
        store.get(ACTION_ID)


def test_capacity_failure_does_not_write_partial_authorization(tmp_path, monkeypatch):
    store = _store(tmp_path)
    _create(store, max_devices=1)
    path = tmp_path / "private" / "delegations.json"
    before = path.read_bytes()
    monkeypatch.setattr(delegation_module, "MAX_STATE_BYTES", len(before) + 1)

    with pytest.raises(DelegationCapacityError, match="write byte limit"):
        store.create(
            human_action_id="second-action",
            authorized_agent_id=AGENT_ID,
            authorized_session_id=SESSION_ID,
            issuer=ISSUER,
            network_ids=NETWORKS,
            configuration_profile=PROFILE,
            max_new_devices=1,
            expires_at=NOW + 300,
            now=NOW,
        )

    assert path.read_bytes() == before
    assert store.get(ACTION_ID).consumed_new_devices == 0
    assert store.get("second-action") is None


def test_existing_state_with_permissive_file_mode_is_rejected(tmp_path):
    store = _store(tmp_path)
    path = tmp_path / "private" / "delegations.json"
    path.chmod(0o644)

    with pytest.raises(DelegationPersistenceError, match="mode 0600"):
        store.get(ACTION_ID)
