"""Regression tests for loss-safe Hub session state persistence."""

import asyncio
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from plugins.hub.plugin import HubPlugin
from plugins.hub.session_state import SessionStateManager


class TestHubSessionStatePersistence(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.vault_dir = Path(self.tempdir.name)
        self.manager = SessionStateManager()

    def tearDown(self):
        self.tempdir.cleanup()

    def _state_path(self, vault_dir=None):
        return self.manager._state_path(vault_dir or self.vault_dir)

    def _assert_state_error(self, operation):
        with self.assertRaises(Exception) as caught:
            operation()
        self.assertEqual(type(caught.exception).__name__, "SessionStateError")
        return caught.exception

    def test_malformed_and_invalid_schema_state_is_preserved(self):
        invalid_states = (
            b"{malformed session state",
            b"[]",
            b'{"open_files":"not-a-list"}',
            b'{"saved_at":NaN}',
        )
        path = self._state_path()

        for original in invalid_states:
            with self.subTest(original=original):
                path.write_bytes(original)
                self._assert_state_error(
                    lambda: self.manager.update_state(
                        self.vault_dir, {"focus_file": "replacement.py"}
                    )
                )
                self.assertEqual(path.read_bytes(), original)

    def test_read_error_is_not_reported_as_absent(self):
        path = self._state_path()
        path.write_text(json.dumps({"focus_file": "kept.py"}), encoding="utf-8")

        with patch.object(Path, "open", side_effect=PermissionError("denied")):
            error = self._assert_state_error(
                lambda: self.manager.get_injection_prompt(self.vault_dir)
            )

        self.assertIn("not treated as empty", str(error).lower())

    def test_missing_state_and_partial_updates_survive_restart(self):
        notes = "résumé and recovery notes — " * 500
        created = self.manager.update_state(
            self.vault_dir,
            {"focus_file": "naïve.py", "investigation_notes": notes},
        )
        self.assertEqual(created.focus_file, "naïve.py")

        restarted_manager = SessionStateManager()
        updated = restarted_manager.update_state(
            self.vault_dir, {"last_command": "inspect résumé.py"}
        )
        self.assertEqual(updated.focus_file, "naïve.py")
        self.assertEqual(updated.investigation_notes, notes)
        self.assertEqual(updated.last_command, "inspect résumé.py")

        repeated = restarted_manager.update_state(
            self.vault_dir, {"last_command": "inspect résumé.py"}
        )
        self.assertEqual(repeated.last_command, "inspect résumé.py")

    def test_interrupted_atomic_update_preserves_previous_file(self):
        self.manager.update_state(self.vault_dir, {"focus_file": "kept.py"})
        path = self._state_path()
        original = path.read_bytes()

        with patch(
            "plugins.hub.session_state.write_text_atomic",
            side_effect=OSError("synthetic interrupted write"),
            create=True,
        ):
            self._assert_state_error(
                lambda: self.manager.update_state(
                    self.vault_dir, {"focus_file": "replacement.py"}
                )
            )

        self.assertEqual(path.read_bytes(), original)

    def test_concurrent_partial_updates_do_not_drop_either_writer(self):
        barrier = threading.Barrier(2)
        original_load_state = SessionStateManager.load_state

        def synchronized_load(manager, vault_dir):
            state = original_load_state(manager, vault_dir)
            barrier.wait(timeout=5)
            return state

        first_manager = SessionStateManager()
        second_manager = SessionStateManager()
        with patch.object(SessionStateManager, "load_state", synchronized_load):
            with ThreadPoolExecutor(max_workers=2) as executor:
                first = executor.submit(
                    first_manager.update_state,
                    self.vault_dir,
                    {"focus_file": "first.py"},
                )
                second = executor.submit(
                    second_manager.update_state,
                    self.vault_dir,
                    {"investigation_notes": "second writer"},
                )
                first.result(timeout=10)
                second.result(timeout=10)

        saved = SessionStateManager().load_state(self.vault_dir)
        self.assertEqual(saved.focus_file, "first.py")
        self.assertEqual(saved.investigation_notes, "second writer")

    def test_state_update_tool_reports_corrupt_and_failed_persistence(self):
        path = self._state_path()
        original = b"{broken session state"
        path.write_bytes(original)
        plugin = object.__new__(HubPlugin)
        plugin._vault = SimpleNamespace(_vault_dir=self.vault_dir)
        plugin._session_state_mgr = SessionStateManager()

        corrupt_result = asyncio.run(
            plugin._handle_state_update_tool(
                {"id": "synthetic", "json_str": '{"focus_file":"new.py"}'}
            )
        )
        self.assertFalse(corrupt_result.success)
        self.assertLessEqual(len(corrupt_result.error), 240)
        self.assertNotIn(str(self.vault_dir), corrupt_result.error)
        self.assertEqual(path.read_bytes(), original)

        missing_vault = self.vault_dir / "missing-vault"
        plugin._vault = SimpleNamespace(_vault_dir=missing_vault)
        failed_result = asyncio.run(
            plugin._handle_state_update_tool(
                {"id": "synthetic", "json_str": '{"focus_file":"new.py"}'}
            )
        )
        self.assertFalse(failed_result.success)
        self.assertTrue(failed_result.error)
        self.assertLessEqual(len(failed_result.error), 240)
        self.assertNotIn(str(missing_vault), failed_result.error)


if __name__ == "__main__":
    unittest.main()
