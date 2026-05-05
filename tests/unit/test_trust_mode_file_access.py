import tempfile
import unittest
from pathlib import Path

from kollabor_agent.file_operations_executor import (
    FileOperationsExecutor,
    PathAccessMode,
)


class TestTrustModeFileAccess(unittest.TestCase):
    def test_project_mode_rejects_absolute_path_outside_project(self):
        executor = FileOperationsExecutor()
        executor.set_path_access_mode(PathAccessMode.PROJECT_ONLY)

        with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp:
            tmp.write("hello\n")
            path = tmp.name

        valid, error, _ = executor.validate_file_path(path)
        self.assertFalse(valid)
        self.assertIn("outside workspace", error)

    def test_trust_mode_allows_absolute_path_outside_project(self):
        executor = FileOperationsExecutor()
        executor.set_path_access_mode(PathAccessMode.ANYWHERE)

        with tempfile.NamedTemporaryFile(mode="w", delete=False) as tmp:
            tmp.write("hello\n")
            path = tmp.name

        valid, error, normalized = executor.validate_file_path(path)
        self.assertTrue(valid)
        self.assertEqual(error, "")
        self.assertEqual(normalized, path)

    def test_trust_mode_can_read_absolute_path_outside_project(self):
        executor = FileOperationsExecutor()
        executor.set_path_access_mode(PathAccessMode.ANYWHERE)

        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "outside.txt"
            file_path.write_text("alpha\nbeta\n", encoding="utf-8")

            result = executor.execute_operation(
                {"type": "file_read", "file": str(file_path), "offset": 0, "limit": 1}
            )

            self.assertTrue(result["success"])
            self.assertIn("alpha", result["output"])


if __name__ == "__main__":
    unittest.main()
