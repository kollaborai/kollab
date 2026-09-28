"""Persistence contract tests for approved enrollment recovery records."""

from __future__ import annotations

import stat

import pytest

from plugins.hub.enrollment_recovery import (
    EnrollmentRecoveryError,
    EnrollmentRecoveryJournal,
    derive_enrollment_destination_recovery_key,
    derive_enrollment_recovery_key,
)


def test_recovery_journal_encrypts_and_reopens_secret_material(tmp_path):
    state_dir = tmp_path / "private-state"
    state_dir.mkdir(mode=0o700)
    state_dir.chmod(0o700)
    journal_path = state_dir / "enrollment-recovery.json"
    relay_key = b"r" * 32
    secret = "provider-secret-and-code-key"
    record = {
        "offer_id": "a" * 32,
        "status": "approval_intent",
        "secret": secret,
    }

    journal = EnrollmentRecoveryJournal(
        journal_path, derive_enrollment_recovery_key(relay_key)
    )
    journal.put(record["offer_id"], record)

    raw = journal_path.read_bytes()
    assert secret.encode() not in raw
    assert stat.S_IMODE(journal_path.stat().st_mode) == 0o600
    assert EnrollmentRecoveryJournal(
        journal_path, derive_enrollment_recovery_key(relay_key)
    ).get(record["offer_id"]) == record

    with pytest.raises(EnrollmentRecoveryError, match="invalid_journal"):
        EnrollmentRecoveryJournal(
            journal_path, derive_enrollment_recovery_key(b"x" * 32)
        ).records()


def test_recovery_journal_is_bounded_and_removes_completed_offer(tmp_path):
    state_dir = tmp_path / "private-state"
    state_dir.mkdir(mode=0o700)
    state_dir.chmod(0o700)
    journal = EnrollmentRecoveryJournal(
        state_dir / "enrollment-recovery.json",
        derive_enrollment_recovery_key(b"k" * 32),
    )
    offer_id = "b" * 32
    journal.put(offer_id, {"offer_id": offer_id, "status": "approved"})
    assert [record["offer_id"] for record in journal.records()] == [offer_id]
    journal.delete(offer_id)
    assert journal.records() == ()


def test_recovery_journal_rejects_unprivate_parent(tmp_path):
    state_dir = tmp_path / "shared"
    state_dir.mkdir(mode=0o755)
    state_dir.chmod(0o755)
    with pytest.raises(EnrollmentRecoveryError, match="invalid_parent"):
        EnrollmentRecoveryJournal(
            state_dir / "enrollment-recovery.json",
            derive_enrollment_recovery_key(b"k" * 32),
        )


def test_destination_and_issuer_recovery_keys_are_domain_separated():
    relay_key = b"k" * 32

    assert derive_enrollment_destination_recovery_key(relay_key) != derive_enrollment_recovery_key(
        relay_key
    )
