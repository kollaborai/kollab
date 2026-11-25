"""Exercise the real service wrapper through the engine's HTTP handlers."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

from fastapi.testclient import TestClient


def test_disable_publishes_real_service_stopping_until_exit(tmp_path, monkeypatch):
    from kollabor_engine.server import create_app

    from kollabor_config import file_lock
    from kollabor_plugins import services
    from kollabor_plugins.packages import files_sha256

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    monkeypatch.setenv("KOLLAB_ENGINE_BYPASS_AUTH", "1")
    paths = [Path(module.__file__).resolve().parent.parent for module in (services, file_lock)]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(map(str, paths)))
    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    root.mkdir(parents=True)
    ready = tmp_path / "ready"
    code = (
        "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"open({str(ready)!r},'w').close(); time.sleep(60)"
    )
    manifest = {"id": "hello", "name": "Hello", "services": {
        "worker": {"command": [sys.executable, "-c", code], "stop_timeout_s": 4},
    }}
    (root / "kollab-plugin.json").write_text(json.dumps(manifest))
    (home / "plugins.json").write_text(json.dumps({"plugins": {"hello": {
        "enabled": True, "enabled_path": "/usr/bin:/bin", "granted": [],
        "files_sha256": files_sha256(root),
    }}, "dev": {}}))
    wrapper = subprocess.Popen([
        sys.executable, "-c",
        "from kollabor_plugins.services import run_service; run_service('hello','worker','abcd')", "abcd",
    ])
    try:
        deadline = time.monotonic() + 8
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists(), "service did not start"
        with TestClient(create_app()) as client:
            assert client.post("/plugins/hello/disable").status_code == 200
            response = client.get("/plugins")
            assert response.status_code == 200
            body = response.json()
            entry = next(item for item in body["plugins"] if item["id"] == "hello")
            assert entry["enabled"] is False
            assert entry["state"] == "stopping"
            assert entry["services"][0]["state"] == "stopping"
            assert entry["services"][0]["stop_deadline"] > body["server_time"]
            assert client.post("/plugins/hello/enable").status_code == 409
            wrapper.wait(timeout=8)
            response = client.get("/plugins")
            entry = next(item for item in response.json()["plugins"] if item["id"] == "hello")
            assert entry["state"] == "off"
            assert entry["services"][0]["state"] == "stopped"
            assert client.post("/plugins/hello/enable").status_code == 200
    finally:
        if wrapper.poll() is None:
            wrapper.terminate()
            try:
                wrapper.wait(timeout=8)
            except subprocess.TimeoutExpired:
                wrapper.kill()
                wrapper.wait(timeout=3)
