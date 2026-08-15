import sys
import unittest
from unittest.mock import MagicMock

sys.path.append(".")

try:
    from kollabor_ai import ConversationManager

    class MockConfig:
        def get(self, key, default=None):
            return default

    # Test ConversationManager
    config = MockConfig()
    manager = ConversationManager(config)

    print("✅ ConversationManager created successfully")

    # Test basic functionality
    msg1 = manager.add_message("user", "Hello")
    msg2 = manager.add_message("assistant", "Hi there!", parent_uuid=msg1)
    msg3 = manager.add_message("user", "How are you?")

    print(
        f"✅ Message threading working: {len(manager.get_message_thread(msg3))} messages in thread"
    )
    print(
        f"✅ Context retrieval working: {len(manager.get_context_messages())} messages"
    )
    print(
        f"✅ Search functionality working: {len(manager.search_messages('hello'))} results"
    )

    # Test persistence
    saved_path = manager.save_conversation()
    print(f"✅ Persistence working: {saved_path}")

    # Test statistics
    stats = manager.get_conversation_stats()
    print(f"✅ Statistics working: {stats['messages']['total']} total messages")

    print("\n🎉 ConversationManager fully functional!")

except Exception as e:
    print(f"❌ Error: {e}")
    import traceback

    traceback.print_exc()


class TestConversationSessionStatsPersistence(unittest.TestCase):
    """Regression tests for token/cache statistics across process boundaries."""

    SESSION_STATS = {
        "messages": 2,
        "input_tokens": 101,
        "output_tokens": 202,
        "total_input_tokens": 303,
        "total_output_tokens": 404,
        "cache_read_tokens": 505,
        "cache_creation_tokens": 606,
        "total_cache_read_tokens": 707,
        "total_cache_creation_tokens": 808,
    }

    def _manager(self, root):
        config = MagicMock()
        config._conversations_dir = root
        config.get.side_effect = lambda _key, default=None: default
        return ConversationManager(config)

    def test_save_then_new_manager_load_restores_exact_session_stats(self):
        """A new manager restores all persisted token/cache counters."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            writer = self._manager(root)
            writer.current_session_id = "saved-stats"
            writer.bind_session_stats(dict(self.SESSION_STATS))
            writer.add_message("user", "persist these counters")
            self.assertTrue(writer.save_session("saved-stats"))

            restored_stats = {key: -1 for key in self.SESSION_STATS}
            reader = self._manager(root)
            reader.bind_session_stats(restored_stats)

            self.assertTrue(reader.load_session("saved-stats"))
            self.assertEqual(restored_stats, self.SESSION_STATS)

    def test_snapshot_load_in_new_manager_restores_exact_session_stats(self):
        """Direct snapshot loading restores the same persisted counters."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            writer = self._manager(root)
            writer.current_session_id = "snapshot-stats"
            writer.bind_session_stats(dict(self.SESSION_STATS))
            writer.add_message("user", "persist snapshot counters")
            snapshot = writer.save_conversation()

            restored_stats = {key: -1 for key in self.SESSION_STATS}
            reader = self._manager(root)
            reader.bind_session_stats(restored_stats)

            self.assertTrue(reader.load_conversation(snapshot))
            self.assertEqual(restored_stats, self.SESSION_STATS)

    def test_load_legacy_session_without_stats_restores_zero_defaults(self):
        """Old saved sessions remain loadable and clear stale counters."""
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            legacy = {
                "session_id": "legacy",
                "metadata": {},
                "messages": [],
                "message_index": {},
                "context_window": [],
                "current_parent_uuid": None,
            }
            (root / "legacy.jsonl").write_text(json.dumps(legacy) + "\n")

            restored_stats = {key: 999 for key in self.SESSION_STATS}
            reader = self._manager(root)
            reader.bind_session_stats(restored_stats)

            self.assertTrue(reader.load_session("legacy"))
            self.assertEqual(
                restored_stats,
                {key: 0 for key in self.SESSION_STATS},
            )

    def test_load_session_with_malformed_stats_restores_zero_defaults(self):
        """A malformed stats payload does not break legacy session loading."""
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            malformed = {
                "session_id": "malformed",
                "metadata": {},
                "messages": [],
                "message_index": {},
                "context_window": [],
                "current_parent_uuid": None,
                "session_stats": "not-a-mapping",
            }
            (root / "malformed.jsonl").write_text(json.dumps(malformed) + "\n")

            restored_stats = {key: 999 for key in self.SESSION_STATS}
            reader = self._manager(root)
            reader.bind_session_stats(restored_stats)

            self.assertTrue(reader.load_session("malformed"))
            self.assertEqual(
                restored_stats,
                {key: 0 for key in self.SESSION_STATS},
            )

    def test_load_session_sanitizes_malformed_counter_values(self):
        """Malformed individual counters cannot poison later accumulation."""
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            malformed = {
                "session_id": "malformed-counters",
                "metadata": {},
                "messages": [],
                "message_index": {},
                "context_window": [],
                "current_parent_uuid": None,
                "session_stats": {
                    "messages": 3,
                    "input_tokens": "101",
                    "output_tokens": None,
                    "total_input_tokens": 303,
                    "total_output_tokens": -1,
                    "cache_read_tokens": 1.5,
                    "cache_creation_tokens": True,
                    "total_cache_read_tokens": [],
                    "total_cache_creation_tokens": 808,
                    "unknown_counter": 999,
                },
            }
            (root / "malformed-counters.jsonl").write_text(json.dumps(malformed) + "\n")

            restored_stats = {key: 999 for key in self.SESSION_STATS}
            reader = self._manager(root)
            reader.bind_session_stats(restored_stats)

            self.assertTrue(reader.load_session("malformed-counters"))
            self.assertEqual(
                restored_stats,
                {
                    "messages": 3,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_input_tokens": 303,
                    "total_output_tokens": 0,
                    "cache_read_tokens": 0,
                    "cache_creation_tokens": 0,
                    "total_cache_read_tokens": 0,
                    "total_cache_creation_tokens": 808,
                },
            )
            restored_stats["total_input_tokens"] += restored_stats["input_tokens"]
            restored_stats["total_cache_read_tokens"] += restored_stats[
                "cache_read_tokens"
            ]

    def test_invalid_json_only_session_does_not_mutate_live_state(self):
        """An unrecognized session fails without clearing the current conversation."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "invalid.jsonl").write_text("{not-json}\n")

            reader = self._manager(root)
            reader.current_session_id = "current"
            reader.add_message("user", "keep this message")
            original_messages = list(reader.messages)
            original_stats = dict(self.SESSION_STATS)
            reader.bind_session_stats(original_stats)

            self.assertFalse(reader.load_session("invalid"))
            self.assertEqual(reader.current_session_id, "current")
            self.assertEqual(reader.messages, original_messages)
            self.assertEqual(original_stats, self.SESSION_STATS)

    def test_metadata_only_streaming_session_remains_loadable(self):
        """A recognized empty streaming session is valid legacy state."""
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            metadata = {
                "type": "conversation_metadata",
                "startTime": "2026-08-14T22:00:00Z",
                "cwd": str(root),
                "gitBranch": "main",
            }
            (root / "metadata-only.jsonl").write_text(json.dumps(metadata) + "\n")

            restored_stats = {key: 999 for key in self.SESSION_STATS}
            reader = self._manager(root)
            reader.bind_session_stats(restored_stats)

            self.assertTrue(reader.load_session("metadata-only"))
            self.assertEqual(reader.messages, [])
            self.assertEqual(
                restored_stats,
                {key: 0 for key in self.SESSION_STATS},
            )

    def test_streaming_session_uses_matching_saved_snapshot_stats(self):
        """The normal streaming resume path reads stats from its saved sidecar."""
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            writer = self._manager(root)
            writer.current_session_id = "streamed"
            writer.bind_session_stats(dict(self.SESSION_STATS))
            writer.add_message("user", "streamed message")
            writer.save_conversation()
            streaming_record = {
                "type": "user",
                "uuid": "streamed-user",
                "timestamp": "2026-08-14T22:00:00Z",
                "message": {"role": "user", "content": "streamed message"},
            }
            (root / "streamed.jsonl").write_text(json.dumps(streaming_record) + "\n")

            restored_stats = {key: -1 for key in self.SESSION_STATS}
            reader = self._manager(root)
            reader.bind_session_stats(restored_stats)

            self.assertTrue(reader.load_session("streamed"))
            self.assertEqual(restored_stats, self.SESSION_STATS)
