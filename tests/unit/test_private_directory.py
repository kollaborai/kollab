"""Security contract tests for private device enrollment and conversation grants."""

from __future__ import annotations

import base64
import json
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from nacl.signing import SigningKey

import plugins.hub.dns.private_directory as private_directory_module
from plugins.hub.dns.private_directory import (
    AuthorizationError,
    CredentialError,
    PrivateDirectory,
    ReplayError,
    StateCapacityError,
    new_device_signing_key,
    prove_pairing,
    public_key_id,
    sign_request,
    verify_pairing_proof,
)

NOW = 1_900_000_000
WORKSPACE = "ws-server-19"
TARGET_URI = "https://a2a.example.test/a2a"
TARGET_PATH = "/a2a"
PURPOSE = "workspace.write"
CONVERSATION = "conversation-abc"
REQUEST_BODY = b'{"jsonrpc":"2.0","id":1}'


def _claims(token: str) -> dict:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))


def _header(token: str) -> dict:
    protected = token.split(".")[0]
    protected += "=" * (-len(protected) % 4)
    return json.loads(base64.urlsafe_b64decode(protected))


def _flow(tmp_path: Path):
    owner_key = SigningKey.generate()
    owner_public = bytes(owner_key.verify_key)
    owner_dir = PrivateDirectory(
        tmp_path / "owner" / "private.json", owner_public_key=owner_public
    )
    receiver_dir = PrivateDirectory(
        tmp_path / "server" / "private.json",
        owner_public_key=owner_public,
        workspace_id=WORKSPACE,
    )
    device_key = new_device_signing_key()
    challenge = owner_dir.begin_pairing(
        owner_key,
        expected_device_public_key=bytes(device_key.verify_key),
        now=NOW,
    )
    receiver_dir.register_pairing_challenge(challenge, now=NOW)
    device_proof = prove_pairing(
        challenge,
        device_key,
        owner_public_key=owner_public,
        now=NOW + 1,
    )
    verify_pairing_proof(
        challenge.token,
        device_proof.token,
        owner_public_key=owner_public,
        now=NOW + 1,
    )
    receiver_dir.record_pairing_proof(challenge, device_proof, now=NOW + 1)
    owner_dir.record_pairing_proof(challenge, device_proof, now=NOW + 1)
    credential = owner_dir.approve_pairing(
        challenge,
        device_proof,
        owner_key,
        approved_by_human=True,
        now=NOW + 2,
    )
    receiver_dir.import_device_credential(credential, now=NOW + 2)
    grant = owner_dir.issue_conversation_grant(
        credential,
        owner_key,
        recipient_workspace_id=WORKSPACE,
        purpose=PURPOSE,
        conversation_id=CONVERSATION,
        approved_by_human=True,
        expires_in_seconds=900,
        now=NOW + 3,
    )
    return owner_key, owner_dir, receiver_dir, device_key, credential, grant


def _proof(
    device_key: SigningKey,
    credential: str,
    grant: str,
    *,
    message_id: str = "msg-1",
    now: int = NOW + 4,
) -> str:
    return sign_request(
        device_key,
        credential_jws=credential,
        grant_jws=grant,
        body=REQUEST_BODY,
        method="POST",
        path=TARGET_PATH,
        target_uri=TARGET_URI,
        recipient_workspace_id=WORKSPACE,
        purpose=PURPOSE,
        conversation_id=CONVERSATION,
        message_id=message_id,
        now=now,
    )


def _authorize(
    receiver,
    credential: str,
    grant: str,
    proof: str,
    *,
    message_id: str = "msg-1",
    now: int = NOW + 5,
):
    return receiver.authorize_request(
        credential,
        grant,
        proof,
        body=REQUEST_BODY,
        method="POST",
        path=TARGET_PATH,
        target_uri=TARGET_URI,
        recipient_workspace_id=WORKSPACE,
        purpose=PURPOSE,
        conversation_id=CONVERSATION,
        message_id=message_id,
        now=now,
    )


def test_owner_to_new_device_pairing_is_distinct_and_operator_approved(tmp_path):
    owner_key = SigningKey.generate()
    owner_public = bytes(owner_key.verify_key)
    owner_dir = PrivateDirectory(tmp_path / "owner.json", owner_public_key=owner_public)
    device_key = new_device_signing_key()
    challenge = owner_dir.begin_pairing(
        owner_key,
        expected_device_public_key=bytes(device_key.verify_key),
        now=NOW,
    )
    assert public_key_id(bytes(device_key.verify_key)) != public_key_id(owner_public)

    proof = prove_pairing(
        challenge, device_key, owner_public_key=owner_public, now=NOW + 1
    )
    owner_dir.record_pairing_proof(challenge, proof, now=NOW + 1)
    assert owner_dir.members(now=NOW + 1) == ()
    assert len(owner_dir.pending_pairing_proofs(now=NOW + 1)) == 1

    with pytest.raises(AuthorizationError, match="human approval"):
        owner_dir.approve_pairing(
            challenge,
            proof,
            owner_key,
            approved_by_human=False,
            now=NOW + 2,
        )
    assert owner_dir.members(now=NOW + 2) == ()

    credential = owner_dir.approve_pairing(
        challenge,
        proof,
        owner_key,
        approved_by_human=True,
        now=NOW + 2,
    )
    assert credential.device_id == public_key_id(bytes(device_key.verify_key))
    assert len(owner_dir.members(now=NOW + 2)) == 1
    repeated = owner_dir.approve_pairing(
        challenge,
        proof,
        owner_key,
        approved_by_human=True,
        now=NOW + 3,
    )
    assert repeated.token == credential.token
    assert repeated.credential_id == credential.credential_id
    assert len(owner_dir.members(now=NOW + 3)) == 1

    different_proof = prove_pairing(
        challenge,
        device_key,
        owner_public_key=owner_public,
        now=NOW + 3,
    )
    with pytest.raises(AuthorizationError, match="proof"):
        owner_dir.approve_pairing(
            challenge,
            different_proof,
            owner_key,
            approved_by_human=True,
            now=NOW + 4,
        )


def test_revoke_issued_credential_rejects_device_and_owner_mismatch_without_mutation(
    tmp_path,
):
    owner_key, owner_dir, _receiver, device_key, credential, _grant = _flow(tmp_path)
    state_path = tmp_path / "owner" / "private.json"
    original_state = state_path.read_bytes()
    original_members = owner_dir.members(now=NOW + 4)

    with pytest.raises(AuthorizationError, match="different device"):
        owner_dir.revoke_issued_credential(
            credential,
            owner_key,
            expected_device_public_key=bytes(new_device_signing_key().verify_key),
            now=NOW + 4,
        )
    assert state_path.read_bytes() == original_state
    assert owner_dir.members(now=NOW + 4) == original_members

    with pytest.raises(AuthorizationError, match="pinned owner key"):
        owner_dir.revoke_issued_credential(
            credential,
            SigningKey.generate(),
            expected_device_public_key=bytes(device_key.verify_key),
            now=NOW + 4,
        )
    assert state_path.read_bytes() == original_state
    assert owner_dir.members(now=NOW + 4) == original_members

    revocation = owner_dir.revoke_issued_credential(
        credential,
        owner_key,
        expected_device_public_key=bytes(device_key.verify_key),
        now=NOW + 5,
    )
    assert revocation is not None
    assert revocation.target_type == "credential"
    assert revocation.target_id == credential.credential_id
    assert owner_dir.members(now=NOW + 5) == ()


def test_pairing_proof_must_match_owner_challenge_and_device_signature(tmp_path):
    owner_key = SigningKey.generate()
    owner_dir = PrivateDirectory(
        tmp_path / "private.json", owner_public_key=bytes(owner_key.verify_key)
    )
    intended_device_key = new_device_signing_key()
    challenge = owner_dir.begin_pairing(
        owner_key,
        expected_device_public_key=bytes(intended_device_key.verify_key),
        now=NOW,
    )

    with pytest.raises(CredentialError, match="pinned owner"):
        prove_pairing(
            challenge,
            intended_device_key,
            owner_public_key=bytes(SigningKey.generate().verify_key),
            now=NOW + 1,
        )

    attacker_key = new_device_signing_key()
    attacker_proof = prove_pairing_unchecked_for_test(challenge, attacker_key)
    with pytest.raises(CredentialError, match="different device key"):
        owner_dir.record_pairing_proof(challenge, attacker_proof, now=NOW + 1)
    assert owner_dir.pending_pairing_proofs(now=NOW + 1) == ()

    intended_proof = prove_pairing(
        challenge,
        intended_device_key,
        owner_public_key=bytes(owner_key.verify_key),
        now=NOW + 1,
    )
    owner_dir.record_pairing_proof(challenge, intended_proof, now=NOW + 1)
    assert owner_dir.pending_pairing_proofs(now=NOW + 1)[0].device_id == public_key_id(
        bytes(intended_device_key.verify_key)
    )

    # Change signature bytes while preserving canonical base64url. Replacing
    # the final character can instead change only padding bits and randomly
    # exercise the compact-JWS parser rather than signature verification.
    header, body, signature = challenge.token.split(".")
    changed = bytearray(base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4)))
    changed[0] ^= 1
    tampered = ".".join((header, body, base64.urlsafe_b64encode(changed).decode().rstrip("=")))
    with pytest.raises(CredentialError, match="signature"):
        verify_pairing_proof(
            tampered,
            "x.y.z",
            owner_public_key=bytes(owner_key.verify_key),
            now=NOW + 1,
        )


def prove_pairing_unchecked_for_test(challenge, device_key):
    """Create a valid device signature for another key to exercise pinning."""
    # A raw owner-signed challenge must never yield a proof for another key.
    # This deliberately uses the low-level test helpers to model a hostile client.
    from plugins.hub.dns.private_directory import _sign_jws

    claims = {
        "kollab_type": "kollab-pairing-proof-v1",
        "iss": public_key_id(bytes(device_key.verify_key)),
        "sub": public_key_id(bytes(device_key.verify_key)),
        "aud": challenge.owner_id,
        "iat": NOW + 1,
        "exp": challenge.expires_at,
        "jti": "attacker-proof",
        "challenge_id": challenge.challenge_id,
        "challenge_sha256": __import__("hashlib")
        .sha256(challenge.token.encode("ascii"))
        .hexdigest(),
        "challenge_nonce": _claims(challenge.token)["nonce"],
    }
    return _sign_jws(
        claims, device_key, kid=public_key_id(bytes(device_key.verify_key))
    )


def test_pairing_challenge_expiry_prevents_proof_and_approval(tmp_path):
    owner_key = SigningKey.generate()
    owner_dir = PrivateDirectory(
        tmp_path / "private.json", owner_public_key=bytes(owner_key.verify_key)
    )
    device_key = new_device_signing_key()
    challenge = owner_dir.begin_pairing(
        owner_key,
        expected_device_public_key=bytes(device_key.verify_key),
        expires_in_seconds=2,
        now=NOW,
    )

    with pytest.raises(CredentialError, match="expired"):
        prove_pairing(
            challenge,
            device_key,
            owner_public_key=bytes(owner_key.verify_key),
            now=NOW + 2,
        )


def test_private_directory_is_installed_only_after_credential_import(tmp_path):
    owner_key, owner_dir, receiver, device_key, credential, grant = _flow(tmp_path)
    assert len(owner_dir.members(now=NOW + 3)) == 1
    assert receiver.members(now=NOW + 3)[0].credential_id == credential.credential_id

    other_receiver = PrivateDirectory(
        tmp_path / "other" / "private.json",
        owner_public_key=bytes(owner_key.verify_key),
        workspace_id=WORKSPACE,
    )
    with pytest.raises(AuthorizationError, match="private directory"):
        other_receiver.authorize_request(
            credential.token,
            grant.token,
            _proof(device_key, credential.token, grant.token),
            body=REQUEST_BODY,
            method="POST",
            path=TARGET_PATH,
            target_uri=TARGET_URI,
            recipient_workspace_id=WORKSPACE,
            purpose=PURPOSE,
            conversation_id=CONVERSATION,
            message_id="msg-1",
            now=NOW + 5,
        )


def test_conversation_grant_requires_explicit_owner_approval_and_has_bounded_scope(
    tmp_path,
):
    owner_key, owner_dir, receiver, device_key, credential, grant = _flow(tmp_path)
    claims = _claims(grant.token)
    assert _header(grant.token)["alg"] == "EdDSA"
    assert claims["iss"] == owner_dir.owner_id
    assert claims["sub"] == credential.device_id
    assert claims["aud"] == f"urn:kollab:workspace:{WORKSPACE}"
    assert claims["purpose"] == PURPOSE
    assert claims["conversation_id"] == CONVERSATION
    assert isinstance(claims["exp"], int) and claims["exp"] > NOW

    with pytest.raises(AuthorizationError, match="human approval"):
        owner_dir.issue_conversation_grant(
            credential,
            owner_key,
            recipient_workspace_id=WORKSPACE,
            purpose="workspace.read",
            conversation_id=CONVERSATION,
            approved_by_human=False,
            now=NOW + 5,
        )

    other_workspace = "different-workspace"
    other_receiver = PrivateDirectory(
        tmp_path / "unbound" / "private.json",
        owner_public_key=bytes(owner_key.verify_key),
    )
    other_receiver.import_device_credential(credential, now=NOW + 2)
    alternate_proof = sign_request(
        device_key,
        credential_jws=credential.token,
        grant_jws=grant.token,
        body=REQUEST_BODY,
        method="POST",
        path=TARGET_PATH,
        target_uri=TARGET_URI,
        recipient_workspace_id=other_workspace,
        purpose=PURPOSE,
        conversation_id=CONVERSATION,
        message_id="msg-other-workspace",
        now=NOW + 4,
    )
    with pytest.raises(AuthorizationError, match="audience"):
        other_receiver.authorize_request(
            credential.token,
            grant.token,
            alternate_proof,
            body=REQUEST_BODY,
            method="POST",
            path=TARGET_PATH,
            target_uri=TARGET_URI,
            recipient_workspace_id=other_workspace,
            purpose=PURPOSE,
            conversation_id=CONVERSATION,
            message_id="msg-1",
            now=NOW + 5,
        )


def test_receiver_authorizes_only_after_owner_membership_grant_and_device_proof(
    tmp_path,
):
    _, _, receiver, device_key, credential, grant = _flow(tmp_path)
    proof = _proof(device_key, credential.token, grant.token)
    principal = _authorize(receiver, credential.token, grant.token, proof)
    assert principal.device_id == credential.device_id
    assert principal.owner_id == credential.owner_id
    assert principal.workspace_id == WORKSPACE
    assert principal.purpose == PURPOSE
    assert principal.conversation_id == CONVERSATION
    assert principal.message_id == "msg-1"
    assert principal.target_uri == TARGET_URI
    assert receiver.revalidate(principal, now=NOW + 6) == principal


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("body", b'{"jsonrpc":"2.0","id":2}'),
        ("path", "/different"),
        ("target_uri", "https://other.example.test/a2a"),
        ("purpose", "workspace.read"),
        ("conversation_id", "another-conversation"),
        ("message_id", "different-message"),
    ],
)
def test_request_proof_binds_raw_body_target_and_scope(tmp_path, field, value):
    _, _, receiver, device_key, credential, grant = _flow(tmp_path)
    proof = _proof(device_key, credential.token, grant.token)
    args = {
        "body": REQUEST_BODY,
        "method": "POST",
        "path": TARGET_PATH,
        "target_uri": TARGET_URI,
        "recipient_workspace_id": WORKSPACE,
        "purpose": PURPOSE,
        "conversation_id": CONVERSATION,
        "message_id": "msg-1",
        "now": NOW + 5,
    }
    args[field] = value
    with pytest.raises(
        (AuthorizationError, ValueError),
        match="proof|path|target_uri|purpose|conversation",
    ):
        receiver.authorize_request(credential.token, grant.token, proof, **args)


def test_request_proof_rejects_another_device_even_with_valid_bearer_tokens(tmp_path):
    _, _, receiver, _, credential, grant = _flow(tmp_path)
    another_device_key = new_device_signing_key()
    forged_proof = _proof(another_device_key, credential.token, grant.token)
    with pytest.raises(AuthorizationError, match="signed by the enrolled device"):
        _authorize(receiver, credential.token, grant.token, forged_proof)


def test_request_proof_and_message_id_replay_are_rejected(tmp_path):
    _, _, receiver, device_key, credential, grant = _flow(tmp_path)
    proof = _proof(device_key, credential.token, grant.token)
    _authorize(receiver, credential.token, grant.token, proof)
    with pytest.raises(ReplayError):
        _authorize(receiver, credential.token, grant.token, proof)

    new_proof_same_message = _proof(device_key, credential.token, grant.token)
    with pytest.raises(ReplayError):
        _authorize(receiver, credential.token, grant.token, new_proof_same_message)


def test_replay_protection_is_atomic_across_directory_instances(tmp_path):
    owner_key, _, receiver, device_key, credential, grant = _flow(tmp_path)
    second_receiver = PrivateDirectory(
        tmp_path / "server" / "private.json",
        owner_public_key=bytes(owner_key.verify_key),
        workspace_id=WORKSPACE,
    )
    proofs = [
        _proof(device_key, credential.token, grant.token),
        _proof(device_key, credential.token, grant.token),
    ]
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(
            pool.map(
                lambda pair: _attempt_authorize(
                    pair[0], credential.token, grant.token, pair[1]
                ),
                [(receiver, proofs[0]), (second_receiver, proofs[1])],
            )
        )
    assert sorted(outcomes) == ["authorized", "replay"]


def _attempt_authorize(receiver, credential, grant, proof):
    try:
        _authorize(receiver, credential, grant, proof)
        return "authorized"
    except ReplayError:
        return "replay"


def test_local_revocation_persists_and_revalidate_blocks_queued_work(tmp_path):
    owner_key, owner_dir, receiver, device_key, credential, grant = _flow(tmp_path)
    principal = _authorize(
        receiver,
        credential.token,
        grant.token,
        _proof(device_key, credential.token, grant.token),
    )
    revocation = owner_dir.revoke("grant", grant.grant_id, owner_key, now=NOW + 6)
    receiver.apply_revocation(revocation)
    with pytest.raises(AuthorizationError, match="revoked"):
        receiver.revalidate(principal, now=NOW + 7)

    restarted = PrivateDirectory(
        tmp_path / "server" / "private.json",
        owner_public_key=bytes(owner_key.verify_key),
        workspace_id=WORKSPACE,
    )
    with pytest.raises(AuthorizationError, match="revoked"):
        restarted.revalidate(principal, now=NOW + 7)
    assert len(restarted.members(now=NOW + 7)) == 1


def test_device_revocation_removes_member_and_blocks_import(tmp_path):
    owner_key, owner_dir, receiver, _, credential, _ = _flow(tmp_path)
    revocation = owner_dir.revoke(
        "device", credential.device_id, owner_key, now=NOW + 5
    )
    receiver.apply_revocation(revocation)
    assert receiver.members(now=NOW + 6) == ()
    with pytest.raises(AuthorizationError, match="revoked"):
        receiver.import_device_credential(credential, now=NOW + 6)


def test_expired_device_credential_is_not_listed_or_authorized(tmp_path):
    owner_key = SigningKey.generate()
    owner_dir = PrivateDirectory(
        tmp_path / "owner.json", owner_public_key=bytes(owner_key.verify_key)
    )
    device_key = new_device_signing_key()
    challenge = owner_dir.begin_pairing(
        owner_key,
        expected_device_public_key=bytes(device_key.verify_key),
        now=NOW,
    )
    proof = prove_pairing(
        challenge, device_key, owner_public_key=bytes(owner_key.verify_key), now=NOW + 1
    )
    owner_dir.record_pairing_proof(challenge, proof, now=NOW + 1)
    owner_dir.approve_pairing(
        challenge,
        proof,
        owner_key,
        approved_by_human=True,
        credential_ttl_seconds=5,
        now=NOW + 2,
    )
    assert owner_dir.members(now=NOW + 7) == ()


def test_private_state_is_restrictive_and_tokens_do_not_contain_workspace_paths(
    tmp_path,
):
    owner_key, owner_dir, receiver, device_key, credential, grant = _flow(tmp_path)
    credential_claims = _claims(credential.token)
    grant_claims = _claims(grant.token)
    assert "private_key" not in json.dumps(credential_claims).lower()
    assert "private_key" not in json.dumps(grant_claims).lower()
    assert str(tmp_path) not in json.dumps(credential_claims)
    assert str(tmp_path) not in json.dumps(grant_claims)
    assert stat.S_IMODE((tmp_path / "server" / "private.json").stat().st_mode) == 0o600
    assert (
        stat.S_IMODE((tmp_path / "server" / "private.json.lock").stat().st_mode)
        == 0o600
    )

    with pytest.raises(ValueError, match="filesystem path"):
        PrivateDirectory(
            tmp_path / "bad.json",
            owner_public_key=bytes(owner_key.verify_key),
            workspace_id=str(tmp_path),
        )


def test_directory_refuses_state_bound_to_another_owner_key(tmp_path):
    owner_key = SigningKey.generate()
    state_path = tmp_path / "private.json"
    PrivateDirectory(state_path, owner_public_key=bytes(owner_key.verify_key))
    with pytest.raises(Exception, match="different owner"):
        PrivateDirectory(
            state_path, owner_public_key=bytes(SigningKey.generate().verify_key)
        )


def test_state_and_member_roster_survive_restart_without_private_keys(tmp_path):
    owner_key, _, _, _, credential, _ = _flow(tmp_path)
    owner_state = tmp_path / "owner" / "private.json"
    restarted = PrivateDirectory(
        owner_state, owner_public_key=bytes(owner_key.verify_key)
    )
    assert [member.credential_id for member in restarted.members(now=NOW + 3)] == [
        credential.credential_id
    ]
    raw_state = owner_state.read_text(encoding="utf-8")
    assert bytes(owner_key).hex() not in raw_state


def test_replay_capacity_fails_closed_without_evicting_live_proofs(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(private_directory_module, "MAX_ACTIVE_PROOFS", 1)
    _, _, receiver, device_key, credential, grant = _flow(tmp_path)
    first = _proof(device_key, credential.token, grant.token, message_id="msg-1")
    _authorize(receiver, credential.token, grant.token, first, message_id="msg-1")

    second = _proof(
        device_key, credential.token, grant.token, message_id="msg-2", now=NOW + 6
    )
    with pytest.raises(StateCapacityError, match="used_proofs ledger is full"):
        _authorize(
            receiver,
            credential.token,
            grant.token,
            second,
            message_id="msg-2",
            now=NOW + 7,
        )
    state_after_rejection = json.loads(
        (tmp_path / "server" / "private.json").read_text(encoding="utf-8")
    )
    assert len(state_after_rejection["used_proofs"]) == 1
    assert len(state_after_rejection["seen_messages"]) == 1
    with pytest.raises(ReplayError):
        _authorize(
            receiver,
            credential.token,
            grant.token,
            first,
            message_id="msg-1",
            now=NOW + 8,
        )

    after_expiry = _proof(
        device_key, credential.token, grant.token, message_id="msg-3", now=NOW + 305
    )
    _authorize(
        receiver,
        credential.token,
        grant.token,
        after_expiry,
        message_id="msg-3",
        now=NOW + 306,
    )
    state = json.loads(
        (tmp_path / "server" / "private.json").read_text(encoding="utf-8")
    )
    assert len(state["used_proofs"]) == 1
    assert (
        len(state["seen_messages"]) == 2
    )  # proof expiry never erases the longer message replay window


def test_message_replay_capacity_prunes_only_after_its_grant_expires(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(private_directory_module, "MAX_ACTIVE_MESSAGES", 1)
    owner_key, owner_dir, receiver, device_key, credential, grant = _flow(tmp_path)
    first = _proof(device_key, credential.token, grant.token, message_id="msg-1")
    _authorize(receiver, credential.token, grant.token, first, message_id="msg-1")

    second = _proof(
        device_key, credential.token, grant.token, message_id="msg-2", now=NOW + 6
    )
    with pytest.raises(StateCapacityError, match="seen_messages ledger is full"):
        _authorize(
            receiver,
            credential.token,
            grant.token,
            second,
            message_id="msg-2",
            now=NOW + 7,
        )
    with pytest.raises(ReplayError):
        _authorize(
            receiver,
            credential.token,
            grant.token,
            first,
            message_id="msg-1",
            now=NOW + 8,
        )

    renewed_grant = owner_dir.issue_conversation_grant(
        credential,
        owner_key,
        recipient_workspace_id=WORKSPACE,
        purpose=PURPOSE,
        conversation_id=CONVERSATION,
        approved_by_human=True,
        expires_in_seconds=900,
        now=NOW + 904,
    )
    renewed_proof = _proof(
        device_key,
        credential.token,
        renewed_grant.token,
        message_id="msg-1",
        now=NOW + 905,
    )
    _authorize(
        receiver,
        credential.token,
        renewed_grant.token,
        renewed_proof,
        message_id="msg-1",
        now=NOW + 906,
    )
    state = json.loads(
        (tmp_path / "server" / "private.json").read_text(encoding="utf-8")
    )
    assert len(state["seen_messages"]) == 1


def test_pending_pairing_expiry_frees_capacity_without_admitting_old_device(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(private_directory_module, "MAX_PENDING_PAIRINGS", 1)
    owner_key = SigningKey.generate()
    owner_dir = PrivateDirectory(
        tmp_path / "owner.json", owner_public_key=bytes(owner_key.verify_key)
    )
    old_device = new_device_signing_key()
    old_challenge = owner_dir.begin_pairing(
        owner_key,
        expected_device_public_key=bytes(old_device.verify_key),
        expires_in_seconds=1,
        now=NOW,
    )
    new_device = new_device_signing_key()
    new_challenge = owner_dir.begin_pairing(
        owner_key,
        expected_device_public_key=bytes(new_device.verify_key),
        now=NOW + 2,
    )
    with pytest.raises(AuthorizationError, match="missing, used, or expired"):
        owner_dir.get_pairing_challenge(old_challenge.challenge_id, now=NOW + 2)
    assert (
        owner_dir.get_pairing_challenge(new_challenge.challenge_id, now=NOW + 2)
        == new_challenge
    )


def test_revocations_are_never_pruned_and_capacity_rejects_new_revocations(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(private_directory_module, "MAX_REVOCATIONS", 1)
    owner_key, owner_dir, _, _, credential, _ = _flow(tmp_path)
    first = owner_dir.revoke("device", credential.device_id, owner_key, now=NOW + 4)
    with pytest.raises(StateCapacityError, match="revocations ledger is full"):
        owner_dir.revoke(
            "credential", credential.credential_id, owner_key, now=NOW + 10_000
        )

    restarted = PrivateDirectory(
        tmp_path / "owner" / "private.json",
        owner_public_key=bytes(owner_key.verify_key),
    )
    with pytest.raises(StateCapacityError, match="revocations ledger is full"):
        restarted.revoke("grant", "some-other-grant", owner_key, now=NOW + 20_000)
    state = json.loads(
        (tmp_path / "owner" / "private.json").read_text(encoding="utf-8")
    )
    assert state["revocations"] == {first.revocation_id: first.token}


def test_expired_accepted_grants_are_pruned_before_grant_capacity_check(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(private_directory_module, "MAX_ACCEPTED_GRANTS", 1)
    owner_key, owner_dir, receiver, device_key, credential, grant = _flow(tmp_path)
    first = _proof(device_key, credential.token, grant.token, message_id="msg-1")
    _authorize(receiver, credential.token, grant.token, first)

    renewed_grant = owner_dir.issue_conversation_grant(
        credential,
        owner_key,
        recipient_workspace_id=WORKSPACE,
        purpose=PURPOSE,
        conversation_id=CONVERSATION,
        approved_by_human=True,
        now=NOW + 904,
    )
    renewed_proof = _proof(
        device_key,
        credential.token,
        renewed_grant.token,
        message_id="msg-2",
        now=NOW + 905,
    )
    _authorize(
        receiver,
        credential.token,
        renewed_grant.token,
        renewed_proof,
        message_id="msg-2",
        now=NOW + 906,
    )
    state = json.loads(
        (tmp_path / "server" / "private.json").read_text(encoding="utf-8")
    )
    assert set(state["accepted_grants"]) == {renewed_grant.grant_id}


def test_state_file_read_and_write_have_a_byte_cap(tmp_path, monkeypatch):
    owner_key = SigningKey.generate()
    state_path = tmp_path / "private.json"
    directory = PrivateDirectory(
        state_path, owner_public_key=bytes(owner_key.verify_key)
    )
    original = state_path.read_bytes()
    monkeypatch.setattr(private_directory_module, "MAX_STATE_BYTES", len(original) + 10)

    device_key = new_device_signing_key()
    with pytest.raises(StateCapacityError, match="write limit"):
        directory.begin_pairing(
            owner_key,
            expected_device_public_key=bytes(device_key.verify_key),
            now=NOW,
        )
    assert state_path.read_bytes() == original

    monkeypatch.setattr(private_directory_module, "MAX_STATE_BYTES", len(original) - 1)
    with pytest.raises(StateCapacityError, match="read limit"):
        PrivateDirectory(state_path, owner_public_key=bytes(owner_key.verify_key))
