"""`kollab service`: this folder's agent under systemd or launchd.

Rendering and the manager commands are checked here; the live proof installs it
for real (macOS launchd, Linux systemd in a container) and reboots the agent.
"""

import os
import plistlib
import sys
from pathlib import Path

import pytest

import kollabor.daemon as daemon
import kollabor.service as service
import kollabor_cli_main

ROOT = Path("/home/dev/My Project")


def test_the_name_is_readable_and_two_folders_with_one_name_never_collide():
    name = service.service_name(ROOT)
    assert name.startswith("kollab-my-project-") and len(name) == len("kollab-my-project-") + 8
    assert service.service_name(Path("/srv/My Project")) != name
    assert service.service_name(Path("/")).startswith("kollab-root-")


def test_the_systemd_unit_runs_kollab_detached_in_the_folder_and_always_restarts():
    env = service.service_env({"KOLLAB_NO_KEYRING": "1"})
    unit = service.systemd_unit(Path("/home/dev/100% done"), env)

    assert "WorkingDirectory=/home/dev/100%% done" in unit  # systemd expands % specifiers
    assert 'Environment="KOLLAB_SERVICE=1"' in unit
    assert 'Environment="KOLLAB_NO_KEYRING=1"' in unit
    assert f'ExecStart="{sys.executable}" "-m" "kollabor_cli_main" "--detached"' in unit
    assert "Restart=always" in unit and "WantedBy=multi-user.target" in unit
    assert "User=" in unit


def test_the_launchd_plist_starts_at_login_and_after_any_exit(tmp_path):
    env = service.service_env({})
    plist = plistlib.loads(service.launchd_plist("ai.kollabor.x", ROOT, env, tmp_path / "service.log"))

    assert plist["ProgramArguments"] == service.exec_argv()
    assert plist["RunAtLoad"] is True and plist["KeepAlive"] is True
    assert plist["WorkingDirectory"] == str(ROOT)
    assert plist["EnvironmentVariables"] == {"KOLLAB_SERVICE": "1", "PATH": os.environ["PATH"]}


@pytest.fixture
def systemd(monkeypatch, tmp_path):
    """A systemd host that records manager commands instead of running them."""
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(service, "has_systemd", lambda: True)
    monkeypatch.setattr(service, "SYSTEMD_DIR", tmp_path)
    monkeypatch.setattr(service.time, "sleep", lambda seconds: None)
    calls = []

    class Done:
        returncode, stdout, stderr = 0, "LoadState=loaded\nActiveState=active\nMainPID=4242\n", ""

    def run(cmd, **kwargs):
        calls.append(cmd)
        return Done()

    monkeypatch.setattr(service.subprocess, "run", run)
    return calls


def test_install_writes_the_unit_with_sudo_enables_it_and_waits_for_the_agent(systemd, monkeypatch, capsys):
    monkeypatch.setattr(service.os, "geteuid", lambda: 1000)
    answers = iter([[], [], [("koordinator", 4242, True)], [("koordinator", 4242, True)]])
    monkeypatch.setattr(service, "_agents", lambda root: next(answers))
    manager = service._Manager(ROOT)

    assert service.install(manager, service.service_env({}), show=False) == 0

    name = manager.name
    assert systemd[:4] == [
        ["sudo", "tee", str(service.SYSTEMD_DIR / f"{name}.service")],
        ["sudo", "systemctl", "daemon-reload"],
        ["sudo", "systemctl", "enable", name],
        ["sudo", "systemctl", "restart", name],
    ]
    out = capsys.readouterr().out
    assert f"installed {name}: starts at boot" in out
    assert "running, pid 4242" in out and "koordinator; `kollab` in this folder attaches to it" in out


def test_install_refuses_while_another_agent_runs_in_the_folder(systemd, monkeypatch):
    monkeypatch.setattr(service, "_agents", lambda root: [("koordinator", 4242, False)])

    with pytest.raises(SystemExit) as exc:
        service.install(service._Manager(ROOT), service.service_env({}), show=False)

    assert "koordinator (pid 4242) already running" in str(exc.value)
    assert "kollab --hub stop koordinator" in str(exc.value)
    assert systemd == []


def test_status_says_not_installed_and_exits_nonzero(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(service, "has_systemd", lambda: True)

    class Missing:
        returncode, stdout, stderr = 0, "LoadState=not-found\nActiveState=inactive\nMainPID=0\n", ""

    monkeypatch.setattr(service.subprocess, "run", lambda cmd, **kwargs: Missing())

    assert service.status(service._Manager(ROOT)) == 1
    assert "no kollab service for" in capsys.readouterr().out


def test_launchd_install_bootstraps_into_the_login_session(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(service.Path, "home", lambda: tmp_path)
    calls = []

    class Done:
        returncode, stdout, stderr = 0, "", ""

    class Gone:  # `launchctl print` once bootout has finished
        returncode, stdout, stderr = 113, "", "Could not find service"

    def run(cmd, **kwargs):
        calls.append(cmd)
        return Gone() if cmd[1] == "print" else Done()

    monkeypatch.setattr(service.subprocess, "run", run)
    manager = service._Manager(ROOT)

    manager.install(manager.render(service.service_env({})))

    plist = tmp_path / "Library" / "LaunchAgents" / f"{manager.label}.plist"
    target = f"gui/{os.getuid()}/{manager.label}"
    assert plistlib.loads(plist.read_bytes())["Label"] == manager.label
    assert calls == [
        ["launchctl", "bootout", target],
        ["launchctl", "print", target],
        ["launchctl", "enable", target],
        ["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)],
    ]


def test_no_service_manager_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(service, "has_systemd", lambda: False)

    with pytest.raises(SystemExit, match="needs systemd"):
        service._Manager(ROOT)


def test_env_takes_key_value_only():
    with pytest.raises(SystemExit) as exc:
        service.main(["install", "--env", "not-an-assignment"])
    assert exc.value.code == 2


@pytest.mark.parametrize(
    "argv, routed",
    [
        (["service", "status"], True),
        (["service", "install", "--print"], True),
        (["service"], True),
        (["service", "is", "down?"], False),  # a question for the agent
    ],
)
def test_only_service_actions_leave_the_app(monkeypatch, argv, routed):
    seen = []
    monkeypatch.setattr(sys, "argv", ["kollab", *argv])
    monkeypatch.setattr(service, "main", lambda args: seen.append(("service", args)) or 0)
    import kollabor.cli

    monkeypatch.setattr(kollabor.cli, "cli_main", lambda: seen.append(("app", None)))

    kollabor_cli_main.cli_main()

    assert seen == ([("service", argv[1:])] if routed else [("app", None)])


def test_the_hub_status_says_service_only_for_the_process_the_manager_started(monkeypatch):
    import asyncio

    from plugins.hub.messenger import AgentMessenger, AgentSocketServer

    async def run():
        server = AgentSocketServer("service-status", lambda *a, **k: None, socket_name=f"svc-{os.getpid()}")
        sock = await server.start()
        try:
            # Inherited from the service that spawned this agent: not this process.
            monkeypatch.setenv(daemon.SERVICE_PID_ENV, str(os.getpid() + 1))
            assert (await AgentMessenger.request_status(sock))["service"] is False
            monkeypatch.setenv(daemon.SERVICE_PID_ENV, str(os.getpid()))
            assert (await AgentMessenger.request_status(sock))["service"] is True
        finally:
            await server.stop()

    asyncio.run(run())


def test_the_service_daemon_stays_in_the_foreground(monkeypatch):
    import kollabor.cli as cli

    monkeypatch.setenv(daemon.SERVICE_ENV, "1")
    monkeypatch.setenv(daemon.SERVICE_PID_ENV, "")
    monkeypatch.setattr(os, "fork", lambda: pytest.fail("forked under a service manager"))
    monkeypatch.setattr(os, "setsid", lambda: pytest.fail("setsid under a service manager"))
    redirected = []
    monkeypatch.setattr(os, "dup2", lambda fd, target: redirected.append(target))
    seen = []

    async def fake_main():
        seen.append(os.environ.get(daemon.SERVICE_ENV))

    monkeypatch.setattr(cli, "async_main", fake_main)
    monkeypatch.setattr(sys, "argv", ["kollab", "--detached"])

    cli.cli_main()

    assert seen == [None]  # popped: agents it spawns with --detached still detach
    assert os.environ[daemon.SERVICE_PID_ENV] == str(os.getpid())
    assert redirected == [0, 1]  # stderr stays with the manager's log


@pytest.mark.parametrize("is_service", [True, False])
def test_a_window_owns_a_daemon_it_attaches_to_unless_a_service_manager_does(monkeypatch, is_service):
    import kollabor.cli as cli

    monkeypatch.setattr(cli, "_should_use_daemon", lambda: True)
    monkeypatch.setattr(daemon, "find_workspace_daemon", lambda argv: (4242, "/tmp/x/koordinator.sock", is_service))
    owned = []

    async def fake_main():
        owned.append(os.environ.get("KOLLAB_DAEMON_PID"))

    monkeypatch.setattr(cli, "async_main", fake_main)
    monkeypatch.setattr(cli, "_kill_owned_daemon", lambda: None)
    monkeypatch.setattr(sys, "argv", ["kollab"])
    for name in (daemon.LAUNCH_ARGS_ENV, "KOLLAB_DAEMON_PID"):
        monkeypatch.setenv(name, "")
    monkeypatch.delenv("KOLLAB_DAEMON_PID")

    cli.cli_main()

    assert owned == [None if is_service else "4242"]
