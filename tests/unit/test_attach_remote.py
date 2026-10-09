"""Tests for `kollab --attach identity@host` (kollabor/attach_remote.py).

ssh is faked at the subprocess boundary. The `where` helper runs against
real unix sockets inside a fake ~/.kollab.
"""

import asyncio
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import kollabor.cli as cli
from kollabor import attach_remote
from kollabor.attach_remote import (
    RemoteAttach,
    RemoteAttachError,
    open_attach_target,
    parse_attach_target,
)
from plugins.hub import presence


def _done(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


def _no_ssh(*args, **kwargs):
    raise AssertionError("ssh must not run for a plain identity or a bad target")


def _fake_ssh(monkeypatch, *, creates_socket=True, exits_with=None, stderr_text=""):
    """Replace the ssh -L process: record argv, create or refuse the local socket."""
    started = []

    class FakeSsh:
        def __init__(self, argv, stdin=None, stdout=None, stderr=None):
            self.argv = argv
            self.returncode = exits_with
            self.terminated = False
            started.append(self)
            if stderr is not None:
                stderr.write(stderr_text)
            if creates_socket:
                local = argv[argv.index("-L") + 1].split(":", 1)[0]
                open(local, "w").close()

        def poll(self):
            return self.returncode

        def terminate(self):
            self.terminated = True
            self.returncode = -15

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(attach_remote.subprocess, "Popen", FakeSsh)
    return started


def test_plain_identity_passes_through_without_ssh(monkeypatch):
    monkeypatch.setattr(attach_remote.subprocess, "run", _no_ssh)
    monkeypatch.setattr(attach_remote.subprocess, "Popen", _no_ssh)
    assert open_attach_target("lapis") == ("lapis", None)


def test_identity_at_host_splits_on_first_at():
    assert parse_attach_target("lapis@devbox") == ("lapis", "devbox")
    assert parse_attach_target("lapis@alice@devbox") == ("lapis", "alice@devbox")


@pytest.mark.parametrize(
    "value",
    [
        "@devbox",
        "lapis@",
        "lapis@-oProxyCommand=evil",
        "la pis@devbox",
        "lapis@dev box",
    ],
)
def test_bad_target_is_refused_before_ssh(monkeypatch, value):
    monkeypatch.setattr(attach_remote.subprocess, "run", _no_ssh)
    with pytest.raises(RemoteAttachError):
        open_attach_target(value)


def test_discovery_runs_where_over_ssh(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return _done(
            stdout="Warning: Permanently added\n/tmp/kollabor-hub/k/lapis.sock\n"
        )

    monkeypatch.setattr(attach_remote.subprocess, "run", fake_run)
    sock = attach_remote.find_remote_socket("lapis", "devbox")
    assert sock == "/tmp/kollabor-hub/k/lapis.sock"
    argv, kwargs = calls[0]
    assert argv[0] == "ssh" and argv[-2] == "devbox"
    assert argv[-1] == "kollab --hub where lapis"
    assert "BatchMode=yes" in argv
    assert kwargs["timeout"] == attach_remote.DISCOVERY_TIMEOUT_S


@pytest.mark.parametrize(
    "result, expected",
    [
        (
            _done(255, stderr="Permission denied (publickey).\n"),
            "cannot reach devbox over ssh: Permission denied (publickey).",
        ),
        (
            _done(127, stderr="zsh: command not found: kollab\n"),
            "kollab is not on the PATH of ssh sessions on devbox",
        ),
        (
            _done(1, stderr="no live agent named 'lapis' on this machine\n"),
            "devbox: no live agent named 'lapis' on this machine",
        ),
        (
            _done(1, stdout="unknown hub command: where\n"),
            "devbox's kollab gave no socket for 'lapis'",
        ),
        (_done(0, stdout="not a path\n"), "devbox's kollab gave no socket for 'lapis'"),
    ],
)
def test_discovery_failures_are_plain(monkeypatch, result, expected):
    monkeypatch.setattr(attach_remote.subprocess, "run", lambda argv, **kw: result)
    with pytest.raises(RemoteAttachError) as exc:
        attach_remote.find_remote_socket("lapis", "devbox")
    assert expected in str(exc.value)


def test_discovery_missing_ssh_and_timeout_are_plain(monkeypatch):
    def missing(argv, **kwargs):
        raise FileNotFoundError(2, "No such file", "ssh")

    monkeypatch.setattr(attach_remote.subprocess, "run", missing)
    with pytest.raises(RemoteAttachError, match="ssh is not installed or not on PATH"):
        attach_remote.find_remote_socket("lapis", "devbox")

    def slow(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(attach_remote.subprocess, "run", slow)
    with pytest.raises(
        RemoteAttachError, match="timed out after 3s reaching devbox over ssh"
    ):
        attach_remote.find_remote_socket("lapis", "devbox", timeout=3)


def test_forward_argv_and_cleanup_on_close(monkeypatch):
    monkeypatch.setattr(
        attach_remote,
        "find_remote_socket",
        lambda identity, host: "/tmp/kollabor-hub/k/lapis.sock",
    )
    started = _fake_ssh(monkeypatch)
    remote = RemoteAttach("lapis", "devbox")
    local = remote.open()
    argv = started[0].argv
    assert argv[:2] == ["ssh", "-N"]
    for flag in (
        "ExitOnForwardFailure=yes",
        "StreamLocalBindUnlink=yes",
        "BatchMode=yes",
    ):
        assert flag in argv
    assert argv[argv.index("-L") + 1] == f"{local}:/tmp/kollabor-hub/k/lapis.sock"
    assert argv[-1] == "devbox"
    tmp = os.path.dirname(local)
    assert stat.S_IMODE(os.stat(tmp).st_mode) == 0o700
    remote.close()
    assert started[0].terminated
    assert not os.path.exists(tmp)
    remote.close()  # second call is a no-op


def test_exit_hook_kills_forward_and_removes_dir(monkeypatch):
    monkeypatch.setattr(
        attach_remote, "find_remote_socket", lambda identity, host: "/tmp/x.sock"
    )
    started = _fake_ssh(monkeypatch)
    registered = []
    monkeypatch.setattr(attach_remote.atexit, "register", registered.append)
    monkeypatch.setattr(attach_remote.atexit, "unregister", lambda fn: None)
    local = RemoteAttach("lapis", "devbox").open()
    assert registered and registered[0].__name__ == "close"
    registered[0]()  # what interpreter exit runs
    assert started[0].terminated
    assert not os.path.exists(os.path.dirname(local))


def test_forward_that_never_comes_up_times_out_and_kills_ssh(monkeypatch):
    monkeypatch.setattr(
        attach_remote, "find_remote_socket", lambda identity, host: "/tmp/x.sock"
    )
    started = _fake_ssh(monkeypatch, creates_socket=False)
    with pytest.raises(
        RemoteAttachError, match="timed out after 0.2s waiting for the forward"
    ):
        RemoteAttach("lapis", "devbox", timeout=0.2).open()
    assert started[0].terminated


def test_forward_that_exits_reports_its_stderr(monkeypatch):
    monkeypatch.setattr(
        attach_remote, "find_remote_socket", lambda identity, host: "/tmp/x.sock"
    )
    _fake_ssh(
        monkeypatch,
        creates_socket=False,
        exits_with=255,
        stderr_text="Warning: x\nbind: Address already in use\n",
    )
    with pytest.raises(
        RemoteAttachError, match="ssh forward to devbox failed: bind: Address"
    ):
        RemoteAttach("lapis", "devbox").open()


def test_forward_without_ssh_binary_is_plain(monkeypatch):
    monkeypatch.setattr(
        attach_remote, "find_remote_socket", lambda identity, host: "/tmp/x.sock"
    )

    def missing(argv, **kwargs):
        raise FileNotFoundError(2, "No such file", "ssh")

    monkeypatch.setattr(attach_remote.subprocess, "Popen", missing)
    with pytest.raises(RemoteAttachError, match="cannot run ssh: No such file"):
        RemoteAttach("lapis", "devbox").open()


@pytest.fixture
def where_home(monkeypatch):
    """A fake ~/.kollab. Sockets live in a short /tmp dir (unix path length limit)."""
    base = Path(tempfile.mkdtemp(prefix="kollab-where-", dir="/tmp"))
    home = base / ".kollab"
    monkeypatch.setattr(
        "kollabor_config.config_utils.get_config_directory", lambda: home
    )
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    yield SimpleNamespace(home=home, socks=base, dead_pid=dead.pid)
    shutil.rmtree(base, ignore_errors=True)


def _unix_socket(path: Path) -> str:
    sock = socket.socket(socket.AF_UNIX)
    sock.bind(str(path))
    sock.close()
    return str(path)


def _record(
    hub: Path, name: str, identity: str, pid: int, sock: str, started: float
) -> None:
    (hub / "presence").mkdir(parents=True, exist_ok=True)
    record = {
        "agent_id": name,
        "identity": identity,
        "pid": pid,
        "socket_path": sock,
        "started_at": started,
    }
    (hub / "presence" / f"{name}.json").write_text(json.dumps(record))


def test_find_live_socket_picks_newest_live_record_in_any_project(where_home):
    home, socks = where_home.home, where_home.socks
    old = _unix_socket(socks / "old.sock")
    new = _unix_socket(socks / "new.sock")
    dead = _unix_socket(socks / "dead.sock")
    _record(
        home / "projects" / "one" / "hub", "lapis-1", "lapis", os.getpid(), old, 100.0
    )
    _record(
        home / "projects" / "two" / "hub", "lapis-2", "lapis", os.getpid(), new, 200.0
    )
    _record(home / "hub", "lapis-3", "lapis", where_home.dead_pid, dead, 300.0)
    _record(home / "hub", "opal-1", "opal", os.getpid(), old, 400.0)
    assert presence.find_live_socket("lapis") == new


def test_find_live_socket_skips_missing_socket_and_relative_path(where_home):
    home, socks = where_home.home, where_home.socks
    _record(
        home / "hub", "lapis-1", "lapis", os.getpid(), str(socks / "gone.sock"), 1.0
    )
    _record(home / "hub", "lapis-2", "lapis", os.getpid(), "lapis.sock", 2.0)
    assert presence.find_live_socket("lapis") is None


def test_live_agents_on_machine_lists_each_live_agent_with_its_hub(where_home):
    home, socks = where_home.home, where_home.socks
    one, two = home / "projects" / "one" / "hub", home / "projects" / "two" / "hub"
    sock = _unix_socket(socks / "a.sock")
    _record(one, "lapis-1", "lapis", os.getpid(), sock, 1.0)
    _record(two, "ruby-1", "ruby", os.getpid(), sock, 2.0)
    _record(two, "opal-1", "opal", where_home.dead_pid, sock, 3.0)
    found = [(hub, rec["identity"]) for hub, rec in presence.live_agents_on_machine()]
    assert found == [(one, "lapis"), (two, "ruby")]


def test_find_live_socket_creates_nothing(where_home):
    assert presence.find_live_socket("lapis") is None
    assert not where_home.home.exists()


def test_hub_where_prints_the_socket_or_fails_plainly(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_apply_hub_project_scope_from_config", lambda: None)
    monkeypatch.setattr(
        presence,
        "find_live_socket",
        lambda name: "/tmp/kollabor-hub/k/lapis.sock" if name == "lapis" else None,
    )
    asyncio.run(cli._handle_cli_hub(["where", "lapis"]))
    assert capsys.readouterr().out == "/tmp/kollabor-hub/k/lapis.sock\n"

    with pytest.raises(SystemExit) as exc:
        asyncio.run(cli._handle_cli_hub(["where", "nobody"]))
    assert exc.value.code == 1
    assert "no live agent named 'nobody'" in capsys.readouterr().err

    with pytest.raises(SystemExit) as exc:
        asyncio.run(cli._handle_cli_hub(["where"]))
    assert exc.value.code == 2


def test_sigterm_exits_through_cleanup(monkeypatch):
    """`kill <pid>` becomes a normal exit, so the atexit cleanup still runs."""
    import signal

    monkeypatch.setattr(
        attach_remote, "find_remote_socket", lambda identity, host: "/tmp/x.sock"
    )
    _fake_ssh(monkeypatch)
    previous = signal.signal(signal.SIGTERM, signal.SIG_DFL)
    try:
        remote = RemoteAttach("lapis", "devbox")
        remote.open()
        handler = signal.getsignal(signal.SIGTERM)
        assert callable(handler)
        with pytest.raises(SystemExit) as exited:
            handler(signal.SIGTERM, None)
        assert exited.value.code == 128 + signal.SIGTERM
        remote.close()
    finally:
        signal.signal(signal.SIGTERM, previous)
