"""The CI smoke captures early detached-child errors without changing other processes."""

import os
import subprocess
import sys

from scripts.windows_halt_smoke import enable_child_diagnostics


def test_only_detached_child_stderr_is_captured(tmp_path):
    env = dict(os.environ)
    env.pop("KOLLAB_DETACHED_CHILD", None)
    log = enable_child_diagnostics(tmp_path, env)
    code = "import sys; print('synthetic startup error', file=sys.stderr)"
    normal = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True)
    assert "synthetic startup error" in normal.stderr and not log.exists()
    child = subprocess.run(
        [sys.executable, "-c", code],
        env={**env, "KOLLAB_DETACHED_CHILD": "1"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert child.stderr == ""
    assert "synthetic startup error" in log.read_text(encoding="utf-8")
