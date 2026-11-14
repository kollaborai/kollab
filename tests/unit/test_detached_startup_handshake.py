import os
import subprocess

import pytest

from kollabor import daemon


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


def test_report_detached_startup_clears_channel(monkeypatch):
    read_fd, write_fd = os.pipe()
    monkeypatch.setenv(daemon.DETACHED_READY_FD_ENV, str(write_fd))
    try:
        daemon.report_detached_startup(ready=False, reason="name already held")
        assert os.read(read_fd, 128) == b"error:name already held\n"
        assert daemon.DETACHED_READY_FD_ENV not in os.environ
    finally:
        os.close(read_fd)


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
