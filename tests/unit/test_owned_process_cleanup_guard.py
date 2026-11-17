"""Fake-only regressions for private-group cleanup authorization."""

import json
import os
from types import SimpleNamespace

import psutil
import pytest

from tests.unit import _owned_processes as owned


def _unexpected_group_signal(*args):
    raise AssertionError(args)


def test_cleanup_leaves_a_stop_request_before_watchdog_handshake(tmp_path, monkeypatch):
    group = os.getpgrp() + 100000
    ready = tmp_path / "guard.json"

    def unexpected_process_lookup(*args, **kwargs):
        raise AssertionError("cleanup must not resolve a numeric PID")

    monkeypatch.setattr(psutil, "Process", unexpected_process_lookup)
    monkeypatch.setattr(os, "getpgid", unexpected_process_lookup)
    monkeypatch.setattr(os, "killpg", _unexpected_group_signal)

    owned.cleanup_group(group, ready)
    assert ready.with_suffix(ready.suffix + ".stop").is_file()


def test_cleanup_requests_group_stop_from_the_live_private_watchdog(tmp_path, monkeypatch):
    group = os.getpgrp() + 100000
    ready = tmp_path / "guard.json"
    ready.write_text(json.dumps({"pid": 999999, "birth": 10.0, "pgid": group}))
    stop_request = ready.with_suffix(ready.suffix + ".stop")

    def unexpected_process_lookup(*args, **kwargs):
        raise AssertionError("cleanup must not resolve a numeric PID")

    monkeypatch.setattr(psutil, "Process", unexpected_process_lookup)
    monkeypatch.setattr(os, "getpgid", unexpected_process_lookup)
    monkeypatch.setattr(os, "killpg", _unexpected_group_signal)

    owned.cleanup_group(group, ready)

    assert stop_request.is_file()
    assert stop_request.stat().st_mode & 0o777 == 0o600


def test_cleanup_only_writes_a_request_for_a_stale_watchdog_identity(tmp_path, monkeypatch):
    group = os.getpgrp() + 100000
    ready = tmp_path / "guard.json"
    ready.write_text(json.dumps({"pid": 999999, "birth": 10.0, "pgid": group}))
    stop_request = ready.with_suffix(ready.suffix + ".stop")
    signals = []

    def unexpected_process_lookup(*args, **kwargs):
        raise AssertionError("cleanup must not resolve a numeric PID")

    monkeypatch.setattr(psutil, "Process", unexpected_process_lookup)
    monkeypatch.setattr(os, "getpgid", unexpected_process_lookup)
    monkeypatch.setattr(os, "killpg", lambda *args: signals.append(args))

    owned.cleanup_group(group, ready)

    assert stop_request.is_file()
    assert signals == []


def test_bounded_identity_wait_reports_survivor_without_signaling(monkeypatch):
    signals = []

    class LiveProcess:
        def create_time(self):
            return 10.0

        def is_running(self):
            return True

        def status(self):
            return psutil.STATUS_RUNNING

        def terminate(self):
            signals.append("terminate")

        def kill(self):
            signals.append("kill")

    monkeypatch.setattr(owned.psutil, "Process", lambda _pid: LiveProcess())

    with pytest.raises(RuntimeError, match="defer cleanup to the ephemeral CI runner"):
        owned.wait_for_recorded_identities([(4242, 10.0)], timeout=0)

    assert signals == []


def test_bounded_identity_wait_treats_reused_pid_as_the_old_identity_exit(monkeypatch):
    class ReusedProcess:
        def create_time(self):
            return 11.0

        def is_running(self):
            return True

        def status(self):
            return psutil.STATUS_RUNNING

    monkeypatch.setattr(owned.psutil, "Process", lambda _pid: ReusedProcess())

    owned.wait_for_recorded_identities([(4242, 10.0)], timeout=0)


def test_asyncio_child_signal_gate_fails_closed_without_waitid(monkeypatch):
    loop_type = type("_UnixSelectorEventLoop", (), {"__module__": "asyncio.unix_events"})
    watcher_type = type("_UnknownChildWatcher", (), {"__module__": "asyncio.unix_events"})
    threaded_type = type("_ThreadedChildWatcher", (), {"__module__": "asyncio.unix_events"})
    loop = loop_type()
    loop._watcher = watcher_type()
    monkeypatch.setattr(owned, "asyncio", SimpleNamespace(get_running_loop=lambda: loop))
    monkeypatch.setattr(
        owned,
        "import_module",
        lambda _name: SimpleNamespace(_UnixSelectorEventLoop=loop_type, _ThreadedChildWatcher=threaded_type),
    )
    monkeypatch.setattr(owned, "os", SimpleNamespace(name="posix", waitid=object(), WNOWAIT=object()))
    monkeypatch.setattr(owned, "sys", SimpleNamespace(implementation=SimpleNamespace(name="cpython")))

    assert not owned.asyncio_child_signal_is_reap_serialized()


@pytest.mark.parametrize(
    ("watcher_name", "os_attributes"),
    [
        ("_PidfdChildWatcher", {"pidfd_open": object()}),
        ("_ThreadedChildWatcher", {"waitid": object(), "WNOWAIT": object()}),
    ],
)
def test_asyncio_child_signal_gate_accepts_known_serialized_watchers(monkeypatch, watcher_name, os_attributes):
    loop_type = type("_UnixSelectorEventLoop", (), {"__module__": "asyncio.unix_events"})
    watcher_type = type(watcher_name, (), {"__module__": "asyncio.unix_events"})
    loop = loop_type()
    loop._watcher = watcher_type()
    monkeypatch.setattr(owned, "asyncio", SimpleNamespace(get_running_loop=lambda: loop))
    monkeypatch.setattr(
        owned,
        "import_module",
        lambda _name: SimpleNamespace(
            _UnixSelectorEventLoop=loop_type,
            **{watcher_name: watcher_type},
        ),
    )
    monkeypatch.setattr(owned, "os", SimpleNamespace(name="posix", **os_attributes))
    monkeypatch.setattr(owned, "sys", SimpleNamespace(implementation=SimpleNamespace(name="cpython")))

    assert owned.asyncio_child_signal_is_reap_serialized()
