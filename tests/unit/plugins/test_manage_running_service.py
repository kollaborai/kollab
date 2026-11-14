"""Approved package changes stop verified services before touching files."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from kollabor_plugins import cli
from kollabor_plugins.packages import files_sha256
from tests.unit.plugins.test_plugin_update_cli import fixture


@pytest.mark.parametrize("action", ["update", "remove"])
def test_management_stops_running_service(tmp_path, monkeypatch, action):
    from kollabor_config import file_lock
    from kollabor_plugins import services

    home, root = fixture(tmp_path, monkeypatch)
    manifest_path = root / "kollab-plugin.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["services"] = {
        "worker": {"command": [sys.executable, "-c", "import time; time.sleep(60)"], "stop_timeout_s": 1}
    }
    manifest_path.write_text(json.dumps(manifest))
    registry_path = home / "plugins.json"
    registry = json.loads(registry_path.read_text())
    registry["plugins"]["hello"].update(
        {"enabled": True, "enabled_path": "/usr/bin:/bin", "files_sha256": files_sha256(root)}
    )
    registry_path.write_text(json.dumps(registry))
    paths = [Path(module.__file__).resolve().parent.parent for module in (services, file_lock)]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(map(str, paths)))
    monkeypatch.setattr("builtins.input", lambda prompt: "yes")
    wrapper = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from kollabor_plugins.services import run_service; run_service('hello','worker','abcd')",
            "abcd",
        ]
    )
    try:
        pid = home / "plugin-data" / "hello" / "run" / "worker.pid"
        deadline = time.monotonic() + 5
        while (not pid.exists() or not json.loads(pid.read_text()).get("child")) and time.monotonic() < deadline:
            time.sleep(0.02)
        assert pid.exists() and json.loads(pid.read_text()).get("child")
        assert cli.main([action, "hello"]) == 0
        wrapper.wait(timeout=5)
        assert root.exists() is (action == "update")
    finally:
        if wrapper.poll() is None:
            wrapper.kill()
            wrapper.wait(timeout=3)
