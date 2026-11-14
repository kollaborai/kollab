"""Fake-only checks for ACP parent-drop teardown without PID signal fallbacks."""

import asyncio
import json

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


def test_truncated_owner_report_still_observes_identity_and_reports_deferred_cleanup(tmp_path, monkeypatch):
    (tmp_path / "owner-report.json").write_text('{"phase": "ready",')
    owned = tmp_path / "owned"
    detached = tmp_path / "detached"
    owned.mkdir()
    detached.mkdir()
    for workspace in (owned, detached):
        for process_name, pid in (("daemon", 4242), ("sdk", 4343)):
            receipt = workspace / f"owned-{process_name}-identity.json"
            receipt.write_text(json.dumps(_identity(pid, workspace)))

    async def wait_dead(_identity):
        return None

    monkeypatch.setattr(parent_drop, "same_birth_alive", lambda _identity: True)
    monkeypatch.setattr(parent_drop, "wait_dead", wait_dead)
    calls = []
    observe_owned_processes = parent_drop.observe_test_owned_processes

    async def observe(root, report):
        calls.append((root, report))
        return await observe_owned_processes(root, report)

    monkeypatch.setattr(parent_drop, "observe_test_owned_processes", observe)
    report, problems, deferred = asyncio.run(
        parent_drop.observe_test_owned_processes_after_owner_shutdown(tmp_path, {})
    )

    assert calls == [(tmp_path, {})]
    assert report == {}
    assert any("unreadable owner report" in problem for problem in problems)
    assert {item["identity"] for item in deferred} == {"detached_daemon", "detached_sdk"}
