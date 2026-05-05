"""Tests for conversation_manager get_message_by_uuid and rewrite_message."""

import sys
from pathlib import Path

sys.path.append(".")

from kollabor_ai.conversation_manager import ConversationManager


class MockConfig:
    def get(self, key, default=None):
        return default

    _conversations_dir = Path("/tmp/test_conversations_context")


def _make_manager():
    return ConversationManager(MockConfig())


class TestGetMessageByUuid:
    """Tests for get_message_by_uuid."""

    def test_finds_existing_message(self):
        mgr = _make_manager()
        uuid = mgr.add_message("user", "hello")
        msg = mgr.get_message_by_uuid(uuid)
        assert msg is not None
        assert msg["role"] == "user"
        assert msg["content"] == "hello"

    def test_returns_none_for_missing(self):
        mgr = _make_manager()
        assert mgr.get_message_by_uuid("nonexistent-uuid") is None

    def test_finds_message_after_multiple_adds(self):
        mgr = _make_manager()
        uuid1 = mgr.add_message("user", "first")
        uuid2 = mgr.add_message("assistant", "second")
        uuid3 = mgr.add_message("user", "third")

        msg2 = mgr.get_message_by_uuid(uuid2)
        assert msg2 is not None
        assert msg2["content"] == "second"

        # All three should be findable
        assert mgr.get_message_by_uuid(uuid1) is not None
        assert mgr.get_message_by_uuid(uuid3) is not None


class TestRewriteMessage:
    """Tests for rewrite_message."""

    def test_rewrite_existing_message(self):
        mgr = _make_manager()
        uuid = mgr.add_message("assistant", "original content")
        result = mgr.rewrite_message(uuid, "rewritten content")
        assert result is True

        msg = mgr.get_message_by_uuid(uuid)
        assert msg["content"] == "rewritten content"

    def test_rewrite_nonexistent_returns_false(self):
        mgr = _make_manager()
        result = mgr.rewrite_message("nonexistent-uuid", "new content")
        assert result is False

    def test_rewrite_preserves_other_fields(self):
        mgr = _make_manager()
        uuid = mgr.add_message("user", "old")
        msg_before = mgr.get_message_by_uuid(uuid)
        original_role = msg_before["role"]
        original_uuid = msg_before["uuid"]

        mgr.rewrite_message(uuid, "new")

        msg_after = mgr.get_message_by_uuid(uuid)
        assert msg_after["role"] == original_role
        assert msg_after["uuid"] == original_uuid
        assert msg_after["content"] == "new"

    def test_rewrite_empty_content(self):
        mgr = _make_manager()
        uuid = mgr.add_message("assistant", "big content here")
        mgr.rewrite_message(uuid, "[evicted]")
        msg = mgr.get_message_by_uuid(uuid)
        assert msg["content"] == "[evicted]"
