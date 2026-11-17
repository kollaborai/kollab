"""Disabling a package must stop its already-running service wrapper."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path


def test_disable_stops_live_service(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    from kollabor_config import file_lock
    from kollabor_plugins import services
    from kollabor_plugins.packages import files_sha256
    from kollabor_plugins.settings import set_enabled

    paths = [Path(module.__file__).resolve().parent.parent for module in (services, file_lock)]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(map(str, paths)))
    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    root.mkdir(parents=True)
    (root / "kollab-plugin.json").write_text(
        json.dumps(
            {
                "services": {
                    "worker": {"command": [sys.executable, "-c", "import time; time.sleep(60)"], "stop_timeout_s": 0.2}
                }
            }
        )
    )
    (home / "plugins.json").write_text(
        json.dumps(
            {
                "plugins": {
                    "hello": {"enabled": True, "enabled_path": "/usr/bin:/bin", "files_sha256": files_sha256(root)}
                },
                "dev": {},
            }
        )
    )
    wrapper = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from kollabor_plugins.services import run_service; run_service('hello','worker','abcd')",
            "abcd",
        ]
    )
    try:
        state_file = home / "plugin-data" / "hello" / "run" / "worker.state"
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if state_file.exists() and json.loads(state_file.read_text()).get("status") == "running":
                break
            time.sleep(0.02)
        assert state_file.exists() and json.loads(state_file.read_text())["status"] == "running"
        set_enabled("hello", False)
        wrapper.wait(timeout=3)
        assert json.loads(state_file.read_text())["status"] == "stopped"
        assert json.loads((home / "plugins.json").read_text())["plugins"]["hello"]["enabled"] is False
    finally:
        if wrapper.poll() is None:
            wrapper.terminate()
            wrapper.wait(timeout=4)
