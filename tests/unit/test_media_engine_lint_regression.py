"""Keep the image media engine free of F401 and F811 regressions."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MEDIA_ENGINE_PYTHON_FILES = (
    "packages/kollabor-engine/src/kollabor_engine/routes/messages.py",
    "packages/kollabor-engine/src/kollabor_engine/routes/sessions.py",
    "packages/kollabor-engine/src/kollabor_engine/server.py",
    "packages/kollabor-engine/tests/test_media_routes.py",
    "packages/kollabor-engine/tests/test_media_store.py",
)


def test_media_engine_has_no_f401_or_f811() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--select",
            "F401,F811",
            *MEDIA_ENGINE_PYTHON_FILES,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
