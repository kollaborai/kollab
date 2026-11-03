"""Fake-only checks for ACP parent-drop teardown without PID signal fallbacks."""

import asyncio

from tests.integration import test_acp_parent_drop as parent_drop


def _identity(pid, workspace, birth=10.0):
    return {"pid": pid, "create_time": birth, "workspace": str(workspace)}


def test_stale_acp_cleanup_identity_is_observed_without_signaling(tmp_path, monkeypatch):
    workspace = tmp_path / "owned"
    workspace.mkdir()
    signals = []

    class StaleProcess:
        def is_running(self):
            return True

        def create_time(self):
            return 11.0

        def send_signal(self, *args):
            signals.append(("send_signal", args))

        def terminate(self):
            signals.append(("terminate", ()))

        def kill(self):
            signals.append(("kill", ()))

    monkeypatch.setattr(parent_drop.psutil, "Process", lambda _pid: StaleProcess())
    report = {
        "phase": "blocked",
        "daemon": _identity(4242, workspace),
        "sdk": _identity(4343, workspace),
    }

    problems, deferred = asyncio.run(parent_drop.observe_test_owned_processes(tmp_path, report))

    assert problems == []
    assert deferred == []
    assert signals == []


def test_missing_acp_cleanup_process_and_receipt_fail_closed_without_signaling(tmp_path, monkeypatch):
    workspace = tmp_path / "owned"
    workspace.mkdir()
    signals = []

    class MissingProcess:
        def is_running(self):
            return False

        def send_signal(self, *args):
            signals.append(("send_signal", args))

        def terminate(self):
            signals.append(("terminate", ()))

        def kill(self):
            signals.append(("kill", ()))

    monkeypatch.setattr(parent_drop.psutil, "Process", lambda _pid: MissingProcess())
    report = {
        "phase": "blocked",
        "daemon": _identity(4242, workspace),
    }

    problems, deferred = asyncio.run(parent_drop.observe_test_owned_processes(tmp_path, report))

    assert any("missing sdk identity receipt" in problem for problem in problems)
    assert deferred == []
    assert signals == []
