"""A recorded surviving child prevents duplicate service launches."""

import json
import subprocess
import sys

import psutil


def test_supervisor_respects_surviving_child(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    from kollabor_plugins.packages import files_sha256
    from kollabor_plugins.services import supervisor_tick

    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    root.mkdir(parents=True)
    (root / "kollab-plugin.json").write_text(
        json.dumps({"services": {"worker": {"command": [sys.executable, "-c", "pass"], "autostart": True}}})
    )
    (home / "plugins.json").write_text(
        json.dumps(
            {
                "plugins": {
                    "hello": {
                        "enabled": True,
                        "enabled_path": "/usr/bin:/bin",
                        "granted": [],
                        "files_sha256": files_sha256(root),
                    }
                },
                "dev": {},
            }
        )
    )
    run = home / "plugin-data" / "hello" / "run"
    run.mkdir(parents=True)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"])
    try:
        (run / "worker.pid").write_text(
            json.dumps(
                {
                    "wrapper": {"pid": 0, "start_time": 0, "nonce": "abcd"},
                    "child": {"pid": child.pid, "pgid": 0, "start_time": psutil.Process(child.pid).create_time()},
                }
            )
        )
        launches = []
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: launches.append((a, k)))
        supervisor_tick()
        assert launches == []
    finally:
        child.terminate()
        child.wait(timeout=5)
