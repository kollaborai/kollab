"""Regression coverage for Hub scratchpad storage failures."""

import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from plugins.hub.plugin import HubPlugin
from plugins.hub.scratchpad import SCRATCHPAD_MAX, Scratchpad


class TestHubScratchpadStorageFailures(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.vault = self.root / "vault"
        self.vault.mkdir()

    def _plugin(self, vault_dir: Path | None = None) -> HubPlugin:
        plugin = object.__new__(HubPlugin)
        plugin._scratchpad = Scratchpad(vault_dir or self.vault)
        return plugin

    async def test_missing_note_is_empty_but_read_error_is_reported(self) -> None:
        missing = self._plugin(self.root / "missing-vault")
        missing_result = await missing._handle_scratchpad_get_tool({"id": "get"})
        self.assertTrue(missing_result.success)
        self.assertEqual(missing_result.output, "(empty)")

        unreadable_vault = self.root / "unreadable-vault"
        (unreadable_vault / "scratchpad.md").mkdir(parents=True)
        unreadable = self._plugin(unreadable_vault)
        read_result = await unreadable._handle_scratchpad_get_tool({"id": "get"})
        self.assertFalse(read_result.success)
        self.assertEqual(read_result.error, "scratchpad read failed")
        self.assertNotIn(str(self.root), read_result.error)
        self.assertNotIn("(empty)", read_result.output or "")

    async def test_write_failure_is_reported_without_storage_details(self) -> None:
        plugin = self._plugin(self.root / "missing-vault")
        result = await plugin._handle_scratchpad_tool(
            {"id": "write", "content": "private note"}
        )
        self.assertFalse(result.success)
        self.assertEqual(result.error, "scratchpad write failed")
        self.assertNotIn(str(self.root), result.error)
        self.assertNotIn("private note", result.error)

    async def test_append_failure_is_reported_without_storage_details(self) -> None:
        plugin = self._plugin(self.root / "missing-vault")
        result = await plugin._handle_scratchpad_append_tool(
            {"id": "append", "content": "private note"}
        )
        self.assertFalse(result.success)
        self.assertEqual(result.error, "scratchpad append failed")
        self.assertNotIn(str(self.root), result.error)
        self.assertNotIn("private note", result.error)

    async def test_append_read_failure_preserves_saved_note(self) -> None:
        path = self.vault / "scratchpad.md"
        path.write_text("saved note", encoding="utf-8")
        original_read_text = Path.read_text

        def fail_scratchpad_read(target: Path, *args, **kwargs):
            if target == path:
                raise OSError("injected read failure")
            return original_read_text(target, *args, **kwargs)

        plugin = self._plugin()
        with patch.object(Path, "read_text", new=fail_scratchpad_read):
            result = await plugin._handle_scratchpad_append_tool(
                {"id": "append", "content": "new note"}
            )

        self.assertFalse(result.success)
        self.assertEqual(result.error, "scratchpad append failed")
        self.assertEqual(path.read_text(encoding="utf-8"), "saved note")

    async def test_clear_failure_is_reported_without_storage_details(self) -> None:
        (self.vault / "scratchpad.md").mkdir()
        plugin = self._plugin()
        result = await plugin._handle_scratchpad_clear_tool({"id": "clear"})
        self.assertFalse(result.success)
        self.assertEqual(result.error, "scratchpad clear failed")
        self.assertNotIn(str(self.root), result.error)
        self.assertTrue((self.vault / "scratchpad.md").is_dir())

    async def test_partial_write_preserves_previous_note(self) -> None:
        path = self.vault / "scratchpad.md"
        path.write_text("previous note", encoding="utf-8")

        def partially_write_then_fail(target: Path, content: str, **kwargs):
            with target.open("w", encoding=kwargs.get("encoding", "utf-8")) as f:
                f.write(content[:4])
            raise OSError("injected partial write")

        plugin = self._plugin()
        with patch.object(Path, "write_text", new=partially_write_then_fail):
            result = await plugin._handle_scratchpad_tool(
                {"id": "write", "content": "replacement note"}
            )

        self.assertEqual(path.read_text(encoding="utf-8"), "previous note")
        self.assertFalse(result.success)
        self.assertEqual(result.error, "scratchpad write failed")

    async def test_handlers_round_trip_and_clear_notes(self) -> None:
        plugin = self._plugin()
        write_result = await plugin._handle_scratchpad_tool(
            {"id": "write", "content": "first note"}
        )
        append_result = await plugin._handle_scratchpad_append_tool(
            {"id": "append", "content": "second note"}
        )
        read_result = await plugin._handle_scratchpad_get_tool({"id": "get"})

        self.assertTrue(write_result.success)
        self.assertTrue(append_result.success)
        self.assertTrue(read_result.success)
        self.assertEqual(
            read_result.output, "first note\nsecond note"
        )

        clear_result = await plugin._handle_scratchpad_clear_tool({"id": "clear"})
        self.assertTrue(clear_result.success)
        self.assertFalse((self.vault / "scratchpad.md").exists())

    async def test_size_limits_are_preserved(self) -> None:
        scratchpad = Scratchpad(self.vault)
        scratchpad.write("w" * (SCRATCHPAD_MAX + 5))
        self.assertEqual(scratchpad.get(), "w" * SCRATCHPAD_MAX)

        scratchpad.write("a" * (SCRATCHPAD_MAX - 2))
        scratchpad.append("tail")
        self.assertEqual(
            scratchpad.get(), ("a" * (SCRATCHPAD_MAX - 2) + "\ntail")[-SCRATCHPAD_MAX:]
        )

    async def test_concurrent_appends_keep_every_update(self) -> None:
        scratchpad = Scratchpad(self.vault)
        scratchpad.write("initial")
        entries = [f"entry-{index}" for index in range(12)]

        with ThreadPoolExecutor(max_workers=6) as pool:
            list(pool.map(scratchpad.append, entries))

        saved = scratchpad.get()
        self.assertTrue(saved.startswith("initial\n"))
        for entry in entries:
            self.assertIn(entry, saved)


if __name__ == "__main__":
    unittest.main()
