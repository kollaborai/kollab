"""Windows private folders for enrollment delegations: the round trip only runs on Windows."""

import os

import pytest

from kollabor_config import private_fs
from plugins.hub.enrollment_delegations import EnrollmentDelegationStore

NOW = 1_800_000_000
ACTION_ID = "human-action-01"
AGENT_ID = "agent:ed25519:" + "a" * 64
SESSION_ID = "session-01"
ISSUER = "ed25519:" + "b" * 64


@pytest.mark.skipif(os.name != "nt", reason="Windows private folders")
def test_windows_delegation_folder_is_private_after_write_and_read(tmp_path):
    path = tmp_path / "delegations" / "state.json"
    store = EnrollmentDelegationStore(path)
    store.create(
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
    assert EnrollmentDelegationStore(path).get(ACTION_ID) is not None
    private_fs.check_private_dir(path.parent)
