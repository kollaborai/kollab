"""Regression tests for bounded and durable crystal writes."""

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from plugins.hub.crystal_store import CrystalStore
from plugins.hub.plugin import MAX_CRYSTAL_BODY_CHARS, HubPlugin

MAX_VAULT_WRITE_CHARS = MAX_CRYSTAL_BODY_CHARS


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_plugin(root: Path) -> HubPlugin:
    plugin = object.__new__(HubPlugin)
    project_root = root / "project"
    project_root.mkdir()
    global_root = root / "global"
    global_root.mkdir()
    plugin._crystal_store = CrystalStore(project_root)
    plugin._global_crystal_store = CrystalStore(global_root)
    plugin._vault = None
    plugin._identity = None
    return plugin


class TestCrystalStoreWriteIntegrity(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.store = CrystalStore(self.root)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_internal_store_round_trips_bodies_larger_than_tool_limit(self):
        body = "x" * 6000
        entry = self.store.add_entry(body, manual_keywords=["internal-long-body"])

        reloaded = CrystalStore(self.root).get_by_id(entry.id)

        self.assertIsNotNone(reloaded)
        self.assertEqual(reloaded.body, body)

    def test_sliced_dedup_save_failure_restores_memory_and_file(self):
        first = self.store.add_entry(
            "Cobalt ternary spindle.", manual_keywords=["cobalt-ternary-spindle"]
        )
        second = self.store.add_entry(
            "Opal fractal meadow.", manual_keywords=["opal-fractal-meadow"]
        )
        original_file = self.store._path.read_bytes()

        def merge_one(copies, changed, targets):
            copies.pop()
            targets.append(copies[0])
            yield 1

        with patch.object(self.store, "_merge_steps", side_effect=merge_one):
            with patch("os.replace", side_effect=OSError("simulated replace failure")):
                with self.assertRaisesRegex(OSError, "simulated replace failure"):
                    _run(self.store.deduplicate_in_slices(slice_s=0))

        self.assertEqual(self.store._path.read_bytes(), original_file)
        self.assertEqual(self.store.count(), 2)
        self.assertEqual(self.store.get_by_id(first.id).body, "Cobalt ternary spindle.")
        self.assertEqual(self.store.get_by_id(second.id).body, "Opal fractal meadow.")

    def test_failed_update_restores_existing_entry(self):
        existing = self.store.add_entry(
            "Original editable insight.", manual_keywords=["original-editable-insight"]
        )
        original_file = self.store._path.read_bytes()

        with patch("os.replace", side_effect=OSError("simulated replace failure")):
            with self.assertRaisesRegex(OSError, "simulated replace failure"):
                self.store.update_entry(existing.id, "Replacement body.")

        self.assertEqual(existing.body, "Original editable insight.")
        self.assertEqual(self.store._path.read_bytes(), original_file)

    def test_atomic_replace_failure_keeps_previous_file_and_memory(self):
        original_entry = self.store.add_entry(
            "Existing saved insight.", manual_keywords=["existing-saved-insight"]
        )
        original_file = self.store._path.read_bytes()

        with patch(
            "os.replace",
            side_effect=OSError("simulated replace failure"),
        ):
            with self.assertRaisesRegex(OSError, "simulated replace failure"):
                self.store.add_entry(
                    "New unrelated insight.",
                    manual_keywords=["new-unrelated-insight"],
                )

        self.assertEqual(self.store._path.read_bytes(), original_file)
        self.assertEqual(self.store.count(), 1)
        self.assertIsNotNone(self.store.get_by_id(original_entry.id))
        self.assertEqual(list(self.root.glob(".*.tmp")), [])

    def test_first_write_readback_mismatch_leaves_no_unverified_file(self):
        real_replace = os.replace
        real_read_bytes = Path.read_bytes
        replaced = False
        corrupted_read = False

        def replace_then_mark(source, destination):
            nonlocal replaced
            result = real_replace(source, destination)
            if Path(destination) == self.store._path:
                replaced = True
            return result

        def corrupt_first_final_read(path):
            nonlocal corrupted_read
            data = real_read_bytes(path)
            if path == self.store._path and replaced and not corrupted_read:
                corrupted_read = True
                return data + b"corruption"
            return data

        with (
            patch("os.replace", side_effect=replace_then_mark),
            patch.object(Path, "read_bytes", new=corrupt_first_final_read),
        ):
            with self.assertRaisesRegex(OSError, "integrity|read-back|readback"):
                self.store.add_entry(
                    "First unverified insight.",
                    manual_keywords=["first-unverified-insight"],
                )

        self.assertTrue(corrupted_read)
        self.assertFalse(self.store._path.exists())
        self.assertEqual(self.store.count(), 0)
        self.assertEqual(list(self.root.glob(".*.tmp")), [])

    def test_readback_mismatch_is_reported_and_original_file_restored(self):
        original_entry = self.store.add_entry(
            "Existing durable insight.", manual_keywords=["existing-durable-insight"]
        )
        original_file = self.store._path.read_bytes()
        real_replace = os.replace
        real_read_bytes = Path.read_bytes
        replaced = False
        corrupted_read = False

        def replace_then_mark(source, destination):
            nonlocal replaced
            result = real_replace(source, destination)
            if Path(destination) == self.store._path:
                replaced = True
            return result

        def corrupt_first_final_read(path):
            nonlocal corrupted_read
            data = real_read_bytes(path)
            if path == self.store._path and replaced and not corrupted_read:
                corrupted_read = True
                return data + b"corruption"
            return data

        with (
            patch("os.replace", side_effect=replace_then_mark),
            patch.object(Path, "read_bytes", new=corrupt_first_final_read),
        ):
            with self.assertRaisesRegex(OSError, "integrity|read-back|readback"):
                self.store.add_entry(
                    "New durable insight.", manual_keywords=["new-durable-insight"]
                )

        self.assertTrue(corrupted_read)
        self.assertEqual(self.store._path.read_bytes(), original_file)
        self.assertEqual(self.store.count(), 1)
        self.assertIsNotNone(self.store.get_by_id(original_entry.id))
        self.assertEqual(list(self.root.glob(".*.tmp")), [])


class TestVaultWriteCharacterLimit(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.plugin = _make_plugin(self.root)

    def tearDown(self):
        self.tempdir.cleanup()

    def test_exact_limit_body_is_saved_intact(self):
        body = "x" * MAX_VAULT_WRITE_CHARS

        result = _run(
            self.plugin._handle_vault_write_tool(
                {"id": "within-limit", "content": body}
            )
        )

        self.assertTrue(result.success)
        entry = self.plugin._crystal_store.get_by_id("crys-001")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.body, body)
        reloaded = CrystalStore(self.root / "project").get_by_id(entry.id)
        self.assertIsNotNone(reloaded)
        self.assertEqual(reloaded.body, body)

    def test_over_limit_local_and_global_bodies_are_rejected_before_persistence(self):
        body = "x" * (MAX_VAULT_WRITE_CHARS + 1)
        writers = (
            (self.plugin._handle_vault_write_tool, self.plugin._crystal_store),
            (
                self.plugin._handle_global_vault_write_tool,
                self.plugin._global_crystal_store,
            ),
        )

        for handler, store in writers:
            with self.subTest(store=store._path.parent.name):
                result = _run(handler({"id": "over-limit", "content": body}))

                self.assertFalse(result.success)
                self.assertIn(str(MAX_VAULT_WRITE_CHARS), result.error)
                self.assertIn("No changes were saved.", result.error)
                self.assertEqual(store.count(), 0)
                self.assertFalse(store._path.exists())

    def test_failed_save_is_reported_to_write_caller(self):
        with patch("os.replace", side_effect=OSError("simulated disk failure")):
            result = _run(
                self.plugin._handle_vault_write_tool(
                    {"id": "failed-save", "content": "valid insight"}
                )
            )

        self.assertFalse(result.success)
        self.assertIn("simulated disk failure", result.error)
        self.assertEqual(self.plugin._crystal_store.count(), 0)
        self.assertFalse(self.plugin._crystal_store._path.exists())

    def test_over_limit_edit_leaves_existing_entry_unchanged(self):
        store = self.plugin._crystal_store
        existing = store.add_entry(
            "Existing editable insight.", manual_keywords=["existing-editable-insight"]
        )
        original_file = store._path.read_bytes()

        result = _run(
            self.plugin._handle_crystal_edit_tool(
                {
                    "id": "over-limit-edit",
                    "entry_id": existing.id,
                    "content": "x" * (MAX_VAULT_WRITE_CHARS + 1),
                }
            )
        )

        self.assertFalse(result.success)
        self.assertIn(str(MAX_VAULT_WRITE_CHARS), result.error)
        self.assertEqual(
            store.get_by_id(existing.id).body, "Existing editable insight."
        )
        self.assertEqual(store._path.read_bytes(), original_file)


if __name__ == "__main__":
    unittest.main()
