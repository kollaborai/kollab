"""A daemon that dies before it is ready reports how it died, not a hang.

The launcher stays quiet for exit status 2 (a rejected argument such as an
unknown --llm) because the in-process fallback prints that error itself.
"""

import os

import pytest

from kollabor import daemon


def test_a_daemon_that_exits_reports_its_status_not_a_hang():
    read_fd, write_fd = os.pipe()
    pid = os.fork()
    if pid == 0:  # the "daemon": rejects its arguments before signalling ready
        os._exit(2)
    os.close(write_fd)
    try:
        with pytest.raises(daemon.DaemonExited) as exc:
            daemon._wait_for_ready(read_fd, pid, timeout=5.0)
    finally:
        os.close(read_fd)
    assert exc.value.exit_code == 2
    assert isinstance(exc.value, RuntimeError)  # cli's fallback still catches it
