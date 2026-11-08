"""CLI agent launches from terminal-tool children must be refused."""

import sys

import pytest

import kollabor.cli as cli


@pytest.mark.parametrize(
    "argv",
    [
        ["kollab", "--agent", "coder"],
        ["kollab", "-a", "coder"],
        ["kollab", "--agent=coder"],
        ["kollab", "--agen", "coder"],
        ["kollab", "-acoder"],
        ["kollab", "--detached", "--agent", "coder"],
    ],
)
@pytest.mark.parametrize(
    ("marker", "value"),
    [
        ("KOLLAB_TOOL_SHELL", "1"),
        ("KOLLAB_AGENT_PID", "1"),
        ("KOLLAB_TOOL_SHELL", ""),
        ("KOLLAB_AGENT_PID", ""),
    ],
)
def test_cli_refuses_tool_shell_agent_launch_before_startup(
    argv, marker, value, monkeypatch, capsys
):
    for name in ("KOLLAB_TOOL_SHELL", "KOLLAB_AGENT_PID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(marker, value)
    monkeypatch.setattr(
        cli,
        "_update_before_launch",
        lambda: pytest.fail("agent launch must be refused before update/startup"),
    )
    monkeypatch.setattr(sys, "argv", argv)

    with pytest.raises(SystemExit) as exc:
        cli.cli_main()

    assert exc.value.code == 2
    assert "hub_spawn" in capsys.readouterr().err


def test_cli_allows_agent_launch_without_tool_shell_markers(monkeypatch):
    for name in ("KOLLAB_TOOL_SHELL", "KOLLAB_AGENT_PID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cli, "_update_before_launch", lambda: None)
    monkeypatch.setattr(cli, "_should_use_daemon", lambda: False)
    launches = []
    monkeypatch.setattr(cli, "_run_async_main_with_sigterm_cleanup", launches.append)
    monkeypatch.setattr(sys, "argv", ["kollab", "--agent", "coder"])

    cli.cli_main()

    assert launches == [False]


@pytest.mark.parametrize(
    "argv",
    [
        ["kollab", "--", "--agent", "coder"],
        ["kollab", "please help with --agent coder"],
    ],
)
def test_cli_does_not_refuse_agent_text_without_agent_option(argv, monkeypatch):
    monkeypatch.setenv("KOLLAB_TOOL_SHELL", "1")
    monkeypatch.delenv("KOLLAB_AGENT_PID", raising=False)
    monkeypatch.setattr(cli, "_update_before_launch", lambda: None)
    monkeypatch.setattr(cli, "_should_use_daemon", lambda: False)
    launches = []
    monkeypatch.setattr(cli, "_run_async_main_with_sigterm_cleanup", launches.append)
    monkeypatch.setattr(sys, "argv", argv)

    cli.cli_main()

    assert launches == [False]
