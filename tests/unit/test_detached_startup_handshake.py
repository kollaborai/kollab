import os
import subprocess

import pytest

from kollabor import daemon


def test_detached_startup_timeout_covers_acp_hub_readiness_budget():
    # ACP startup may wait up to 30 seconds for the Hub after initialization.
    assert daemon.DETACHED_STARTUP_TIMEOUT > 30.0


@pytest.mark.parametrize(
    ("payload", "expected"),
    [(b"ready\n", None), (b"error:name already held\n", "name already held")],
)
def test_wait_for_detached_startup_reads_child_result(monkeypatch, payload, expected):
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, payload)
        monkeypatch.setattr(os, "waitpid", lambda *args: (0, 0))
        assert daemon.wait_for_detached_startup(read_fd, 12345, timeout=0.1) == expected
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_wait_for_detached_startup_ignores_windows_stderr_before_result(monkeypatch):
    read_fd, write_fd = os.pipe()
    try:
        os.write(
            write_fd,
            b"[kollab] 'jasper' is already running (pid=12345).\n"
            b"error:name already held\n",
        )
        monkeypatch.setattr(os, "waitpid", lambda *args: (0, 0))
        assert (
            daemon.wait_for_detached_startup(read_fd, 12345, timeout=0.1)
            == "name already held"
        )
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_report_detached_startup_clears_channel(monkeypatch):
    read_fd, write_fd = os.pipe()
    monkeypatch.setenv(daemon.DETACHED_READY_FD_ENV, str(write_fd))
    try:
        daemon.report_detached_startup(ready=False, reason="name already held")
        assert os.read(read_fd, 128) == b"error:name already held\n"
        assert daemon.DETACHED_READY_FD_ENV not in os.environ
    finally:
        os.close(read_fd)


def test_report_detached_startup_keeps_windows_stderr_open(monkeypatch):
    operations = []
    open_fds = {2}

    def open_devnull(*args, **kwargs):
        open_fds.add(2)
        operations.append(("open", 2))
        return 2

    def close_fd(fd):
        open_fds.discard(fd)
        operations.append(("close", fd))

    with monkeypatch.context() as patcher:
        patcher.setattr(daemon.os, "name", "nt")
        patcher.setattr(daemon.os, "write", lambda fd, data: len(data))
        patcher.setattr(daemon.os, "close", close_fd)
        patcher.setattr(daemon.os, "open", open_devnull)
        patcher.setattr(
            daemon.os,
            "dup2",
            lambda source, target: operations.append(("dup2", source, target)),
        )
        patcher.setenv(daemon.DETACHED_READY_FD_ENV, "2")

        daemon.report_detached_startup(ready=True)

    assert ("dup2", 2, 2) in operations
    assert 2 in open_fds


def test_wait_for_detached_startup_reports_child_exit(monkeypatch):
    read_fd, write_fd = os.pipe()
    monkeypatch.setattr("select.select", lambda *args: ([], [], []))
    monkeypatch.setattr(os, "waitpid", lambda *args: (12345, 256))
    try:
        with pytest.raises(daemon.DaemonExited) as exc:
            daemon.wait_for_detached_startup(read_fd, 12345, timeout=0.1)
        assert exc.value.exit_code == 1
    finally:
        os.close(read_fd)
        os.close(write_fd)


def test_windows_spawn_captures_startup_channel(monkeypatch):
    from kollabor_config import proc_ctl

    seen = {}

    class Child:
        pid = 12345

    def start_child(argv, *, detached=False, **kwargs):
        seen.update(detached=detached, **kwargs)
        return Child()

    monkeypatch.setattr(proc_ctl, "start_child", start_child)
    child = daemon.spawn_detached_child(["kollab", "--detached"], wait_for_startup=True)

    assert child.pid == 12345
    assert seen["detached"] is True
    assert seen["env"][daemon.DETACHED_READY_FD_ENV] == "2"
    assert seen["stderr"] is subprocess.PIPE
