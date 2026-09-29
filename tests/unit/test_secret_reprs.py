"""repr() of anything that holds a key, a receipt id or a join code shows none of them.

A repr lands in logs, tracebacks and assertion output, so every class in the
network code that holds a secret keeps it out of its repr (constitution
section 13: keys, receipts and codes never reach a human).
"""

import asyncio
import re
import time

import pytest
from nacl.signing import SigningKey

from plugins.altview.connect_altview import (
    ConnectOutcome,
    ConnectScreenState,
    ConnectSubmission,
    PrivateCode,
)
from plugins.hub.contact_requests import (
    ContactDecision,
    PendingContactRequest,
    PrivateMessage,
)
from plugins.hub.enrollment_client import (
    EnrollmentApprovalRequest,
    _ActiveEnrollmentOffer,
    _LiveEnrollmentRequest,
)
from plugins.hub.enrollment_codes import EnrollmentEnvelopeKey
from plugins.hub.relay_agent import ActiveRelayTask
from plugins.hub.relay_commands import ConnectSnapshot, JoinRequestRow

KEY = "c" * 64
RECEIPT = "b" * 32
ROUND = "e" * 32
CODE = "ABCD-EFGH"


def _assert_clean(value, *secrets):
    text = repr(value)
    assert not re.search(r"[0-9a-f]{64}", text), text
    assert "ed25519:" not in text and "relay:" not in text, text
    for secret in secrets:
        assert secret not in text, text
    return text


def test_pending_contact_request_repr_is_the_name_and_the_expiry_only():
    request = PendingContactRequest(
        RECEIPT, KEY, 1_800_000_000, PrivateMessage("a private introduction"), "ana-laptop"
    )

    text = _assert_clean(request, RECEIPT, "a private introduction")

    assert "ana-laptop" in text and "1800000000" in text


def test_contact_decision_repr_hides_the_receipt():
    _assert_clean(ContactDecision(RECEIPT, "accepted"), RECEIPT)


def test_connect_outcome_repr_hides_the_receipt_id():
    _assert_clean(ConnectOutcome.pending("0123456789abcdef"), "0123456789abcdef")


def test_connect_submission_repr_hides_the_code():
    _assert_clean(ConnectSubmission("kollabor.ai", PrivateCode(CODE)), CODE)


def test_join_request_row_repr_hides_the_receipt():
    row = JoinRequestRow(RECEIPT, "ana-laptop", "4d04…9f2e", ("conversation:send",))

    text = _assert_clean(row, RECEIPT)

    assert "ana-laptop" in text


def test_connect_screen_state_repr_hides_the_join_code_and_nested_receipts():
    snapshot = ConnectSnapshot(
        network="marco-home",
        domain="kollabor.ai",
        trust="open",
        device="mac-kollab",
        relay_online=True,
        requests=(JoinRequestRow(RECEIPT, "ana-laptop", "4d04…9f2e"),),
    )
    state = ConnectScreenState(
        snapshot=snapshot, code=CODE, code_remaining=200, code_status="active"
    )

    _assert_clean(state, CODE, RECEIPT)


def test_enrollment_approval_request_repr_hides_the_receipt_and_the_fingerprint():
    request = EnrollmentApprovalRequest(
        enrollment_id=RECEIPT,
        device_key_fingerprint="9" * 64,
        issuer="issuer-principal",
        network_ids=("network-alpha",),
        configuration_profile=None,
        credential_categories=("conversation:send",),
        workspace_id="f" * 32,
        expires_at=1_800_000_000,
        remaining_new_devices=1,
        decision_available=True,
        device_name="ana-laptop",
    )

    text = _assert_clean(request, RECEIPT)

    assert "ana-laptop" in text


def _offer():
    return _ActiveEnrollmentOffer(
        offer_id="0123456789abcdef0123456789abcdef",
        expires_at=int(time.time()) + 300,
        human_action_id="human-action-secret",
        session_id="a" * 32,
        issuer_key=KEY,
        room_capability="d" * 64,
        origin="https://kollabor.ai",
        issuer_principal_id="issuer-principal",
        network_ids=("network-alpha",),
        profile=None,
        envelope_key=EnrollmentEnvelopeKey("0123456789abcdef0123456789abcdef", b"k" * 32),
        owner_signing_key=SigningKey.generate(),
        discovery=None,
        ca="",
        private_cidrs=(),
    )


def test_active_enrollment_offer_repr_hides_keys_ids_and_the_room():
    offer = _offer()

    _assert_clean(offer, offer.offer_id, "human-action-secret", "a" * 32, offer.room_capability)


@pytest.mark.asyncio
async def test_live_enrollment_request_repr_hides_the_round_and_the_keys():
    live = _LiveEnrollmentRequest(
        offer=_offer(),
        destination_key="d" * 64,
        round_id=ROUND,
        workspace_id="f" * 32,
        challenge_token="challenge-secret",
        proof_token="proof-secret",
        decision_event=asyncio.Event(),
        device_name="ana-laptop",
    )

    text = _assert_clean(live, ROUND, "challenge-secret", "proof-secret")

    assert "ana-laptop" in text


def test_active_relay_task_repr_hides_the_record():
    task = ActiveRelayTask(
        record={
            "id": RECEIPT,
            "peer": KEY,
            "payload": {"from": f"relay:{KEY}:{'f' * 32}:agent", "content": "private task"},
        },
        started_at=1.0,
    )

    _assert_clean(task, RECEIPT, "private task")

