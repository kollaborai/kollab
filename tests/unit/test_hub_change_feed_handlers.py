"""Unit tests for hub plugin change feed pipeline handlers.

Tests the async handler methods that were migrated to the pipeline
architecture in phase 2d:
  - _handle_lane_claim_tool
  - _handle_file_changed_tool
  - _handle_feed_recent_tool
  - _handle_claims_tool
"""

import asyncio
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.append(str(Path(__file__).parent.parent.parent))

from plugins.hub.change_feed import ChangeFeed


def _make_plugin(change_feed, identity_name="sapphire"):
    """Create a minimal HubPlugin-like object with just the handler methods.

    We avoid instantiating the full HubPlugin (heavy deps) by building a
    lightweight mock that has the same attributes the handlers read.
    """
    # Import the class so we can grab unbound methods
    from plugins.hub.plugin import HubPlugin

    plugin = object.__new__(HubPlugin)

    # Mock identity
    mock_identity = MagicMock()
    mock_identity.identity = identity_name
    mock_identity.agent_id = f"{identity_name}-agent-001"
    plugin._identity = mock_identity

    # Real ChangeFeed instance
    plugin._change_feed = change_feed

    # Presence manager — not needed for most tests, set to None
    plugin._presence = None

    return plugin


def _run(coro):
    """Run an async coroutine synchronously for testing.

    Uses a fresh event loop per call to avoid conflicts with the main
    test suite's event loop management (RuntimeError on get_event_loop).
    """
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ================================================================== #
#  _handle_lane_claim_tool
# ================================================================== #


class TestHandleLaneClaim(unittest.TestCase):
    """Tests for _handle_lane_claim_tool handler."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.feed = ChangeFeed(hub_dir=self.tmpdir)
        self.plugin = _make_plugin(self.feed, "sapphire")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_normal_claim(self):
        result = _run(self.plugin._handle_lane_claim_tool({
            "id": "test-1",
            "path": "core/app.py",
            "task_desc": "refactoring",
        }))
        self.assertTrue(result.success)
        self.assertEqual(result.tool_type, "lane_claim")
        self.assertIn("claimed: core/app.py", result.output)

    def test_conflict(self):
        # First claim by sapphire
        self.feed.claim("sapphire", "core/app.py", "refactoring")

        # Second claim by lapis
        plugin2 = _make_plugin(self.feed, "lapis")
        result = _run(plugin2._handle_lane_claim_tool({
            "id": "test-2",
            "path": "core/app.py",
            "task_desc": "bugfix",
        }))
        self.assertTrue(result.success)
        self.assertIn("CONFLICT", result.output)
        self.assertIn("sapphire", result.output)

    def test_missing_change_feed(self):
        plugin = _make_plugin(self.feed, "sapphire")
        plugin._change_feed = None
        result = _run(plugin._handle_lane_claim_tool({
            "id": "test-3",
            "path": "core/app.py",
        }))
        self.assertFalse(result.success)
        self.assertIn("change feed not initialized", result.error)

    def test_missing_identity(self):
        plugin = _make_plugin(self.feed, "sapphire")
        plugin._identity = None
        result = _run(plugin._handle_lane_claim_tool({
            "id": "test-4",
            "path": "core/app.py",
        }))
        self.assertFalse(result.success)
        self.assertIn("change feed not initialized", result.error)

    def test_empty_path(self):
        result = _run(self.plugin._handle_lane_claim_tool({
            "id": "test-5",
            "path": "",
            "task_desc": "",
        }))
        # ChangeFeed.claim handles empty path gracefully
        self.assertTrue(result.success)

    def test_same_identity_reclaim(self):
        self.feed.claim("sapphire", "core/app.py", "first pass")
        result = _run(self.plugin._handle_lane_claim_tool({
            "id": "test-6",
            "path": "core/app.py",
            "task_desc": "second pass",
        }))
        self.assertTrue(result.success)
        self.assertIn("claimed: core/app.py", result.output)

    def test_tool_id_propagated(self):
        result = _run(self.plugin._handle_lane_claim_tool({
            "id": "my-custom-id",
            "path": "core/app.py",
        }))
        self.assertEqual(result.tool_id, "my-custom-id")

    def test_default_tool_id(self):
        result = _run(self.plugin._handle_lane_claim_tool({
            "path": "core/app.py",
        }))
        self.assertEqual(result.tool_id, "unknown")


# ================================================================== #
#  _handle_file_changed_tool
# ================================================================== #


class TestHandleFileChanged(unittest.TestCase):
    """Tests for _handle_file_changed_tool handler."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.feed = ChangeFeed(hub_dir=self.tmpdir)
        self.plugin = _make_plugin(self.feed, "sapphire")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_normal_record(self):
        result = _run(self.plugin._handle_file_changed_tool({
            "id": "test-1",
            "path": "plugins/hub/vault.py",
        }))
        self.assertTrue(result.success)
        self.assertEqual(result.tool_type, "file_changed")
        self.assertIn("recorded: plugins/hub/vault.py", result.output)

    def test_missing_change_feed(self):
        self.plugin._change_feed = None
        result = _run(self.plugin._handle_file_changed_tool({
            "id": "test-2",
            "path": "core/app.py",
        }))
        self.assertFalse(result.success)
        self.assertIn("change feed not initialized", result.error)

    def test_missing_identity(self):
        self.plugin._identity = None
        result = _run(self.plugin._handle_file_changed_tool({
            "id": "test-3",
            "path": "core/app.py",
        }))
        self.assertFalse(result.success)

    def test_subscriber_notification(self):
        """When a subscriber exists for a path, _handle_file_changed
        should attempt to deliver a notification via _deliver_to_agent."""
        # Subscribe lapis to watch plugins/hub/*
        self.feed.subscribe("lapis", "plugins/hub/*")

        # Verify lapis is in subscribers for the changed file
        subs = self.feed.get_subscribers_for("plugins/hub/vault.py")
        self.assertIn("lapis", subs)

        # Mock presence and deliver_to_agent
        mock_presence = MagicMock()
        mock_presence.discover_agents_async = AsyncMock(return_value=[])
        self.plugin._presence = mock_presence
        self.plugin._deliver_to_agent = AsyncMock()

        result = _run(self.plugin._handle_file_changed_tool({
            "id": "test-4",
            "path": "plugins/hub/vault.py",
        }))

        self.assertTrue(result.success)
        self.assertIn("recorded", result.output)
        # discover_agents_async should have been called to find subscribers
        mock_presence.discover_agents_async.assert_called_once()

    def test_subscriber_notification_with_target(self):
        """When a subscriber agent is discoverable, deliver notification."""
        self.feed.subscribe("lapis", "plugins/hub/*")

        # Create a mock agent target
        mock_agent = MagicMock()
        mock_agent.identity = "lapis"

        mock_presence = MagicMock()
        mock_presence.discover_agents_async = AsyncMock(
            return_value=[mock_agent]
        )
        self.plugin._presence = mock_presence
        self.plugin._deliver_to_agent = AsyncMock()

        result = _run(self.plugin._handle_file_changed_tool({
            "id": "test-5",
            "path": "plugins/hub/vault.py",
        }))

        self.assertTrue(result.success)
        # Should have delivered notification to lapis
        self.plugin._deliver_to_agent.assert_called_once()
        call_args = self.plugin._deliver_to_agent.call_args
        # Second positional arg is the HubMessage
        hub_msg = call_args[0][1]
        self.assertEqual(hub_msg.to, "lapis")
        self.assertIn("sapphire", hub_msg.content)
        self.assertIn("vault.py", hub_msg.content)

    def test_no_self_notification(self):
        """Agent should not notify itself when it edits a watched file."""
        # sapphire subscribes to own changes
        self.feed.subscribe("sapphire", "plugins/hub/*")

        mock_presence = MagicMock()
        mock_presence.discover_agents_async = AsyncMock(return_value=[])
        self.plugin._presence = mock_presence
        self.plugin._deliver_to_agent = AsyncMock()

        result = _run(self.plugin._handle_file_changed_tool({
            "id": "test-6",
            "path": "plugins/hub/vault.py",
        }))

        self.assertTrue(result.success)
        # Handler fetches agents then filters self out — verify no delivery
        self.plugin._deliver_to_agent.assert_not_called()


# ================================================================== #
#  _handle_feed_recent_tool
# ================================================================== #


class TestHandleFeedRecent(unittest.TestCase):
    """Tests for _handle_feed_recent_tool handler."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.feed = ChangeFeed(hub_dir=self.tmpdir)
        self.plugin = _make_plugin(self.feed, "sapphire")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_with_entries(self):
        self.feed.record_change("sapphire", "core/app.py", "edit")
        self.feed.record_change("lapis", "core/io.py", "edit")

        result = _run(self.plugin._handle_feed_recent_tool({
            "id": "test-1",
            "limit": 10,
        }))
        self.assertTrue(result.success)
        self.assertEqual(result.tool_type, "feed_recent")
        self.assertIn("last 2 changes:", result.output)
        self.assertIn("sapphire", result.output)
        self.assertIn("lapis", result.output)
        self.assertIn("core/app.py", result.output)
        self.assertIn("core/io.py", result.output)

    def test_empty_feed(self):
        result = _run(self.plugin._handle_feed_recent_tool({
            "id": "test-2",
        }))
        self.assertTrue(result.success)
        self.assertEqual(result.output, "no changes")

    def test_limit_parameter(self):
        for i in range(5):
            self.feed.record_change("sapphire", f"file_{i}.py", "edit")

        result = _run(self.plugin._handle_feed_recent_tool({
            "id": "test-3",
            "limit": 2,
        }))
        self.assertTrue(result.success)
        self.assertIn("last 2 changes:", result.output)

    def test_missing_change_feed(self):
        self.plugin._change_feed = None
        result = _run(self.plugin._handle_feed_recent_tool({
            "id": "test-4",
        }))
        self.assertFalse(result.success)
        self.assertIn("change feed not initialized", result.error)

    def test_default_limit(self):
        self.feed.record_change("sapphire", "a.py", "edit")

        result = _run(self.plugin._handle_feed_recent_tool({
            "id": "test-5",
        }))
        self.assertTrue(result.success)
        self.assertIn("last 1 changes:", result.output)

    def test_tool_id_propagated(self):
        result = _run(self.plugin._handle_feed_recent_tool({
            "id": "custom-id-123",
        }))
        self.assertEqual(result.tool_id, "custom-id-123")


# ================================================================== #
#  _handle_claims_tool
# ================================================================== #


class TestHandleClaims(unittest.TestCase):
    """Tests for _handle_claims_tool handler."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.feed = ChangeFeed(hub_dir=self.tmpdir)
        self.plugin = _make_plugin(self.feed, "sapphire")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_with_claims(self):
        self.feed.claim("sapphire", "core/app.py", "refactoring")
        self.feed.claim("lapis", "core/io.py", "bugfix")

        result = _run(self.plugin._handle_claims_tool({
            "id": "test-1",
        }))
        self.assertTrue(result.success)
        self.assertEqual(result.tool_type, "claims")
        self.assertIn("2 active:", result.output)
        self.assertIn("sapphire", result.output)
        self.assertIn("lapis", result.output)
        self.assertIn("core/app.py", result.output)
        self.assertIn("core/io.py", result.output)

    def test_empty_claims(self):
        result = _run(self.plugin._handle_claims_tool({
            "id": "test-2",
        }))
        self.assertTrue(result.success)
        self.assertEqual(result.output, "no active claims")

    def test_filter_by_identity(self):
        self.feed.claim("sapphire", "core/app.py", "refactoring")
        self.feed.claim("lapis", "core/io.py", "bugfix")

        result = _run(self.plugin._handle_claims_tool({
            "id": "test-3",
            "target_identity": "sapphire",
        }))
        self.assertTrue(result.success)
        self.assertIn("1 active:", result.output)
        self.assertIn("core/app.py", result.output)
        self.assertNotIn("core/io.py", result.output)

    def test_empty_target_identity_falls_back_to_all(self):
        """Empty string target_identity should be treated as None (show all)."""
        self.feed.claim("sapphire", "core/app.py", "refactoring")

        result = _run(self.plugin._handle_claims_tool({
            "id": "test-4",
            "target_identity": "",
        }))
        self.assertTrue(result.success)
        self.assertIn("1 active:", result.output)

    def test_missing_change_feed(self):
        self.plugin._change_feed = None
        result = _run(self.plugin._handle_claims_tool({
            "id": "test-5",
        }))
        self.assertFalse(result.success)
        self.assertIn("change feed not initialized", result.error)

    def test_tool_id_propagated(self):
        result = _run(self.plugin._handle_claims_tool({
            "id": "claims-id-999",
        }))
        self.assertEqual(result.tool_id, "claims-id-999")

    def test_default_tool_id(self):
        result = _run(self.plugin._handle_claims_tool({}))
        self.assertEqual(result.tool_id, "unknown")


if __name__ == "__main__":
    unittest.main()
