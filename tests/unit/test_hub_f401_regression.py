from __future__ import annotations

from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
INBOX_TEST = REPO_ROOT / "tests" / "unit" / "test_hub_durable_inbox.py"


def test_hub_durable_inbox_tests_have_no_f401_unused_imports() -> None:
    """Keep F401 unused imports out of the durable inbox tests."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--select",
            "F401",
            str(INBOX_TEST),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
