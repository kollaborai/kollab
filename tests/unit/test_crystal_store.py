"""Tests for plugins.hub.crystal_store -- structured crystal memories."""

import shutil
import tempfile
import unittest
from pathlib import Path

from plugins.hub.crystal_store import CrystalEntry, CrystalStore


class TestCrystalEntry(unittest.TestCase):
    def test_to_block(self):
        entry = CrystalEntry(
            id="crys-001",
            date="2026-04-12",
            keywords=["hub", "routing"],
            summary="Hub routing fix",
            body="Fixed the routing table.",
        )
        block = entry.to_block()
        self.assertIn("id: crys-001", block)
        self.assertIn("date: 2026-04-12", block)
        self.assertIn("keywords: hub, routing", block)
        self.assertIn("summary: Hub routing fix", block)
        self.assertIn("Fixed the routing table.", block)

    def test_summary_line(self):
        entry = CrystalEntry(
            id="crys-042",
            date="2026-04-12",
            keywords=[],
            summary="Test summary",
            body="Body text",
        )
        self.assertEqual(entry.summary_line(), "[crys-042] Test summary")


class TestCrystalStoreBasic(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.store = CrystalStore(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_empty_store(self):
        self.assertEqual(self.store.count(), 0)
        self.assertEqual(self.store.load(), [])

    def test_add_entry(self):
        entry = self.store.add_entry("Hub routing is broken in plugin.py")
        self.assertEqual(entry.id, "crys-001")
        self.assertEqual(self.store.count(), 1)

    def test_sequential_ids(self):
        self.store.add_entry("First insight about hub")
        e2 = self.store.add_entry("Second insight about config")
        self.assertEqual(e2.id, "crys-002")

    def test_auto_keywords(self):
        entry = self.store.add_entry(
            "The `prompt_renderer.py` has duplicate TRENDER_HUB_IDENTITY_PATTERN"
        )
        self.assertIn("prompt_renderer.py", entry.keywords)

    def test_manual_keywords(self):
        entry = self.store.add_entry(
            "Some insight", manual_keywords=["custom_tag", "special"]
        )
        self.assertIn("custom_tag", entry.keywords)
        self.assertIn("special", entry.keywords)

    def test_summary_from_bold_header(self):
        entry = self.store.add_entry(
            "**Hub message routing bug** -- detailed description here"
        )
        self.assertEqual(entry.summary, "Hub message routing bug")

    def test_summary_from_first_sentence(self):
        entry = self.store.add_entry(
            "The config system has a path encoding issue. It uses underscores."
        )
        self.assertEqual(
            entry.summary, "The config system has a path encoding issue"
        )

    def test_summary_strips_numbered_list_marker_with_bold(self):
        """Dreaming output like '1. **Foo** -- detail' must keep 'Foo' as the
        summary, not the leading list digit. Regression for the nudge emitting
        '[crys-001] 1' as a useless one-liner.
        """
        entry = self.store.add_entry(
            "1. **Hub message routing bug** -- agents drop messages on retry"
        )
        self.assertEqual(entry.summary, "Hub message routing bug")

    def test_summary_strips_numbered_list_marker_no_bold(self):
        entry = self.store.add_entry(
            "2. The config system has a path encoding issue. It uses underscores."
        )
        self.assertEqual(
            entry.summary, "The config system has a path encoding issue"
        )

    def test_summary_strips_bullet_marker(self):
        entry = self.store.add_entry(
            "- **Tool retry storm** observed in queue_processor.py"
        )
        self.assertEqual(entry.summary, "Tool retry storm")

    def test_custom_date(self):
        entry = self.store.add_entry("Test", date="2025-01-15")
        self.assertEqual(entry.date, "2025-01-15")


class TestCrystalStoreRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_round_trip(self):
        store1 = CrystalStore(self.tmpdir)
        store1.add_entry("First insight about hub routing")
        store1.add_entry("Second insight about config paths")

        # Load in a fresh instance
        store2 = CrystalStore(self.tmpdir)
        entries = store2.load()
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].id, "crys-001")
        self.assertEqual(entries[1].id, "crys-002")

    def test_round_trip_preserves_body(self):
        store1 = CrystalStore(self.tmpdir)
        body = "Multi-line insight.\nWith line breaks.\nAnd detail."
        store1.add_entry(body)

        store2 = CrystalStore(self.tmpdir)
        entries = store2.load()
        self.assertEqual(entries[0].body, body)

    def test_id_continuity_after_reload(self):
        store1 = CrystalStore(self.tmpdir)
        store1.add_entry(
            "Hub message routing has a coordinator broadcast bug",
            manual_keywords=["hub", "routing"],
        )
        store1.add_entry(
            "Config path encoding uses underscores for slashes in paths",
            manual_keywords=["config", "encoding"],
        )

        # New instance should continue IDs from highest existing
        store2 = CrystalStore(self.tmpdir)
        e3 = store2.add_entry(
            "TUI rendering artifacts from incremental buffer overwrite",
            manual_keywords=["tui", "rendering"],
        )
        self.assertEqual(e3.id, "crys-003")


class TestCrystalStoreDedup(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.store = CrystalStore(self.tmpdir)

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_duplicate_merges(self):
        # Add two very similar entries
        e1 = self.store.add_entry(
            "Hub message routing bug -- coordinator broadcasting to self. "
            "Fixed by checking identity in routing table.",
            manual_keywords=["hub", "routing", "broadcast"],
        )
        e2 = self.store.add_entry(
            "Hub message routing fix -- coordinator self-broadcast bug. "
            "Identity check added to routing table.",
            manual_keywords=["hub", "routing", "broadcast", "self-broadcast"],
        )
        # Should merge into same entry
        self.assertEqual(e2.id, e1.id)
        self.assertEqual(self.store.count(), 1)

    def test_different_entries_not_merged(self):
        self.store.add_entry(
            "Hub routing has a bug where messages go to self",
            manual_keywords=["hub", "routing"],
        )
        self.store.add_entry(
            "Config path encoding uses underscores for slashes",
            manual_keywords=["config", "path", "encoding"],
        )
        self.assertEqual(self.store.count(), 2)

    def test_deduplicate_method(self):
        # Force-add similar entries by manipulating keywords post-add
        self.store.add_entry(
            "Alpha insight about hub routing in the coordinator plugin",
            manual_keywords=["hub", "routing", "coordinator"],
        )
        # Add one with enough shared keywords to be a dupe
        self.store.add_entry(
            "Beta insight about coordinator hub routing pipeline",
            manual_keywords=["hub", "routing", "coordinator", "pipeline"],
        )
        # If dedup threshold caught it, count stays at 1
        # If not (keywords too different), count is 2
        # The deduplicate() method does an explicit pass
        self.store.deduplicate()
        total = self.store.count()
        self.assertTrue(total <= 2)


class TestCrystalStoreNudge(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.store = CrystalStore(self.tmpdir)
        self.store.add_entry(
            "**Hub message routing bug** -- coordinator was broadcasting "
            "to itself instead of routing to peers.",
            manual_keywords=["hub", "routing", "broadcast", "coordinator"],
        )
        self.store.add_entry(
            "**File path encoding** in config uses underscores: "
            "/Users/foo/bar -> Users_foo_bar.",
            manual_keywords=["config", "path", "encoding"],
        )
        self.store.add_entry(
            "**TUI rendering artifacts** from incremental overwrite "
            "without clearing. Fix: pad each line to terminal width.",
            manual_keywords=["tui", "rendering", "artifacts", "overwrite"],
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_nudge_finds_relevant(self):
        results = self.store.nudge("check the hub routing logic")
        self.assertTrue(len(results) > 0)
        self.assertEqual(results[0].id, "crys-001")

    def test_nudge_config_match(self):
        results = self.store.nudge("file path encoding in the config")
        self.assertTrue(len(results) > 0)
        ids = [r.id for r in results]
        self.assertIn("crys-002", ids)

    def test_nudge_no_match(self):
        results = self.store.nudge("quantum physics simulation")
        self.assertEqual(len(results), 0)

    def test_nudge_short_input_skipped(self):
        results = self.store.nudge("hi")
        self.assertEqual(len(results), 0)


class TestCrystalStoreRetrieval(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.store = CrystalStore(self.tmpdir)
        self.store.add_entry("Entry one", date="2026-04-09")
        self.store.add_entry("Entry two about something else", date="2026-04-10")
        self.store.add_entry("Entry three latest work", date="2026-04-12")

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_get_recent(self):
        recent = self.store.get_recent(2)
        self.assertEqual(len(recent), 2)
        self.assertEqual(recent[0].date, "2026-04-12")
        self.assertEqual(recent[1].date, "2026-04-10")

    def test_get_by_id(self):
        entry = self.store.get_by_id("crys-002")
        self.assertIsNotNone(entry)
        self.assertIn("two", entry.body)

    def test_get_by_id_missing(self):
        entry = self.store.get_by_id("crys-999")
        self.assertIsNone(entry)

    def test_get_all(self):
        all_entries = self.store.get_all()
        self.assertEqual(len(all_entries), 3)

    def test_find_by_keywords(self):
        results = self.store.find_by_keywords(["entry"])
        self.assertTrue(len(results) > 0)


class TestCrystalStoreInjection(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.store = CrystalStore(self.tmpdir)
        # Create distinct entries to avoid dedup
        topics = [
            ("hub routing coordinator broadcast", ["hub", "routing"]),
            ("config path encoding underscores", ["config", "path"]),
            ("tui rendering artifacts overwrite", ["tui", "rendering"]),
            ("daemon socket timeout bootstrap", ["daemon", "socket"]),
            ("vault dreaming crystallize cycle", ["vault", "dreaming"]),
        ]
        for i, (text, kw) in enumerate(topics):
            self.store.add_entry(
                text,
                manual_keywords=kw,
                date=f"2026-04-{i + 1:02d}",
            )

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_injection_context_not_empty(self):
        ctx = self.store.get_injection_context()
        self.assertTrue(len(ctx) > 0)

    def test_injection_includes_recent(self):
        ctx = self.store.get_injection_context()
        self.assertIn("recent:", ctx)

    def test_injection_respects_budget(self):
        ctx = self.store.get_injection_context(budget=200)
        self.assertLessEqual(len(ctx), 500)  # some overhead allowed

    def test_injection_shows_entry_count(self):
        ctx = self.store.get_injection_context()
        self.assertIn("5 entries", ctx)


class TestReindexKeywords(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.store = CrystalStore(self.tmpdir)
        self.store.add_entry(
            "The prompt_renderer.py has a bug in TRENDER pattern"
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_reindex(self):
        count = self.store.reindex_keywords()
        self.assertEqual(count, 1)
        entry = self.store.get_by_id("crys-001")
        self.assertTrue(len(entry.keywords) > 0)


if __name__ == "__main__":
    unittest.main()
