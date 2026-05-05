"""Test conversation logger sorting by newest first."""

import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

from kollabor_ai import KollaborConversationLogger


def test_list_sessions_sorts_newest_first():
    """Test that list_sessions returns sessions sorted by newest first."""
    with tempfile.TemporaryDirectory() as tmpdir:
        conversations_dir = Path(tmpdir) / "conversations"
        conversations_dir.mkdir()

        # Create multiple session files with different timestamps
        now = datetime.now()

        # Oldest session (3 days ago)
        old_session_id = f"{(now - timedelta(days=3)).strftime('%y%m%d%H%M')}-old"
        old_session_path = conversations_dir / f"{old_session_id}.jsonl"
        with open(old_session_path, "w") as f:
            old_metadata = {
                "type": "conversation_metadata",
                "startTime": (now - timedelta(days=3)).isoformat() + "Z",
                "sessionId": old_session_id,
            }
            f.write(json.dumps(old_metadata) + "\n")

        # Middle session (1 day ago)
        mid_session_id = f"{(now - timedelta(days=1)).strftime('%y%m%d%H%M')}-mid"
        mid_session_path = conversations_dir / f"{mid_session_id}.jsonl"
        with open(mid_session_path, "w") as f:
            mid_metadata = {
                "type": "conversation_metadata",
                "startTime": (now - timedelta(days=1)).isoformat() + "Z",
                "sessionId": mid_session_id,
            }
            f.write(json.dumps(mid_metadata) + "\n")

        # Newest session (today)
        new_session_id = f"{now.strftime('%y%m%d%H%M')}-new"
        new_session_path = conversations_dir / f"{new_session_id}.jsonl"
        with open(new_session_path, "w") as f:
            new_metadata = {
                "type": "conversation_metadata",
                "startTime": now.isoformat() + "Z",
                "sessionId": new_session_id,
            }
            f.write(json.dumps(new_metadata) + "\n")

        # Create logger and list sessions
        logger = KollaborConversationLogger(conversations_dir)
        sessions = logger.list_sessions()

        # Assert we have 3 sessions
        assert len(sessions) == 3, f"Expected 3 sessions, got {len(sessions)}"

        # Assert they are sorted newest first
        assert (
            sessions[0]["session_id"] == new_session_id
        ), f"First session should be newest ({new_session_id}), got {sessions[0]['session_id']}"
        assert (
            sessions[1]["session_id"] == mid_session_id
        ), f"Second session should be middle ({mid_session_id}), got {sessions[1]['session_id']}"
        assert (
            sessions[2]["session_id"] == old_session_id
        ), f"Third session should be oldest ({old_session_id}), got {sessions[2]['session_id']}"

        print("test_list_sessions_sorts_newest_first: PASSED")


def test_list_sessions_handles_missing_metadata():
    """Test that sorting works even when start_time is missing from metadata."""
    with tempfile.TemporaryDirectory() as tmpdir:
        conversations_dir = Path(tmpdir) / "conversations"
        conversations_dir.mkdir()

        now = datetime.now()

        # Session with valid metadata (newest by filename)
        new_session_id = f"{now.strftime('%y%m%d%H%M')}-with-meta"
        new_session_path = conversations_dir / f"{new_session_id}.jsonl"
        with open(new_session_path, "w") as f:
            new_metadata = {
                "type": "conversation_metadata",
                "startTime": now.isoformat() + "Z",
                "sessionId": new_session_id,
            }
            f.write(json.dumps(new_metadata) + "\n")

        # Session without metadata (older by filename)
        old_session_id = f"{(now - timedelta(hours=1)).strftime('%y%m%d%H%M')}-no-meta"
        old_session_path = conversations_dir / f"{old_session_id}.jsonl"
        with open(old_session_path, "w") as f:
            # Just a message without metadata
            message = {"type": "user", "message": {"role": "user", "content": "test"}}
            f.write(json.dumps(message) + "\n")

        # Create logger and list sessions
        logger = KollaborConversationLogger(conversations_dir)
        sessions = logger.list_sessions()

        # Assert we have 2 sessions
        assert len(sessions) == 2, f"Expected 2 sessions, got {len(sessions)}"

        # Assert they are sorted newest first (should fall back to filename parsing)
        assert (
            sessions[0]["session_id"] == new_session_id
        ), f"First session should be newest ({new_session_id}), got {sessions[0]['session_id']}"
        assert (
            sessions[1]["session_id"] == old_session_id
        ), f"Second session should be older ({old_session_id}), got {sessions[1]['session_id']}"

        print("test_list_sessions_handles_missing_metadata: PASSED")


if __name__ == "__main__":
    test_list_sessions_sorts_newest_first()
    test_list_sessions_handles_missing_metadata()
    print("\nAll tests passed!")
