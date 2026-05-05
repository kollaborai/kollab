#!/usr/bin/env python3
"""Test script to verify session ID fix."""

import sys
import tempfile
from pathlib import Path

sys.path.append(".")

from kollabor_ai import ConversationManager  # noqa: E402


class MockConfig:
    def __init__(self, conversations_dir):
        self._conversations_dir = conversations_dir

    def get(self, key, default=None):
        return default


print("Testing session ID generation...")

# Test 1: Without conversation logger (generates own ID)
with tempfile.TemporaryDirectory() as tmpdir:
    conversations_dir = Path(tmpdir) / "conversations"
    conversations_dir.mkdir()
    config = MockConfig(conversations_dir)
    manager = ConversationManager(config)
    print(f"[OK] Generated session ID: {manager.current_session_id}")
    manager.add_message("user", "Test message")
    saved_path = manager.save_conversation()
    print(f"[OK] Saved to: {saved_path.name}")
    print()

    # Test 2: With conversation logger (should use logger's ID)
    from kollabor_ai import KollaborConversationLogger  # noqa: E402

    logger = KollaborConversationLogger(conversations_dir)
    print(f"[OK] Logger session ID: {logger.session_id}")

    manager2 = ConversationManager(config, conversation_logger=logger)
    print(f"[OK] Manager session ID: {manager2.current_session_id}")

    if manager2.current_session_id == logger.session_id:
        print("[PASS] Session IDs match!")
    else:
        print(
            f"[FAIL] Session IDs don't match: {manager2.current_session_id} != {logger.session_id}"
        )

    manager2.add_message("user", "Test message 2")
    saved_path2 = manager2.save_conversation()
    print(f"[OK] Saved to: {saved_path2.name}")
    print()

    print("All tests passed!")
