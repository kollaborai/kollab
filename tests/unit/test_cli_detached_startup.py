import os
import sys
from types import SimpleNamespace

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


@pytest.mark.asyncio
async def test_detached_parent_reports_deferred_startup_failure(monkeypatch):
    ready_event = __import__("asyncio").Event()

    class App:
        _startup_ready = ready_event
        _startup_complete = False
        _startup_error = "deferred startup failed"
        pipe_mode = False

        async def start(self, initial_message=None):
            ready_event.set()

        async def cleanup(self):
            pass

    args = SimpleNamespace(
        mcp_hub=False,
        acp_prompt=None,
        acp_command=None,
        reset_config=False,
        cleanup_projects=False,
        update=False,
        font_dir=False,
        login=False,
        web_ui=False,
        restart=False,
        hub=None,
        attach=None,
        cli_command=None,
        _unknown_args=[],
        _help_pending=False,
        pipe=False,
        query=None,
        detached=True,
        system_prompt=None,
        agent=None,
        llm=None,
        model=None,
        effort=None,
        save=False,
        local=False,
        make_default_profile=False,
        skill=[],
        context=None,
        timeout=None,
    )
    discovery = SimpleNamespace(plugins_dir="plugins")
    reports = []

    monkeypatch.setattr(cli, "discover_plugin_args", lambda: ([], discovery))
    monkeypatch.setattr(cli, "parse_arguments", lambda _: args)
    monkeypatch.setattr(cli, "handle_early_plugin_args", lambda *args: False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys, "argv", ["kollab", "--detached"])
    monkeypatch.setenv(daemon.DETACHED_READY_FD_ENV, "123")
    monkeypatch.setattr(daemon, "report_detached_startup", lambda **kw: reports.append(kw))
    monkeypatch.setattr("kollabor_plugins.PluginRegistry", lambda _: SimpleNamespace())
    monkeypatch.setattr(cli, "TerminalLLMChat", lambda **_: App())

    await cli.async_main()

    assert reports == [{"ready": False, "reason": "deferred startup failed"}]


def test_detached_parent_reports_nonzero_child_exit(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_update_before_launch", lambda: None)
    monkeypatch.setattr(os, "fork", lambda: 12345)
    monkeypatch.setattr(sys, "argv", ["kollab", "--detached"])
    monkeypatch.delenv(daemon.DETACHED_CHILD_ENV, raising=False)
    monkeypatch.delenv(daemon.SERVICE_ENV, raising=False)

    def child_exited(*args, **kwargs):
        raise daemon.DaemonExited(2)

    monkeypatch.setattr(cli, "_wait_for_detached_child_startup", child_exited)

    with pytest.raises(SystemExit) as exc:
        cli.cli_main()

    assert exc.value.code == 2
    assert "status 2" in capsys.readouterr().err
