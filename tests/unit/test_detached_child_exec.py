"""A forked daemon child execs a fresh kollab instead of running Python on.

macOS aborts (SIGABRT, "crashed on child side of fork") when a forked child of
a multithreaded parent first touches an Objective-C class, e.g. a Keychain read.
"""

import os

import pytest

import kollabor.cli as cli
from kollabor import binary, daemon


class Exec(Exception):
    """Raised by the fake execv so the test regains control."""


@pytest.fixture
def forked_child(monkeypatch):
    monkeypatch.setattr(os, "fork", lambda: 0)
    monkeypatch.setattr(os, "setsid", lambda: None)
    monkeypatch.setattr(os, "dup2", lambda *a: None)
    monkeypatch.setattr(binary, "kollab_argv", lambda: ["/bin/kollab"])
    # Never let a test run the real app in a mocked "child".
    monkeypatch.setattr(daemon, "_run_daemon", lambda *a: None)
    monkeypatch.setattr(cli, "_run_async_main_with_sigterm_cleanup", lambda *a: None)
    monkeypatch.delenv(daemon.DETACHED_CHILD_ENV, raising=False)
    monkeypatch.delenv(daemon.DAEMON_READY_FD_ENV, raising=False)
    yield
    for name in (daemon.DETACHED_CHILD_ENV, daemon.DAEMON_READY_FD_ENV):
        os.environ.pop(name, None)  # the code under test sets these directly


def test_daemon_child_execs_kollab_with_ready_fd(monkeypatch, forked_child):
    seen = {}

    def fake_execv(path, argv):
        fd = int(os.environ[daemon.DAEMON_READY_FD_ENV])
        seen.update(path=path, argv=argv, env=os.environ[daemon.DETACHED_CHILD_ENV],
                    fd=fd, inheritable=os.get_inheritable(fd))
        raise Exec

    monkeypatch.setattr(os, "execv", fake_execv)
    with pytest.raises(Exec):
        daemon.fork_daemon(["kollab", "--daemon", "--agent", "x"])
    os.close(seen["fd"])

    assert seen["path"] == "/bin/kollab"
    assert seen["argv"] == ["/bin/kollab", "--detached", "--agent", "x"]
    assert seen["env"] == "1"
    assert seen["inheritable"] is True


def test_daemon_child_falls_back_in_process_when_exec_fails(monkeypatch, forked_child):
    ran = []
    monkeypatch.setattr(os, "execv", lambda *a: (_ for _ in ()).throw(OSError("nope")))
    monkeypatch.setattr(daemon, "_run_daemon", lambda argv, fd: ran.append(argv))
    with pytest.raises(SystemExit):
        daemon.fork_daemon(["kollab", "--agent", "x"])
    os.close(int(os.environ.pop(daemon.DAEMON_READY_FD_ENV)))

    assert ran == [["kollab", "--agent", "x"]]
    assert daemon.DETACHED_CHILD_ENV not in os.environ


def test_cli_detached_child_marker_skips_fork(monkeypatch):
    ran = []
    monkeypatch.setattr(cli, "_update_before_launch", lambda: None)
    monkeypatch.setattr(cli, "_run_async_main_with_sigterm_cleanup", ran.append)
    monkeypatch.setattr(os, "fork", lambda: pytest.fail("forked again"))
    monkeypatch.setattr(os, "execv", lambda *a: pytest.fail("exec'd again"))
    monkeypatch.setattr("sys.argv", ["kollab", "--detached"])
    monkeypatch.setenv(daemon.DETACHED_CHILD_ENV, "1")
    monkeypatch.delenv(daemon.SERVICE_PID_ENV, raising=False)

    cli.cli_main()

    assert ran == [True]
    assert daemon.DETACHED_CHILD_ENV not in os.environ
    assert daemon.SERVICE_PID_ENV not in os.environ


def test_cli_detached_branch_execs_in_the_child(monkeypatch, forked_child):
    monkeypatch.setattr(cli, "_update_before_launch", lambda: None)
    monkeypatch.setattr("sys.argv", ["kollab", "--detached", "--agent", "x"])
    monkeypatch.delenv(daemon.SERVICE_ENV, raising=False)
    monkeypatch.setattr(os, "execv", lambda path, argv: (_ for _ in ()).throw(Exec((path, argv))))

    with pytest.raises(Exec) as exc:
        cli.cli_main()

    assert exc.value.args[0] == ("/bin/kollab", ["/bin/kollab", "--detached", "--agent", "x"])
