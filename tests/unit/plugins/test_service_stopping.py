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



def test_wrapper_cannot_overwrite_stopping_state_during_startup(tmp_path, monkeypatch):
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
    paused, proceed, ready, running_write = (
        tmp_path / "paused",
        tmp_path / "proceed",
        tmp_path / "ready",
        tmp_path / "running-write",
    )
    child_code = (
        "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        f"open({str(ready)!r},'w').close(); time.sleep(60)"
    )
    manifest = {"id": "hello", "services": {"worker": {
        "command": [sys.executable, "-c", child_code], "stop_timeout_s": 3,
    }}}
    (root / "kollab-plugin.json").write_text(json.dumps(manifest))
    (home / "plugins.json").write_text(json.dumps({"plugins": {"hello": {
        "enabled": True, "enabled_path": "/usr/bin:/bin", "granted": [],
        "files_sha256": files_sha256(root),
    }}, "dev": {}}))
    source = f'''
import time
from pathlib import Path
from kollabor_plugins import services
write = services._write_state
def pause(path, value):
    child_identity = path.suffix == ".pid" and isinstance(value.get("child"), dict)
    if child_identity:
        Path({str(paused)!r}).touch()
        while not Path({str(proceed)!r}).exists():
            time.sleep(0.01)
    result = write(path, value)
    if path.suffix == ".state" and value.get("status") == "running":
        Path({str(running_write)!r}).touch()
    return result
services._write_state = pause
services.run_service("hello", "worker", "abcd")
'''
    wrapper = subprocess.Popen([sys.executable, "-c", source, "abcd"])
    try:
        deadline = time.monotonic() + 8
        while not (paused.exists() and ready.exists()) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert paused.exists() and ready.exists(), "wrapper did not reach the state-write race"
        settings.set_enabled("hello", False)
        entry = registry._service_entries(home, "hello", manifest)[0]
        assert entry["state"] == "stopping"
        assert entry["desired"] == "stopped"
        proceed.touch()
        wrapper.wait(timeout=7)
        assert not running_write.exists(), "wrapper overwrote the stop with a running state"
    finally:
        proceed.touch()
        if wrapper.poll() is None:
            wrapper.terminate()
            try:
                wrapper.wait(timeout=7)
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


def test_stop_services_treats_a_dead_wrapper_as_stopped(tmp_path, monkeypatch):
    # The plugin is not installed, so control_service fails before any signal is sent.
    from kollabor_plugins import services

    monkeypatch.setenv("HOME", str(tmp_path))
    home = tmp_path / ".kollab"
    home.mkdir()
    (home / "plugins.json").write_text(json.dumps({"plugins": {}, "dev": {}}))
    run = home / "plugin-data" / "hello" / "run"
    run.mkdir(parents=True)
    exited = subprocess.Popen([sys.executable, "-c", "pass"])
    exited.wait()
    identity = {"pid": exited.pid, "start_time": 1.0, "nonce": "abcd"}
    (run / "worker.pid").write_text(json.dumps({"wrapper": identity, "child": None}))
    assert services.stop_services("hello") == []
    state = json.loads((run / "worker.state").read_text())
    assert state["status"] == "stopped"
    assert state["desired"] == "stopped"


def test_stop_services_reports_success_after_signalling_a_live_wrapper(tmp_path, monkeypatch):
    import signal

    from kollabor_plugins import services

    monkeypatch.setenv("HOME", str(tmp_path))
    home = tmp_path / ".kollab"
    home.mkdir()
    (home / "plugins.json").write_text(json.dumps({"plugins": {}, "dev": {}}))
    run = home / "plugin-data" / "hello" / "run"
    run.mkdir(parents=True)
    live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        (run / "worker.pid").write_text(json.dumps({"wrapper": {"pid": live.pid}, "child": None}))
        monkeypatch.setattr(services, "_verified_wrapper", lambda identity: identity.get("pid") == live.pid)
        assert services.stop_services("hello") == []
        assert live.wait(timeout=5) == -signal.SIGTERM
        state = json.loads((run / "worker.state").read_text())
        assert state["status"] == "stopping"
        assert state["stop_deadline"] > time.time()
    finally:
        if live.poll() is None:
            live.kill()
            live.wait(timeout=5)


def test_stop_services_still_reports_a_real_signal_error(tmp_path, monkeypatch):
    from kollabor_plugins import services

    monkeypatch.setenv("HOME", str(tmp_path))
    home = tmp_path / ".kollab"
    home.mkdir()
    (home / "plugins.json").write_text(json.dumps({"plugins": {}, "dev": {}}))
    run = home / "plugin-data" / "hello" / "run"
    run.mkdir(parents=True)
    (run / "worker.pid").write_text(json.dumps({"wrapper": {"pid": 4321}, "child": None}))
    monkeypatch.setattr(services, "_verified_wrapper", lambda _: True)

    def denied(*_):
        raise PermissionError("not permitted")

    monkeypatch.setattr(services.os, "kill", denied)
    assert services.stop_services("hello") == ["worker"]
    state = json.loads((run / "worker.state").read_text())
    assert state["status"] == "stop-failed"
    assert state["desired"] == "stopped"


@pytest.mark.parametrize("bad", [10**1000, -5, "720"])
def test_unusable_stop_timeout_on_an_approved_manifest_still_stops(tmp_path, monkeypatch, bad):
    """A manifest approved before the bound existed must not crash the stop and leave the service running."""
    from kollabor_plugins import services
    from kollabor_plugins.packages import files_sha256

    monkeypatch.setenv("HOME", str(tmp_path))
    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    root.mkdir(parents=True)
    manifest = {"id": "hello", "services": {"worker": {"command": ["/bin/true"], "stop_timeout_s": bad}}}
    (root / "kollab-plugin.json").write_text(json.dumps(manifest))
    (home / "plugins.json").write_text(json.dumps({"plugins": {"hello": {
        "enabled": False, "granted": [], "files_sha256": files_sha256(root),
    }}, "dev": {}}))
    run = home / "plugin-data" / "hello" / "run"
    run.mkdir(parents=True)
    (run / "worker.pid").write_text(json.dumps({"wrapper": {"pid": 4321}}))
    signals = []
    monkeypatch.setattr(services, "_verified_wrapper", lambda _: True)
    monkeypatch.setattr(services.os, "kill", lambda *args: signals.append(args))
    before = time.time()
    services.control_service("hello", "worker", "stop")
    state = json.loads((run / "worker.state").read_text())
    assert signals, "the wrapper must still be signalled"
    assert 29 <= state["stop_deadline"] - before <= 31, "an unusable value falls back to the 30 s default"


def test_fallback_stop_publishes_the_shared_default_grace(tmp_path, monkeypatch):
    """When control_service fails, the fallback stop records the same 30 s grace the wrapper uses."""
    from kollabor_plugins import services

    run = tmp_path / "run"
    run.mkdir()
    pid_path = run / "worker.pid"
    pid_path.write_text(json.dumps({"wrapper": {"pid": 4321}, "child": None}))
    monkeypatch.setattr(services, "_verified_wrapper", lambda _identity: True)
    monkeypatch.setattr(services.os, "kill", lambda *_args: None)
    # The value must come from the shared helper, not a literal that happens to match it.
    monkeypatch.setattr(services, "stop_timeout", lambda _service: 7.0)
    before = time.time()
    services._stop_unrecorded(run, pid_path)
    state = json.loads((run / "worker.state").read_text())
    assert 6 <= state["stop_deadline"] - before <= 8


def test_stop_wins_when_wrapper_waits_for_registry_lock(tmp_path, monkeypatch):
    import threading
    from contextlib import contextmanager

    from kollabor_config import file_lock
    from kollabor_plugins import services
    from kollabor_plugins.packages import files_sha256

    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    paths = [Path(module.__file__).resolve().parent.parent for module in (services, file_lock)]
    monkeypatch.setenv("PYTHONPATH", os.pathsep.join(map(str, paths)))
    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    root.mkdir(parents=True)
    ready, waiting, stop_waiting = (
        tmp_path / "ready",
        tmp_path / "waiting-for-registry-lock",
        tmp_path / "stop-waiting-for-registry-lock",
    )
    child_code = f"from pathlib import Path; import time; Path({str(ready)!r}).touch(); time.sleep(60)"
    manifest = {"id": "hello", "services": {"worker": {"command": [sys.executable, "-c", child_code]}}}
    (root / "kollab-plugin.json").write_text(json.dumps(manifest))
    (home / "plugins.json").write_text(json.dumps({"plugins": {"hello": {
        "enabled": True,
        "enabled_path": "/usr/bin:/bin",
        "granted": [],
        "files_sha256": files_sha256(root),
    }}, "dev": {}}))
    run = home / "plugin-data" / "hello" / "run"
    run.mkdir(parents=True)
    (run / "worker.state").write_text(json.dumps({"desired": "running", "status": "starting"}))
    (run / "worker.pid").write_text(json.dumps({
        "wrapper": {"pid": 99999999, "start_time": 0.0, "nonce": "stale"},
        "child": None,
    }))
    source = "\n".join(
        [
            "import time",
            "from contextlib import contextmanager",
            "from pathlib import Path",
            "from kollabor_plugins import services",
            "original = services.locked_file",
            "@contextmanager",
            "def gate(path, *args, **kwargs):",
            '    if Path(path).name == "plugins.json.lock":',
            f"        Path({str(waiting)!r}).touch()",
            "    with original(path, *args, **kwargs):",
            "        yield",
            "services.locked_file = gate",
            'services.run_service("hello", "worker", "abcd")',
        ]
    )
    original_lock = services.locked_file

    @contextmanager
    def wait_for_stop_lock(path, *args, **kwargs):
        if Path(path).name == "plugins.json.lock":
            stop_waiting.touch()
        with original_lock(path, *args, **kwargs):
            yield

    monkeypatch.setattr(services, "locked_file", wait_for_stop_lock)
    wrapper = None
    stop_thread = None
    stop_errors = []
    try:
        with file_lock.locked_file(home / "plugins.json.lock", private=True):
            wrapper = subprocess.Popen([sys.executable, "-c", source])
            deadline = time.monotonic() + 8
            while not waiting.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert waiting.exists(), "wrapper did not wait for the registry lock"
            def stop():
                try:
                    services.control_service("hello", "worker", "stop")
                except Exception as exc:
                    stop_errors.append(exc)

            stop_thread = threading.Thread(target=stop)
            stop_thread.start()
            deadline = time.monotonic() + 3
            while not stop_waiting.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert stop_waiting.exists(), "stop did not join the startup lock"
        stop_thread.join(timeout=5)
        assert not stop_thread.is_alive(), "stop deadlocked behind startup"
        assert not stop_errors
        try:
            wrapper.wait(timeout=4)
        except subprocess.TimeoutExpired:
            pytest.fail("wrapper started a service after stop had succeeded")
        state = json.loads((run / "worker.state").read_text())
        assert state["desired"] == "stopped"
    finally:
        if stop_thread is not None:
            stop_thread.join(timeout=5)
        if wrapper is not None and wrapper.poll() is None:
            wrapper.terminate()
            try:
                wrapper.wait(timeout=5)
            except subprocess.TimeoutExpired:
                wrapper.kill()
                wrapper.wait(timeout=3)
