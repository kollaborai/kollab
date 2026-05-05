"""Unit tests for hub plugin native tool parameter compatibility.

These cover mismatches between canonical native tool schemas from the
registry and legacy XML extractor keys still accepted by plugin handlers.
"""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.append(str(Path(__file__).parent.parent.parent))

from plugins.hub.crystal_store import CrystalStore
from plugins.hub.plugin import HubPlugin


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_plugin(identity_name="sapphire"):
    plugin = object.__new__(HubPlugin)
    mock_identity = MagicMock()
    mock_identity.identity = identity_name
    mock_identity.agent_id = f"{identity_name}-agent-001"
    plugin._identity = mock_identity
    plugin._presence = None
    plugin._task_ledger = None
    plugin._change_feed = None
    plugin._vault = MagicMock()
    plugin._vault._vault_dir = Path(tempfile.mkdtemp())
    plugin._vault.global_vault_dir = Path(tempfile.mkdtemp())
    plugin._vault.append_stream = MagicMock()
    plugin._session_state_mgr = MagicMock()
    plugin._crystal_store = CrystalStore(plugin._vault._vault_dir)
    plugin._global_crystal_store = CrystalStore(plugin._vault.global_vault_dir)
    return plugin


class TestHubNativeParamCompat(unittest.TestCase):
    def test_state_update_accepts_canonical_state_param(self):
        plugin = _make_plugin()
        result = _run(plugin._handle_state_update_tool({"id": "tc1", "state": "working on bugfix"}))
        self.assertTrue(result.success)
        plugin._session_state_mgr.update_state.assert_called_once()
        args = plugin._session_state_mgr.update_state.call_args[0]
        self.assertEqual(args[1], {"state": "working on bugfix"})

    def test_hub_claim_does_not_use_tool_call_id_as_slot_id(self):
        plugin = _make_plugin()

        async def _fake_handle_claim_command(arg):
            return f"claim:{arg}"

        plugin._handle_claim_command = _fake_handle_claim_command
        result = _run(plugin._handle_hub_claim_tool({"id": "tool-call-123"}))
        self.assertTrue(result.success)
        self.assertEqual(result.output, "claim:")


    def test_crystal_read_does_not_use_tool_call_id_as_entry_id(self):
        plugin = _make_plugin()
        result = _run(plugin._handle_crystal_read_tool({"id": "tool-call-123"}))
        self.assertTrue(result.success)
        self.assertIn("no crystal entry ''", result.output)

    def test_task_checkpoint_does_not_use_tool_call_id_as_task_id(self):
        plugin = _make_plugin()
        plugin._task_ledger = MagicMock()
        result = _run(
            plugin._handle_task_checkpoint_tool(
                {"id": "tool-call-123", "progress": "still working"}
            )
        )
        self.assertTrue(result.success)
        plugin._task_ledger.checkpoint.assert_called_once_with("", "still working")
        self.assertEqual(result.output, "task  checkpoint saved")

    def test_crystal_read_accepts_canonical_id_param(self):
        plugin = _make_plugin()
        plugin._crystal_store.add_entry("summary text\n\nbody text", manual_keywords=["alpha"])
        result = _run(plugin._handle_crystal_read_tool({"entry_id": "crys-001"}))
        self.assertTrue(result.success)
        self.assertIn("summary text", result.output)
        self.assertIn("body text", result.output)

    def test_crystal_edit_accepts_canonical_body_and_id_params(self):
        plugin = _make_plugin()
        plugin._crystal_store.add_entry("old summary\n\nold body", manual_keywords=["alpha"])
        result = _run(
            plugin._handle_crystal_edit_tool(
                {"entry_id": "crys-001", "body": "new body", "summary": "new summary"}
            )
        )
        self.assertTrue(result.success)
        updated = plugin._crystal_store.get_by_id("crys-001")
        self.assertEqual(updated.body, "new body")
        self.assertEqual(updated.summary, "new summary")

    def test_crystal_delete_accepts_canonical_id_param(self):
        plugin = _make_plugin()
        plugin._crystal_store.add_entry("summary text\n\nbody text", manual_keywords=["alpha"])
        result = _run(plugin._handle_crystal_delete_tool({"entry_id": "crys-001"}))
        self.assertTrue(result.success)
        self.assertIsNone(plugin._crystal_store.get_by_id("crys-001"))
