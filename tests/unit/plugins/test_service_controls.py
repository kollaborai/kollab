"""Explicit process controls record desired state and touch only the target."""

import json
from unittest.mock import Mock

import pytest


def setup(tmp_path, monkeypatch):
    from kollabor_plugins.packages import files_sha256

    monkeypatch.setenv("HOME", str(tmp_path))
    home = tmp_path / ".kollab"
    root = home / "plugins" / "hello"
    root.mkdir(parents=True)
    manifest = {
        "services": {"one": {"command": ["python3", "-c", "pass"]}, "two": {"command": ["python3", "-c", "pass"]}}
    }
    (root / "kollab-plugin.json").write_text(json.dumps(manifest))
    (home / "plugins.json").write_text(
        json.dumps({"plugins": {"hello": {"enabled": True, "files_sha256": files_sha256(root)}}, "dev": {}})
    )
    return home / "plugin-data" / "hello" / "run"


def test_start_records_desired_and_wakes_supervisor(tmp_path, monkeypatch):
    from kollabor_plugins import services

    run = setup(tmp_path, monkeypatch)
    run.mkdir(parents=True, exist_ok=True)
    (run / "one.state").write_text(json.dumps({"desired": "stopped", "status": "stop-failed", "error": "old"}))
    tick = Mock()
    monkeypatch.setattr(services, "supervisor_tick", tick)
    services.control_service("hello", "one", "start")
    state = json.loads((run / "one.state").read_text())
    assert state["desired"] == "running"
    assert state["status"] == "starting"
    assert "error" not in state
    tick.assert_called_once()
    assert not (run / "two.state").exists()


def test_stop_signals_only_verified_target(tmp_path, monkeypatch):
    from kollabor_plugins import services

    run = setup(tmp_path, monkeypatch)
    run.mkdir(parents=True)
    (run / "one.pid").write_text(json.dumps({"wrapper": {"pid": 123, "start_time": 1, "nonce": "abc"}}))
    signal = Mock()
    monkeypatch.setattr(services, "_identity_alive", lambda identity, wrapper=False: True)
    monkeypatch.setattr(services, "_verified_wrapper", lambda identity: True)
    monkeypatch.setattr(services.os, "kill", signal)
    services.control_service("hello", "one", "stop")
    assert json.loads((run / "one.state").read_text())["desired"] == "stopped"
    signal.assert_called_once_with(123, services.signal.SIGTERM)
    assert not (run / "two.state").exists()


def test_unknown_action_does_not_write(tmp_path, monkeypatch):
    from kollabor_plugins import services

    run = setup(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        services.control_service("hello", "one", "bad")
    assert not run.exists()
