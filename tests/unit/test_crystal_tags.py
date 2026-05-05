"""Tests for crystal_store new methods: normalize_crystal_id, delete_entry, update_entry."""

import shutil
import tempfile
import unittest
from pathlib import Path

from plugins.hub.crystal_store import CrystalStore, normalize_crystal_id


class TestNormalizeCrystalId(unittest.TestCase):
    def test_bare_digit_pads_to_three(self):
        self.assertEqual(normalize_crystal_id("3"), "crys-003")

    def test_bare_large_digit(self):
        self.assertEqual(normalize_crystal_id("110"), "crys-110")

    def test_full_form_passthrough(self):
        self.assertEqual(normalize_crystal_id("crys-003"), "crys-003")

    def test_full_form_large_passthrough(self):
        self.assertEqual(normalize_crystal_id("crys-110"), "crys-110")

    def test_whitespace_stripped(self):
        self.assertEqual(normalize_crystal_id("  3  "), "crys-003")

    def test_unknown_format_passthrough(self):
        self.assertEqual(normalize_crystal_id("foo"), "foo")

    def test_empty_passthrough(self):
        self.assertEqual(normalize_crystal_id(""), "")


class TestDeleteEntry(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.store = CrystalStore(self.tmpdir)
        self.e1 = self.store.add_entry(
            "First insight about routing", manual_keywords=["routing"]
        )
        self.e2 = self.store.add_entry(
            "Second insight about config", manual_keywords=["config"]
        )
        self.e3 = self.store.add_entry(
            "Third insight about TUI", manual_keywords=["tui"]
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_delete_returns_entry(self):
        removed = self.store.delete_entry(self.e2.id)
        self.assertIsNotNone(removed)
        self.assertEqual(removed.id, self.e2.id)

    def test_count_decreases(self):
        self.assertEqual(len(self.store.get_all()), 3)
        self.store.delete_entry(self.e2.id)
        self.assertEqual(len(self.store.get_all()), 2)

    def test_deleted_entry_not_found(self):
        self.store.delete_entry(self.e2.id)
        self.assertIsNone(self.store.get_by_id(self.e2.id))

    def test_remaining_entries_still_accessible(self):
        self.store.delete_entry(self.e2.id)
        self.assertIsNotNone(self.store.get_by_id(self.e1.id))
        self.assertIsNotNone(self.store.get_by_id(self.e3.id))

    def test_delete_nonexistent_returns_none(self):
        result = self.store.delete_entry("crys-999")
        self.assertIsNone(result)

    def test_bare_digit_id(self):
        removed = self.store.delete_entry("2")
        self.assertIsNotNone(removed)
        self.assertEqual(removed.id, self.e2.id)

    def test_persists_to_disk(self):
        self.store.delete_entry(self.e2.id)
        fresh = CrystalStore(self.tmpdir)
        self.assertEqual(len(fresh.get_all()), 2)
        self.assertIsNone(fresh.get_by_id(self.e2.id))


class TestUpdateEntry(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.store = CrystalStore(self.tmpdir)
        self.entry = self.store.add_entry(
            "Original body about hub messaging",
            manual_keywords=["hub", "messaging"],
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_basic_body_update(self):
        updated = self.store.update_entry(self.entry.id, "New body content here")
        self.assertIsNotNone(updated)
        self.assertEqual(updated.body, "New body content here")

    def test_keywords_none_reextracts(self):
        updated = self.store.update_entry(self.entry.id, "daemon sockets config loading")
        self.assertIsNotNone(updated)
        # keywords=None triggers re-extraction from body (stemmed)
        self.assertIn("daemon", updated.keywords)
        self.assertIn("socket", updated.keywords)

    def test_keywords_list_replaces(self):
        updated = self.store.update_entry(
            self.entry.id, "body text", keywords=["alpha", "beta"]
        )
        self.assertIsNotNone(updated)
        self.assertEqual(updated.keywords, ["alpha", "beta"])

    def test_summary_replaced(self):
        updated = self.store.update_entry(
            self.entry.id, "body", summary="new summary"
        )
        self.assertIsNotNone(updated)
        self.assertEqual(updated.summary, "new summary")

    def test_summary_none_keeps_existing(self):
        original_summary = self.entry.summary
        updated = self.store.update_entry(self.entry.id, "body", summary=None)
        self.assertIsNotNone(updated)
        self.assertEqual(updated.summary, original_summary)

    def test_nonexistent_returns_none(self):
        result = self.store.update_entry("crys-999", "body text")
        self.assertIsNone(result)

    def test_bare_digit_id(self):
        updated = self.store.update_entry("1", "updated via bare digit")
        self.assertIsNotNone(updated)
        self.assertEqual(updated.body, "updated via bare digit")

    def test_persists_to_disk(self):
        self.store.update_entry(self.entry.id, "persisted body", keywords=["persisted"])
        fresh = CrystalStore(self.tmpdir)
        loaded = fresh.get_by_id(self.entry.id)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.body, "persisted body")
        self.assertEqual(loaded.keywords, ["persisted"])


class TestInjectionContextText(unittest.TestCase):
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self.store = CrystalStore(self.tmpdir)
        self.store.add_entry(
            "Test insight about hub routing",
            manual_keywords=["hub", "routing"],
        )
        self.store.add_entry(
            "Another insight about config paths",
            manual_keywords=["config", "paths"],
        )
        self.store.add_entry(
            "Third insight about TUI rendering",
            manual_keywords=["tui", "rendering"],
        )
        self.store.add_entry(
            "Fourth insight about daemon sockets",
            manual_keywords=["daemon", "sockets"],
        )
        self.store.add_entry(
            "Fifth insight about vault dreaming",
            manual_keywords=["vault", "dreaming"],
        )

    def tearDown(self):
        shutil.rmtree(self.tmpdir)

    def test_no_legacy_vault_read_reference(self):
        ctx = self.store.get_injection_context(budget=200)
        self.assertNotIn("/hub vault read", ctx)

    def test_uses_crystal_read_reference(self):
        ctx = self.store.get_injection_context(budget=200)
        self.assertIn("crystal_read", ctx)


if __name__ == "__main__":
    unittest.main()
