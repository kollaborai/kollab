"""Inaccessible processes are never assumed safe to signal."""

import json
from unittest.mock import Mock

import psutil


def test_stop_does_not_signal_when_process_cannot_be_verified(tmp_path, monkeypatch):
    from kollabor_plugins import services

    monkeypatch.setenv("HOME", str(tmp_path))
    run = tmp_path / ".kollab" / "plugin-data" / "hello" / "run"
    run.mkdir(parents=True)
    (run / "worker.pid").write_text(json.dumps({"wrapper": {"pid": 4242, "start_time": 1, "nonce": "secret-token"}}))

    def denied(pid):
        raise psutil.AccessDenied(pid=pid)

    kill = Mock()
    monkeypatch.setattr(services.psutil, "Process", denied)
    monkeypatch.setattr(services.os, "kill", kill)
    services.stop_services("hello")
    kill.assert_not_called()
