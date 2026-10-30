"""Windows private folders and files for enrollment delegations: these only run on Windows."""

import os
import subprocess

import pytest

from kollabor_config import private_fs
from plugins.hub.enrollment_delegations import DelegationPersistenceError, EnrollmentDelegationStore

NOW = 1_800_000_000
ACTION_ID = "human-action-01"
AGENT_ID = "agent:ed25519:" + "a" * 64
SESSION_ID = "session-01"
ISSUER = "ed25519:" + "b" * 64
# icacls by full path; *S-1-1-0 is Everyone by SID, so the grant works in any Windows language
ICACLS = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "icacls.exe")
EVERYONE_READS = "*S-1-1-0:(R)"


def _create_one(path):
    EnrollmentDelegationStore(path).create(
        human_action_id=ACTION_ID,
        authorized_agent_id=AGENT_ID,
        authorized_session_id=SESSION_ID,
        issuer=ISSUER,
        network_ids=("network-alpha",),
        configuration_profile="standard",
        credential_categories=("mcp:linear",),
        max_new_devices=2,
        expires_at=NOW + 600,
        now=NOW,
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows private folders")
def test_windows_delegation_folder_is_private_after_write_and_read(tmp_path):
    path = tmp_path / "delegations" / "state.json"
    _create_one(path)
    assert EnrollmentDelegationStore(path).get(ACTION_ID) is not None
    private_fs.check_private_dir(path.parent)


@pytest.mark.skipif(os.name != "nt", reason="Windows private files")
def test_windows_delegation_state_others_can_read_is_refused(tmp_path):
    path = tmp_path / "delegations" / "state.json"
    _create_one(path)
    subprocess.run([ICACLS, str(path), "/grant", EVERYONE_READS], check=True, capture_output=True)
    with pytest.raises(DelegationPersistenceError, match="must be private and owned"):
        EnrollmentDelegationStore(path).get(ACTION_ID)


@pytest.mark.skipif(os.name != "nt", reason="Windows private folders")
def test_windows_delegation_folder_others_can_read_is_refused(tmp_path):
    path = tmp_path / "delegations" / "state.json"
    _create_one(path)
    subprocess.run([ICACLS, str(path.parent), "/grant", EVERYONE_READS], check=True, capture_output=True)
    with pytest.raises(DelegationPersistenceError, match="parent must be user-owned"):
        EnrollmentDelegationStore(path).get(ACTION_ID)
