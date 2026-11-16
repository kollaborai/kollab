"""A disabled service exposes its real graceful-shutdown interval."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest


def test_long_stop_timeout_is_preserved(tmp_path, monkeypatch):
    from kollabor_plugins import services
    from kollabor_plugins.packages import files_sha256

    monkeypatch.setenv("HOME", str(tmp_path))
    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    root.mkdir(parents=True)
    manifest = {"id": "hello", "services": {"worker": {"command": ["/bin/true"], "stop_timeout_s": 720}}}
    (root / "kollab-plugin.json").write_text(json.dumps(manifest))
    (home / "plugins.json").write_text(json.dumps({"plugins": {"hello": {
        "enabled": False, "granted": [], "files_sha256": files_sha256(root),
    }}, "dev": {}}))
    run = home / "plugin-data" / "hello" / "run"
    run.mkdir(parents=True)
    (run / "worker.pid").write_text(json.dumps({"wrapper": {"pid": 4321}}))
    monkeypatch.setattr(services, "_verified_wrapper", lambda _: True)
    monkeypatch.setattr(services.os, "kill", lambda *_: None)
    before = time.time()
    services.control_service("hello", "worker", "stop")
    state = json.loads((run / "worker.state.json").read_text())
    assert 719 <= state["stop_deadline"] - before <= 721



def test_disable_exposes_live_stopping_service_and_rejects_restart(tmp_path, monkeypatch):
    from kollabor_config import file_lock
    from kollabor_plugins import registry, services, settings
    from kollabor_plugins.packages import files_sha256

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    paths = [Path(module.__file__).resolve().parent.parent for module in (services, file_lock)]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(map(str, paths)))
    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    root.mkdir(parents=True)
    ready = tmp_path / "ready"
    code = (
        "import signal,time; "
        "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"open({str(ready)!r},'w').close(); "
        "time.sleep(60)"
    )
    manifest = {"id": "hello", "name": "Hello", "services": {
        "worker": {"command": [sys.executable, "-c", code], "stop_timeout_s": 3}
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
        until = time.monotonic() + 8
        while not ready.exists() and time.monotonic() < until:
            time.sleep(0.02)
        assert ready.exists(), "service did not start"
        settings.set_enabled("hello", False)
        entry = registry._service_entries(home, "hello", manifest)[0]
        assert entry["state"] == "stopping"
        assert entry["desired"] == "stopped"
        assert time.time() < entry["stop_deadline"] <= time.time() + 3.5
        builtin = tmp_path / "builtin"
        builtin.mkdir()
        plugin = next(item for item in registry.plugin_entries(builtin) if item["id"] == "hello")
        assert plugin["state"] == "stopping"
        assert plugin["enabled"] is False
        with pytest.raises(ValueError, match="stopping"):
            settings.set_enabled("hello", True)
        with pytest.raises(ValueError, match="stopping"):
            services.control_service("hello", "worker", "start")
        wrapper.wait(timeout=7)
        entry = registry._service_entries(home, "hello", manifest)[0]
        assert entry["state"] == "stopped"
        assert entry["stop_deadline"] is None
    finally:
        if wrapper.poll() is None:
            wrapper.terminate()
            try:
                wrapper.wait(timeout=7)
            except subprocess.TimeoutExpired:
                wrapper.kill()
                wrapper.wait(timeout=3)
