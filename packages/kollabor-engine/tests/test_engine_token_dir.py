"""The engine token path follows KOLLAB_CONFIG_DIR at call time, not import time.

A process that imported kollabor_engine.auth before KOLLAB_CONFIG_DIR was set
froze the token path to the shared ~/.kollab, so an isolated test engine rewrote
the operator's real token (2026-10-31 incident). These tests run in a subprocess
with its own HOME, set the env var AFTER the import (the incident shape), and
assert the shared token file is never touched.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

GENERATE = (
    "import kollabor_engine.auth as auth; print(auth.generate_token(), end='')"
)

# The incident shape: auth imported first, KOLLAB_CONFIG_DIR set afterwards.
SETENV_AFTER_IMPORT_THEN_GENERATE = (
    "import json, os, sys;"
    "home, cfg = sys.argv[1], sys.argv[2];"
    "import kollabor_engine.auth as auth;"
    "os.environ['KOLLAB_CONFIG_DIR'] = cfg;"
    "shared = os.path.join(home, '.kollab', 'engine.token');"
    "token = auth.generate_token();"
    "print(json.dumps({'token': token, 'mtime': os.stat(shared).st_mtime}))"
)

SETENV_AFTER_IMPORT_THEN_READ = (
    "import os, sys;"
    "cfg = sys.argv[1];"
    "import kollabor_engine.auth as auth;"
    "os.environ['KOLLAB_CONFIG_DIR'] = cfg;"
    "seen = auth.read_token_from_disk();"
    "assert seen == 'iso-token', seen;"
    "assert auth.validate_token('iso-token') is True;"
    "print('ok')"
)


def _run(snippet: str, home: Path, config_dir: str | None, *args: str) -> str:
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(home),
        # the subprocess must import the same kollabor_engine under test
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
    }
    if config_dir is not None:
        env["KOLLAB_CONFIG_DIR"] = config_dir
    result = subprocess.run(
        [sys.executable, "-c", snippet, *args],
        env=env,
        cwd=home,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"snippet failed:\n{result.stderr}")
    return result.stdout


def test_token_written_under_kollab_config_dir(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    override = tmp_path / "mentiko-private"

    token = _run(GENERATE, home, str(override))

    assert (override / "engine.token").read_text() == token
    assert not (home / ".kollab" / "engine.token").exists()


def test_token_defaults_to_home_kollab_when_unset(tmp_path):
    home = tmp_path / "home"
    home.mkdir()

    token = _run(GENERATE, home, None)

    assert (home / ".kollab" / "engine.token").read_text() == token


def test_isolated_engine_never_touches_the_shared_home_token(tmp_path):
    """Env set after import must still isolate: the shared token keeps its bytes
    and its mtime (mtime compare proves no write, not just no truncate)."""
    home = tmp_path / "home"
    (home / ".kollab").mkdir(parents=True)
    shared = home / ".kollab" / "engine.token"
    shared.write_text("real-operator-token")
    old_mtime = 1_000_000_000
    os.utime(shared, (old_mtime, old_mtime))
    override = tmp_path / "isolated"

    result = json.loads(
        _run(SETENV_AFTER_IMPORT_THEN_GENERATE, home, None, str(home), str(override))
    )

    assert (override / "engine.token").read_text() == result["token"]
    assert shared.read_text() == "real-operator-token"
    assert os.stat(shared).st_mtime == old_mtime


def test_read_and_validate_follow_config_dir_set_after_import(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    override = tmp_path / "isolated"
    override.mkdir()
    (override / "engine.token").write_text("iso-token")

    out = _run(SETENV_AFTER_IMPORT_THEN_READ, home, None, str(override))

    assert out.strip() == "ok"
