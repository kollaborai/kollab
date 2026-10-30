"""Windows private-folder contract for approved enrollment recovery records."""

from __future__ import annotations

import os

import pytest

from kollabor_config import private_fs
from plugins.hub.enrollment_recovery import (
    EnrollmentRecoveryJournal,
    derive_enrollment_recovery_key,
)


@pytest.mark.skipif(os.name != "nt", reason="Windows private folders")
def test_windows_recovery_state_round_trips_in_a_private_folder(tmp_path):
    folder = tmp_path / "recovery"
    private_fs.ensure_private_dir(folder)
    journal = EnrollmentRecoveryJournal(
        folder / "state.json", derive_enrollment_recovery_key(b"r" * 32)
    )
    record = {
        "offer_id": "a" * 32,
        "status": "approval_intent",
        "secret": "provider-secret-and-code-key",
    }

    journal.put(record["offer_id"], record)

    assert journal.get(record["offer_id"]) == record
    private_fs.check_private_dir(folder)
