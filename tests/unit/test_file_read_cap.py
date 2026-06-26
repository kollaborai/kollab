"""Read-output cap regression tests.

A full dump of a large file blows the model context window — the next API call
comes back empty (stop_reason=model_context_window_exceeded) and the session is
bricked. FileOperationsExecutor caps the returned text and tells the agent which
commands to use to fetch the rest surgically.
"""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from kollabor_agent.file_operations_executor import (
    FileOperationsExecutor,
    PathAccessMode,
)

TRUNCATION_MARKER = "truncated — showing"


def _executor(config=None) -> FileOperationsExecutor:
    ex = FileOperationsExecutor(config=config)
    ex.set_path_access_mode(PathAccessMode.ANYWHERE)
    return ex


class TestFileReadCap(unittest.TestCase):
    def test_large_full_read_is_capped_with_guidance(self):
        ex = _executor()
        with TemporaryDirectory() as d:
            f = Path(d) / "big.py"
            f.write_text("\n".join(f"line {i}" for i in range(5000)), "utf-8")

            out = ex.execute_operation({"type": "file_read", "file": str(f)})["output"]

            self.assertIn(TRUNCATION_MARKER, out)
            # capped well under the on-disk content
            self.assertLessEqual(len(out), ex.max_read_output_chars + 1000)
            # the agent is told exactly how to continue (tool-form-neutral)
            self.assertIn("offset=", out)
            self.assertIn("limit=", out)
            self.assertIn("grep", out)
            # the tail of the file did not make it into context
            self.assertNotIn("line 4999", out)

    def test_small_file_not_capped(self):
        ex = _executor()
        with TemporaryDirectory() as d:
            f = Path(d) / "small.py"
            f.write_text("alpha\nbeta\ngamma\n", "utf-8")

            out = ex.execute_operation({"type": "file_read", "file": str(f)})["output"]

            self.assertNotIn(TRUNCATION_MARKER, out)
            self.assertIn("gamma", out)

    def test_bounded_offset_limit_not_capped(self):
        ex = _executor()
        with TemporaryDirectory() as d:
            f = Path(d) / "big.py"
            f.write_text("\n".join(f"line {i}" for i in range(5000)), "utf-8")

            out = ex.execute_operation(
                {"type": "file_read", "file": str(f), "offset": 0, "limit": 50}
            )["output"]

            self.assertNotIn(TRUNCATION_MARKER, out)
            self.assertIn("line 0", out)

    def test_cap_can_be_disabled_via_config(self):
        ex = _executor(
            config={
                "file_operations.max_read_lines": 0,
                "file_operations.max_read_output_chars": 0,
            }
        )
        with TemporaryDirectory() as d:
            f = Path(d) / "big.py"
            f.write_text("\n".join(f"line {i}" for i in range(5000)), "utf-8")

            out = ex.execute_operation({"type": "file_read", "file": str(f)})["output"]

            self.assertNotIn(TRUNCATION_MARKER, out)
            self.assertIn("line 4999", out)


if __name__ == "__main__":
    unittest.main()
