"""Windows private-folder and private-file contract for approved enrollment recovery records."""

from __future__ import annotations

import os
import subprocess

import pytest

from kollabor_config import private_fs
from plugins.hub.enrollment_recovery import (
    EnrollmentRecoveryError,
    EnrollmentRecoveryJournal,
    derive_enrollment_recovery_key,
)

OFFER_ID = "a" * 32
# icacls by full path; *S-1-1-0 is Everyone by SID, so the grant works in any Windows language
ICACLS = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "icacls.exe")
EVERYONE_READS = "*S-1-1-0:(R)"


def _journal_with_one_record(folder):
    private_fs.ensure_private_dir(folder)
    journal = EnrollmentRecoveryJournal(
        folder / "state.json", derive_enrollment_recovery_key(b"r" * 32)
    )
    record = {
        "offer_id": OFFER_ID,
        "status": "approval_intent",
        "secret": "provider-secret-and-code-key",
    }
    journal.put(OFFER_ID, record)
    return journal, record


@pytest.mark.skipif(os.name != "nt", reason="Windows private folders")
def test_windows_recovery_state_round_trips_in_a_private_folder(tmp_path):
    folder = tmp_path / "recovery"
    journal, record = _journal_with_one_record(folder)
    assert journal.get(OFFER_ID) == record
    private_fs.check_private_dir(folder)


@pytest.mark.skipif(os.name != "nt", reason="Windows private files")
def test_windows_recovery_journal_others_can_read_is_refused(tmp_path):
    folder = tmp_path / "recovery"
    journal, _ = _journal_with_one_record(folder)
    subprocess.run([ICACLS, str(folder / "state.json"), "/grant", EVERYONE_READS], check=True, capture_output=True)
    with pytest.raises(EnrollmentRecoveryError, match="invalid_file"):
        journal.get(OFFER_ID)


@pytest.mark.skipif(os.name != "nt", reason="Windows private folders")
def test_windows_recovery_folder_others_can_read_is_refused(tmp_path):
    folder = tmp_path / "recovery"
    journal, _ = _journal_with_one_record(folder)
    subprocess.run([ICACLS, str(folder), "/grant", EVERYONE_READS], check=True, capture_output=True)
    with pytest.raises(EnrollmentRecoveryError, match="invalid_parent"):
        journal.get(OFFER_ID)
