"""Backups must never land in the project tree, and must not survive success.

Regression guard for the 93 stale `*.bak` files that accumulated in the repo:
create_backup() wrote `<file>.bak` beside the source and nothing ever removed
it, so every successful edit left a copy behind.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from kollabor_agent.file_operations_executor import FileOperationsExecutor


class BackupLocationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.project = Path(self._tmp.name) / "project"
        self.project.mkdir()
        self.data_dir = Path(self._tmp.name) / "data"

        patcher = patch(
            "kollabor_agent.file_operations_executor.get_project_data_dir",
            return_value=self.data_dir,
        )
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._tmp.cleanup)

        self.executor = FileOperationsExecutor(workspace=self.project)

    def _write(self, name: str, content: str = "original\n") -> Path:
        path = self.project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def _project_backups(self) -> list[Path]:
        return [
            p
            for p in self.project.rglob("*")
            if p.suffix in (".bak", ".deleted") and p.is_file()
        ]

    def test_backup_lands_outside_project(self):
        target = self._write("pkg/module.py")

        backup = self.executor.create_backup(str(target))

        self.assertIsNotNone(backup)
        self.assertEqual(self._project_backups(), [])
        self.assertTrue(Path(backup).is_relative_to(self.data_dir))
        self.assertEqual(Path(backup).read_text(), "original\n")

    def test_nested_paths_do_not_collide(self):
        a = self._write("one/dup.py", "a\n")
        b = self._write("two/dup.py", "b\n")

        backup_a = self.executor.create_backup(str(a))
        backup_b = self.executor.create_backup(str(b))

        self.assertNotEqual(backup_a, backup_b)
        self.assertEqual(Path(backup_a).read_text(), "a\n")
        self.assertEqual(Path(backup_b).read_text(), "b\n")

    def test_successful_edit_leaves_no_backup(self):
        target = self._write("app.py", "value = 1\n")

        result = self.executor._execute_edit(
            {"file": str(target), "find": "value = 1", "replace": "value = 2"}
        )

        self.assertTrue(result["success"], result)
        self.assertEqual(target.read_text(), "value = 2\n")
        self.assertEqual(self._project_backups(), [])
        self.assertEqual(list(self.data_dir.rglob("*.bak")), [])

    def test_failed_edit_restores_file_keeps_backup_and_nudges(self):
        target = self._write("app.py", "value = 1\n")

        # Land invalid Python so the syntax check rejects the edit.
        result = self.executor._execute_edit(
            {"file": str(target), "find": "value = 1", "replace": "value = ("}
        )

        self.assertFalse(result["success"])
        self.assertEqual(target.read_text(), "value = 1\n")

        retained = list(self.data_dir.rglob("*.bak"))
        self.assertEqual(len(retained), 1)

        error = result["error"]
        self.assertIn("restored", error)
        self.assertIn(str(retained[0]), error)
        self.assertIn("STOP", error)

    def test_delete_keeps_recovery_copy_outside_project(self):
        target = self._write("gone.py")

        result = self.executor._execute_delete({"file": str(target)})

        self.assertTrue(result["success"], result)
        self.assertFalse(target.exists())
        self.assertEqual(self._project_backups(), [])

        recovery = list(self.data_dir.rglob("*.deleted"))
        self.assertEqual(len(recovery), 1)
        self.assertIn(str(recovery[0]), result["output"])

    def test_discard_backup_tolerates_missing_and_none(self):
        self.executor.discard_backup(None)
        self.executor.discard_backup(str(self.data_dir / "not-there.bak"))

    def test_manual_backup_writes_are_refused(self):
        self._write("app.py")

        result = self.executor.execute_operation(
            {
                "type": "file_create",
                "file": str(self.project / "app.py.bak"),
                "content": "copy\n",
            }
        )

        self.assertFalse(result["success"])
        self.assertIn("STOP", result["error"])
        self.assertFalse((self.project / "app.py.bak").exists())

    def test_manual_backup_copy_destination_is_refused(self):
        source = self._write("app.py")

        result = self.executor.execute_operation(
            {
                "type": "file_copy",
                "from": str(source),
                "to": str(self.project / "app.py.orig"),
            }
        )

        self.assertFalse(result["success"])
        self.assertFalse((self.project / "app.py.orig").exists())

    def test_normal_writes_still_allowed(self):
        result = self.executor.execute_operation(
            {
                "type": "file_create",
                "file": str(self.project / "fresh.py"),
                "content": "x = 1\n",
            }
        )

        self.assertTrue(result["success"], result)
        self.assertEqual((self.project / "fresh.py").read_text(), "x = 1\n")


if __name__ == "__main__":
    unittest.main()
