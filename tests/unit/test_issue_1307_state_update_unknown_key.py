"""Regression coverage for unknown Hub session state update keys."""

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from plugins.hub.plugin import HubPlugin
from plugins.hub.session_state import SessionStateManager


class TestStateUpdateUnknownKey(unittest.TestCase):
    def test_tool_rejects_unknown_key_without_reporting_success(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            vault_dir = Path(temporary_directory)
            manager = SessionStateManager()
            manager.update_state(vault_dir, {"focus_file": "existing.py"})
            plugin = object.__new__(HubPlugin)
            plugin._vault = SimpleNamespace(_vault_dir=vault_dir)
            plugin._session_state_mgr = manager

            result = asyncio.run(
                plugin._handle_state_update_tool(
                    {
                        "id": "unknown-key",
                        "json_str": '{"focsu_file":"typo.py"}',
                    }
                )
            )

            self.assertFalse(result.success)
            self.assertIn("unknown", result.error.lower())
            self.assertEqual(manager.load_state(vault_dir).focus_file, "existing.py")


if __name__ == "__main__":
    unittest.main()
