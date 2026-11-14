"""Detaching a dev package stops its verified background wrapper."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from kollabor_config import file_lock
from kollabor_plugins import cli, services


def test_detach_stops_dev_service(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: "yes")
    home = tmp_path / ".kollab"
    source = tmp_path / "source"
    source.mkdir()
    (source / "kollab-plugin.json").write_text(
        json.dumps(
            {
                "id": "hello",
                "name": "Hello",
                "uses": [],
                "services": {
                    "worker": {"command": [sys.executable, "-c", "import time; time.sleep(60)"], "stop_timeout_s": 1}
                },
            }
        )
    )
    assert cli.main(["dev", str(source)]) == 0
    paths = [Path(module.__file__).resolve().parent.parent for module in (services, file_lock)]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(map(str, paths)))
    wrapper = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from kollabor_plugins.services import run_service; run_service('hello@dev','worker','abcd')",
            "abcd",
        ]
    )
    try:
        pid = home / "plugin-data/hello@dev/run/worker.pid"
        deadline = time.monotonic() + 8
        while (not pid.exists() or not json.loads(pid.read_text()).get("child")) and time.monotonic() < deadline:
            if wrapper.poll() is not None:
                break
            time.sleep(0.02)
        assert pid.exists() and json.loads(pid.read_text()).get("child")
        assert cli.main(["dev", "off", "hello"]) == 0
        wrapper.wait(timeout=5)
        assert "hello" not in json.loads((home / "plugins.json").read_text())["dev"]
    finally:
        if wrapper.poll() is None:
            wrapper.kill()
            wrapper.wait(timeout=3)
