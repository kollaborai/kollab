"""Regression tests for crystal vault read integrity (issue #638)."""

import asyncio
import hashlib
import unittest
from types import SimpleNamespace

from plugins.hub.crystal_store import CrystalEntry
from plugins.hub.plugin import HubPlugin


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _plugin_with_store(store):
    plugin = object.__new__(HubPlugin)
    plugin._crystal_store = store
    plugin._global_crystal_store = None
    return plugin


class TestCrystalReadOutputBudget(unittest.TestCase):
    def _read(self, entry):
        store = SimpleNamespace(get_by_id=lambda _entry_id: entry)
        plugin = _plugin_with_store(store)
        return _run(plugin._handle_crystal_read_tool({"entry_id": entry.id}))

    def test_large_keyword_metadata_cannot_hide_a_valid_body(self):
        # Matches the length of the crys-342 body reported in issue #638.
        body = "COMPLETE_BODY_SENTINEL " + "durable content " * 138
        self.assertEqual(len(body), 2231)
        keywords = [f"k{index:07d}" for index in range(572)]
        keywords.extend(f"k{index:06d}" for index in range(238))
        self.assertEqual(len(keywords), 810)
        self.assertEqual(len(", ".join(keywords)), 7860)
        entry = CrystalEntry(
            id="crys-342",
            date="2026-09-26",
            keywords=keywords,
            summary="Stored body should remain visible",
            body=body,
        )

        result = self._read(entry)

        self.assertTrue(result.success, result.output)
        self.assertLessEqual(len(result.output), 3000)
        self.assertIn(body, result.output)
        self.assertIn("[metadata truncated]", result.output)

    def test_truncated_body_reports_length_and_hash(self):
        body = "large body payload " * 300
        entry = CrystalEntry(
            id="crys-999",
            date="2026-09-26",
            keywords=["integrity"],
            summary="Oversized stored body",
            body=body,
        )

        result = self._read(entry)

        self.assertTrue(result.success, result.output)
        self.assertLessEqual(len(result.output), 3000)
        self.assertIn(body[:100], result.output)
        self.assertIn(f"{len(body)} chars total", result.output)
        self.assertIn(hashlib.sha256(body.encode()).hexdigest(), result.output)
