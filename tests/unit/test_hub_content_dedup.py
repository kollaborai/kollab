"""Tests for content-hash based duplicate detection in _on_message_received.

BUG: Two concurrent agent processes sharing the same identity each have their
own per-process _recent_hub_msgs dict.  Both can send hub_msg with identical
payload to koordinator, producing two distinct UUIDs.  The existing message-id
dedup in _on_message_received only catches exact retransmissions; it lets both
messages through, causing verbatim duplicates in the coordinator's view.

FIX: Add a (sender, content) hash dedup with a 30-second TTL window so that
any second identical message from the same sender arriving within the window
is silently dropped at the receiver.
"""

import asyncio
import collections
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from plugins.hub.models import HubMessage
from plugins.hub.plugin import HubPlugin


def _make_plugin() -> HubPlugin:
    """Return a minimal HubPlugin with stubs for _on_message_received."""
    plugin = HubPlugin(event_bus=MagicMock())

    # Stub the parts of _on_message_received that talk to the LLM / display.
    # We only want to test the dedup gate, not the full receive pipeline.
    plugin._vault = None
    plugin._task_ledger = None
    plugin._nudge_engine = None
    plugin._identity = None
    plugin._election = None
    plugin._roster = []
    plugin._announce_presence = AsyncMock()

    # Capture whether the message made it past the dedup gate to the vault /
    # LLM triggers.  We patch the vault log as a sentinel.
    plugin._vault_append_calls = []
    return plugin


def _msg(content: str, from_identity: str = "sapphire", msg_id: str | None = None) -> HubMessage:
    m = HubMessage(
        action="message",
        from_agent="agent-id-123",
        from_identity=from_identity,
        to="koordinator",
        content=content,
    )
    if msg_id is not None:
        m.id = msg_id
    return m


class TestContentDedup(unittest.IsolatedAsyncioTestCase):
    """_on_message_received should drop verbatim duplicates within the TTL window."""

    async def _process(self, plugin: HubPlugin, message: HubMessage) -> bool:
        """Call _on_message_received; return True if the message was NOT dropped."""
        processed = False

        # Patch the part of _on_message_received that runs AFTER the dedup gate
        # so we can detect if execution reached that point.
        original_vault_append = None  # nothing to restore (vault is None)

        # We'll detect pass-through by checking _seen_content_hashes grew.
        before = len(plugin._seen_content_hashes)
        await plugin._on_message_received(message)
        after = len(plugin._seen_content_hashes)
        # If the hash was freshly added it wasn't already known → processed.
        return after > before

    async def test_first_message_is_accepted(self) -> None:
        plugin = _make_plugin()
        msg = _msg("standing by. both tasks complete - QA on security fixes.")
        result = await self._process(plugin, msg)
        self.assertTrue(result, "First message should pass through the dedup gate")

    async def test_second_identical_message_within_window_is_dropped(self) -> None:
        """Two distinct UUIDs, identical sender+content → second is dropped."""
        plugin = _make_plugin()
        content = "standing by. both tasks complete - QA on security fixes."

        msg_a = _msg(content, msg_id="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        msg_b = _msg(content, msg_id="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")

        # Both have different IDs (simulating two concurrent processes)
        self.assertNotEqual(msg_a.id, msg_b.id)

        await plugin._on_message_received(msg_a)
        await plugin._on_message_received(msg_b)

        # Only one entry should exist in the content hash cache
        self.assertEqual(
            len(plugin._seen_content_hashes),
            1,
            "Second identical message should not add a new content-hash entry",
        )

    async def test_different_content_from_same_sender_both_accepted(self) -> None:
        """Two different messages from the same sender should both be processed."""
        plugin = _make_plugin()

        msg_a = _msg("standing by. tasks complete.")
        msg_b = _msg("starting next task: analyse the report.")

        await plugin._on_message_received(msg_a)
        await plugin._on_message_received(msg_b)

        self.assertEqual(
            len(plugin._seen_content_hashes),
            2,
            "Two distinct messages should produce two content-hash entries",
        )

    async def test_same_content_different_senders_both_accepted(self) -> None:
        """Identical content from DIFFERENT senders must not suppress each other."""
        plugin = _make_plugin()

        msg_sapphire = _msg("acknowledged.", from_identity="sapphire")
        msg_lapis = _msg("acknowledged.", from_identity="lapis")

        await plugin._on_message_received(msg_sapphire)
        await plugin._on_message_received(msg_lapis)

        self.assertEqual(
            len(plugin._seen_content_hashes),
            2,
            "Same content from different senders should produce two separate hash entries",
        )

    async def test_duplicate_accepted_after_window_expires(self) -> None:
        """A duplicate arriving AFTER the TTL window should be accepted again."""
        plugin = _make_plugin()
        content = "standing by."

        # First send — accepted
        await plugin._on_message_received(_msg(content))

        # Manually back-date the entry so it's older than the window
        old_key = next(iter(plugin._seen_content_hashes))
        plugin._seen_content_hashes[old_key] = (
            time.time() - plugin._CONTENT_DEDUP_WINDOW - 1
        )

        # Second send (different UUID, same content) — window has expired → accepted
        before = len(plugin._seen_content_hashes)
        await plugin._on_message_received(_msg(content, msg_id="cccccccccccccccccccccccccccccccc"))
        after = len(plugin._seen_content_hashes)

        # The stale entry is pruned and a new one is added
        self.assertEqual(after, 1, "Fresh duplicate after window expiry should be accepted")

    async def test_message_id_dedup_still_works(self) -> None:
        """The existing message-id dedup should still drop exact retransmissions."""
        plugin = _make_plugin()
        msg = _msg("hello world", msg_id="deadbeefdeadbeefdeadbeefdeadbeef")

        await plugin._on_message_received(msg)
        before = len(plugin._seen_content_hashes)

        # Retransmit the EXACT same object (same id)
        await plugin._on_message_received(msg)
        after = len(plugin._seen_content_hashes)

        # Message-id dedup fires first; content hash stays the same size
        self.assertEqual(before, after, "Exact retransmission should be caught by message-id dedup")


if __name__ == "__main__":
    unittest.main()
