"""The engine token lands in KOLLAB_CONFIG_DIR when set, else in ~/.kollab.

Runs in a subprocess with its own HOME, so the import-time token path is real and
the developer's ~/.kollab is never touched.
"""

import os
import subprocess
import sys
from pathlib import Path

GENERATE = (
    "import kollabor_engine.auth as auth; "
    "print(auth.generate_token(), end='')"
)


def _run(home: Path, config_dir: str | None) -> str:
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(home)}
    if config_dir is not None:
        env["KOLLAB_CONFIG_DIR"] = config_dir
    result = subprocess.run(
        [sys.executable, "-c", GENERATE],
        env=env,
        cwd=home,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def test_token_written_under_kollab_config_dir(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    override = tmp_path / "mentiko-private"

    token = _run(home, str(override))

    assert (override / "engine.token").read_text() == token
    assert not (home / ".kollab" / "engine.token").exists()


def test_token_defaults_to_home_kollab_when_unset(tmp_path):
    home = tmp_path / "home"
    home.mkdir()

    token = _run(home, None)

    assert (home / ".kollab" / "engine.token").read_text() == token
