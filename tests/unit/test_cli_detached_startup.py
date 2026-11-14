import os
import sys

import pytest

import kollabor.cli as cli
from kollabor import daemon


def test_detached_parent_reports_child_startup_refusal(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_update_before_launch", lambda: None)
    monkeypatch.setattr(os, "fork", lambda: 12345)
    monkeypatch.setattr(sys, "argv", ["kollab", "--detached"])
    monkeypatch.delenv(daemon.DETACHED_CHILD_ENV, raising=False)
    monkeypatch.delenv(daemon.SERVICE_ENV, raising=False)
    monkeypatch.setattr(
        cli,
        "_wait_for_detached_child_startup",
        lambda read_fd, child_pid: "refused: name already held",
        raising=False,
    )

    with pytest.raises(SystemExit) as exc:
        cli.cli_main()

    assert exc.value.code == 1
    assert "refused: name already held" in capsys.readouterr().err


@pytest.mark.parametrize("platform", ["posix", "nt"])
def test_detached_parent_rejects_zero_exit_before_ready(monkeypatch, platform):
    monkeypatch.setattr(cli, "_update_before_launch", lambda: None)
    monkeypatch.setattr(sys, "argv", ["kollab", "--detached"])
    monkeypatch.delenv(daemon.DETACHED_CHILD_ENV, raising=False)
    monkeypatch.delenv(daemon.SERVICE_ENV, raising=False)
    monkeypatch.setattr(os, "name", platform)

    def child_exited(*args, **kwargs):
        raise daemon.DaemonExited(0)

    if platform == "nt":
        class Child:
            pid = 12345
            stderr = type("Stderr", (), {"fileno": lambda self: 2, "close": lambda self: None})()

        monkeypatch.setattr(daemon, "spawn_detached_child", lambda *args, **kwargs: Child())
        monkeypatch.setattr(daemon, "wait_for_detached_startup", child_exited)
    else:
        monkeypatch.setattr(os, "fork", lambda: 12345)
        monkeypatch.setattr(cli, "_wait_for_detached_child_startup", child_exited)

    with pytest.raises(SystemExit) as exc:
        cli.cli_main()

    assert exc.value.code == 1
