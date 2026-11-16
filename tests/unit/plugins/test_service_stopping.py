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
    state = json.loads((run / "worker.state").read_text())
    assert 719 <= state["stop_deadline"] - before <= 721



def test_failed_stop_is_visible_and_disable_reports_partial_failure(tmp_path, monkeypatch):
    from kollabor_plugins import registry, services, settings
    from kollabor_plugins.packages import files_sha256

    monkeypatch.setenv("HOME", str(tmp_path))
    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    root.mkdir(parents=True)
    manifest = {"id": "hello", "services": {"worker": {"command": ["/bin/true"]}}}
    (root / "kollab-plugin.json").write_text(json.dumps(manifest))
    (home / "plugins.json").write_text(json.dumps({"plugins": {"hello": {
        "enabled": True, "granted": [], "files_sha256": files_sha256(root),
    }}, "dev": {}}))
    run = home / "plugin-data" / "hello" / "run"
    run.mkdir(parents=True)
    (run / "worker.pid").write_text(json.dumps({"wrapper": {"pid": 4321}}))
    (run / "worker.state").write_text(json.dumps({"status": "running", "desired": "running"}))
    monkeypatch.setattr(services, "_verified_wrapper", lambda _: True)
    monkeypatch.setattr(services, "_identity_alive", lambda *_, **__: True)

    def denied(*_):
        raise PermissionError("not permitted")

    monkeypatch.setattr(services.os, "kill", denied)
    with pytest.raises(ValueError, match="Could not stop"):
        settings.set_enabled("hello", False)
    assert json.loads((home / "plugins.json").read_text())["plugins"]["hello"]["enabled"] is False
    entry = registry._service_entries(home, "hello", manifest)[0]
    assert entry["state"] == "stop-failed"
    assert entry["stop_deadline"] is None
    assert "stop" in entry["error"].lower()
    builtin = tmp_path / "builtin"
    builtin.mkdir()
    plugin = next(item for item in registry.plugin_entries(builtin) if item["id"] == "hello")
    assert plugin["state"] == "stop-failed"



@pytest.mark.parametrize("pid_symlink", [False, True])
def test_unverified_or_symlinked_pid_is_not_signalled_and_reports_failure(tmp_path, monkeypatch, pid_symlink):
    from unittest.mock import Mock

    from kollabor_plugins import services, settings
    from kollabor_plugins.packages import files_sha256

    monkeypatch.setenv("HOME", str(tmp_path))
    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    root.mkdir(parents=True)
    (root / "kollab-plugin.json").write_text(json.dumps({"id": "hello", "services": {
        "worker": {"command": ["/bin/true"]},
    }}))
    (home / "plugins.json").write_text(json.dumps({"plugins": {"hello": {
        "enabled": True, "granted": [], "files_sha256": files_sha256(root),
    }}, "dev": {}}))
    run = home / "plugin-data" / "hello" / "run"
    run.mkdir(parents=True)
    identity = json.dumps({"wrapper": {"pid": 4321}})
    if pid_symlink:
        outside = tmp_path / "outside.pid"
        outside.write_text(identity)
        (run / "worker.pid").symlink_to(outside)
    else:
        (run / "worker.pid").write_text(identity)
    (run / "worker.state").write_text(json.dumps({"status": "running", "desired": "running"}))
    monkeypatch.setattr(services, "_verified_wrapper", lambda _: pid_symlink)
    monkeypatch.setattr(services, "_identity_alive", lambda *_, **__: True)
    kill = Mock()
    monkeypatch.setattr(services.os, "kill", kill)
    with pytest.raises(ValueError, match="Could not stop"):
        settings.set_enabled("hello", False)
    kill.assert_not_called()
    state = json.loads((run / "worker.state").read_text())
    assert state["status"] == "stop-failed"
    assert "stop" in state["error"].lower()
    from kollabor_plugins import registry

    original_read = Path.read_text

    def safe_read(path, *args, **kwargs):
        assert not (pid_symlink and path == run / "worker.pid"), "inventory followed a PID symlink"
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", safe_read)
    manifest = json.loads((root / "kollab-plugin.json").read_text())
    entry = registry._service_entries(home, "hello", manifest)[0]
    assert entry["state"] == "stop-failed"



def test_disable_cannot_miss_a_starting_wrapper(tmp_path, monkeypatch):
    import threading

    from kollabor_config import file_lock
    from kollabor_plugins import services, settings
    from kollabor_plugins.packages import files_sha256

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    paths = [Path(module.__file__).resolve().parent.parent for module in (services, file_lock)]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(map(str, paths)))
    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    root.mkdir(parents=True)
    manifest = {"id": "hello", "services": {"worker": {
        "command": [sys.executable, "-c", "import time; time.sleep(30)"], "stop_timeout_s": 1,
    }}}
    (root / "kollab-plugin.json").write_text(json.dumps(manifest))
    (home / "plugins.json").write_text(json.dumps({"plugins": {"hello": {
        "enabled": True, "enabled_path": "/usr/bin:/bin", "granted": [],
        "files_sha256": files_sha256(root),
    }}, "dev": {}}))
    paused, proceed = tmp_path / "paused", tmp_path / "proceed"
    source = f'''
import time
from pathlib import Path
from kollabor_plugins import services
write = services._write_state
def pause(path, value):
    if path.suffix == ".pid":
        Path({str(paused)!r}).touch()
        while not Path({str(proceed)!r}).exists():
            time.sleep(0.01)
    return write(path, value)
services._write_state = pause
services.run_service("hello", "worker", "abcd")
'''
    wrapper = subprocess.Popen([sys.executable, "-c", source, "abcd"])
    errors = []

    def disable():
        try:
            settings.set_enabled("hello", False)
        except Exception as exc:
            errors.append(exc)

    thread = None
    try:
        until = time.monotonic() + 8
        while not paused.exists() and time.monotonic() < until:
            time.sleep(0.01)
        assert paused.exists(), "wrapper did not reach the startup race"
        thread = threading.Thread(target=disable)
        thread.start()
        time.sleep(0.2)
        proceed.touch()
        thread.join(timeout=5)
        assert not thread.is_alive(), "disable deadlocked with service startup"
        assert not errors
        try:
            wrapper.wait(timeout=3)
        except subprocess.TimeoutExpired:
            pytest.fail("disable returned but missed a starting service wrapper")
        assert wrapper.returncode == 0
    finally:
        proceed.touch()
        if thread is not None:
            thread.join(timeout=5)
        if wrapper.poll() is None:
            wrapper.terminate()
            try:
                wrapper.wait(timeout=5)
            except subprocess.TimeoutExpired:
                wrapper.kill()
                wrapper.wait(timeout=3)



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
