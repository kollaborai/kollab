import os
import subprocess
import sys
from dataclasses import dataclass
from unittest.mock import patch

import pytest

import kollabor.application as application_module
import kollabor.updates.git_update as git_update
from kollabor.application import TerminalLLMChat
from kollabor.updates.version_check_service import ReleaseInfo


@dataclass
class _DummyConfig:
    values: dict

    def get(self, key_path, default=None):
        return self.values.get(key_path, default)


class _DummyVersionCheckService:
    def __init__(self, release_info):
        self.release_info = release_info
        self.initialized = False

    async def initialize(self):
        self.initialized = True

    async def check_for_updates(self):
        return self.release_info


class _DummyMessageCoordinator:
    def __init__(self):
        self.messages = []

    def display_raw_text(self, message):
        self.messages.append(message)


class _DummyRenderer:
    def __init__(self):
        self.message_coordinator = _DummyMessageCoordinator()


def _release(version="9.9.9"):
    return ReleaseInfo(
        version=version,
        tag_name=f"v{version}",
        name=f"Version {version}",
        url=f"https://github.com/kollaborai/kollab/releases/tag/v{version}",
        is_prerelease=False,
    )


def test_config_surface_includes_auto_update_toggle():
    from kollabor_tui.config_widgets import ConfigWidgetDefinitions

    definition = ConfigWidgetDefinitions.get_config_modal_definition()
    app_section = next(
        section
        for section in definition["sections"]
        if section["title"] == "Application Settings"
    )

    auto_update = next(
        widget
        for widget in app_section["widgets"]
        if widget.get("config_path") == "kollabor.updates.auto_update_enabled"
    )

    assert auto_update["type"] == "checkbox"
    assert auto_update["label"] == "Auto Update Kollab"


def test_auto_update_dispatches_to_source_checkout(tmp_path, monkeypatch):
    import kollabor.updates.auto_update as auto_update

    (tmp_path / "pyproject.toml").write_text('[project]\nname = "kollab"\n')
    (tmp_path / ".git").mkdir()
    calls = []

    def fake_source_update(repo_root=None):
        calls.append(repo_root)
        return auto_update.AutoUpdateResult(True, "source updated", method="source")

    monkeypatch.setattr(auto_update, "run_source_update", fake_source_update)

    result = auto_update.run_auto_update(repo_root=tmp_path)

    assert result.success is True
    assert result.method == "source"
    assert calls == [tmp_path]


def _fake_upgrade(monkeypatch, auto_update, *, prefix, which=None, versions=("0.10.1", "0.10.2")):
    calls = []
    remaining = list(versions)

    def fake_run_cmd(*args):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "done\n", "")

    monkeypatch.setattr(auto_update.sys, "prefix", prefix)
    monkeypatch.setattr(auto_update.sys, "executable", f"{prefix}/bin/python")
    monkeypatch.setattr(git_update.shutil, "which", lambda name: which if name == "uv" else None)
    monkeypatch.setattr(auto_update, "_run_cmd", fake_run_cmd)
    monkeypatch.setattr(auto_update, "_installed_version", lambda: remaining.pop(0))
    return calls


def test_auto_update_uses_uv_tool_when_running_from_one(tmp_path, monkeypatch):
    import kollabor.updates.auto_update as auto_update

    calls = _fake_upgrade(monkeypatch, auto_update, prefix="/home/u/.local/share/uv/tools/kollab", which="/usr/bin/uv")

    result = auto_update.run_auto_update(repo_root=tmp_path)

    assert (result.success, result.method, result.changed) == (True, "uv", True)
    assert result.message == "Kollab upgraded: v0.10.1 -> v0.10.2 (via uv)."
    assert calls == [("uv", "tool", "upgrade", "kollab")]


def test_auto_update_upgrades_the_running_venv_even_when_uv_is_on_path(tmp_path, monkeypatch):
    import kollabor.updates.auto_update as auto_update

    monkeypatch.setattr(git_update, "_has_pip", lambda: True)
    calls = _fake_upgrade(monkeypatch, auto_update, prefix="/home/u/.local/share/kollab/venv", which="/usr/bin/uv")

    result = auto_update.run_auto_update(repo_root=tmp_path)

    assert (result.success, result.method) == (True, "pip")
    assert calls == [("/home/u/.local/share/kollab/venv/bin/python", "-m", "pip", "install", "--upgrade", "kollab")]


def test_auto_update_uses_uv_pip_when_the_venv_has_no_pip(tmp_path, monkeypatch):
    import kollabor.updates.auto_update as auto_update

    monkeypatch.setattr(git_update, "_has_pip", lambda: False)
    calls = _fake_upgrade(monkeypatch, auto_update, prefix="/venv", which="/usr/bin/uv")

    auto_update.run_auto_update(repo_root=tmp_path)

    assert calls == [("uv", "pip", "install", "--upgrade", "kollab", "--python", "/venv/bin/python")]


def test_auto_update_reports_already_up_to_date_without_change(tmp_path, monkeypatch):
    import kollabor.updates.auto_update as auto_update

    monkeypatch.setattr(git_update, "_has_pip", lambda: True)
    _fake_upgrade(monkeypatch, auto_update, prefix="/venv", versions=("0.10.2", "0.10.2"))

    result = auto_update.run_auto_update(repo_root=tmp_path)

    assert (result.success, result.changed) == (True, False)
    assert result.message == "Kollab is already up to date (v0.10.2)."


@pytest.mark.asyncio
async def test_upgrade_command_reports_up_to_date_and_restarts_only_after_a_change(monkeypatch):
    from types import SimpleNamespace

    import kollabor.commands.system_commands.handlers.system as system_module
    import kollabor.updates as updates
    from kollabor.commands.system_commands.handlers.system import SystemCommandHandler

    renderer = _DummyRenderer()
    renderer.exit_raw_mode = lambda: None
    handler = SimpleNamespace(
        logger=system_module.logging.getLogger("test"),
        event_bus=SimpleNamespace(get_service=lambda name: renderer if name == "renderer" else None),
    )
    execs = []
    monkeypatch.setattr(os, "execv", lambda *args: execs.append(args))

    current = updates.AutoUpdateResult(True, "Kollab is already up to date (v0.10.2).", "pip", changed=False)
    monkeypatch.setattr(updates, "run_auto_update", lambda: current)
    result = await SystemCommandHandler.handle_upgrade(handler, None)
    assert (result.success, result.message, execs) == (True, "Kollab is already up to date (v0.10.2).", [])

    upgraded = updates.AutoUpdateResult(True, "Kollab upgraded: v0.10.1 -> v0.10.2 (via pip).", "pip")
    monkeypatch.setattr(updates, "run_auto_update", lambda: upgraded)
    await SystemCommandHandler.handle_upgrade(handler, None)
    assert "Kollab upgraded: v0.10.1 -> v0.10.2 (via pip)." in renderer.message_coordinator.messages[-1]
    assert len(execs) == 1


@pytest.mark.asyncio
async def test_startup_auto_updates_when_release_available_and_enabled(monkeypatch):
    release = _release()
    app = object.__new__(TerminalLLMChat)
    app.args = None
    app.config = _DummyConfig({"kollabor.updates.auto_update_enabled": True})
    app.version_check_service = _DummyVersionCheckService(release)
    app.renderer = _DummyRenderer()
    calls = []

    def fake_run_auto_update():
        calls.append(True)
        return application_module.AutoUpdateResult(
            True,
            "updated",
            method="uv",
        )

    monkeypatch.setattr(application_module, "run_auto_update", fake_run_auto_update)

    await app._check_for_updates()

    assert calls == [True]
    messages = app.renderer.message_coordinator.messages
    assert any("Auto-update complete" in message for message in messages)
    assert any("restart Kollab" in message for message in messages)


@pytest.mark.asyncio
async def test_startup_only_notifies_when_auto_update_disabled(monkeypatch):
    release = _release()
    app = object.__new__(TerminalLLMChat)
    app.args = None
    app.config = _DummyConfig({"kollabor.updates.auto_update_enabled": False})
    app.version_check_service = _DummyVersionCheckService(release)
    app.renderer = _DummyRenderer()

    def fail_run_auto_update():
        raise AssertionError("auto update should not run")

    monkeypatch.setattr(application_module, "run_auto_update", fail_run_auto_update)

    await app._check_for_updates()

    messages = app.renderer.message_coordinator.messages
    assert any("Update available" in message for message in messages)


def test_stop_daemon_reaps_its_child():
    from kollabor.daemon import stop_daemon

    pid = os.spawnlp(os.P_NOWAIT, "sleep", "sleep", "30")
    stop_daemon(pid)

    with pytest.raises(ChildProcessError):
        os.waitpid(pid, os.WNOHANG)


@pytest.mark.asyncio
async def test_upgrade_relaunches_the_launch_command_not_the_dead_attach(monkeypatch):
    from types import SimpleNamespace

    import kollabor.commands.system_commands.handlers.system as system_module
    import kollabor.daemon as daemon
    import kollabor.updates as updates
    from kollabor.commands.system_commands.handlers.system import SystemCommandHandler

    handler = SimpleNamespace(
        logger=system_module.logging.getLogger("test"),
        event_bus=SimpleNamespace(get_service=lambda name: None),
    )
    stopped, execs = [], []
    upgraded = updates.AutoUpdateResult(True, "Kollab upgraded: v0.10.3 -> v0.10.4 (via pip).", "pip")
    monkeypatch.setattr(updates, "run_auto_update", lambda: upgraded)
    monkeypatch.setattr(daemon, "stop_daemon", stopped.append)
    monkeypatch.setattr(os, "execv", lambda *args: execs.append(args))
    monkeypatch.setattr(sys, "argv", ["kollab", "--attach", "koordinator"])
    monkeypatch.setenv("KOLLAB_DAEMON_PID", "4242")
    monkeypatch.setenv(daemon.LAUNCH_ARGS_ENV, '["--agent", "lapis"]')

    await SystemCommandHandler.handle_upgrade(handler, None)

    assert stopped == [4242]
    relaunched = execs[0][1]
    assert relaunched[-2:] == ["--agent", "lapis"]
    assert "--attach" not in relaunched


@pytest.mark.asyncio
async def test_a_daemon_only_notifies_so_the_launched_window_installs(monkeypatch):
    from types import SimpleNamespace

    app = object.__new__(TerminalLLMChat)
    app.args = SimpleNamespace(detached=True)
    app.config = _DummyConfig({"kollabor.updates.auto_update_enabled": True})
    app.version_check_service = _DummyVersionCheckService(_release())
    app.renderer = _DummyRenderer()

    def fail_run_auto_update():
        raise AssertionError("a daemon installed the update")

    monkeypatch.setattr(application_module, "run_auto_update", fail_run_auto_update)

    await app._check_for_updates()

    assert any("Update available" in m for m in app.renderer.message_coordinator.messages)


class _Tty:
    """A terminal for stdout: isatty() is true and what is printed is kept."""

    def __init__(self):
        self.text = ""

    def isatty(self):
        return True

    def write(self, text):
        self.text += text

    def flush(self):
        pass


@pytest.fixture
def launch(monkeypatch):
    """`kollab --agent lapis` in a terminal, auto-update on, v9.9.9 released."""
    from types import SimpleNamespace

    import kollabor.binary
    import kollabor.cli as cli
    import kollabor.updates as updates
    import kollabor_config

    stdout = _Tty()
    terminal = SimpleNamespace(stdin=SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(sys, "argv", ["kollab", "--agent", "lapis"])
    # setenv first, so the variable the hook sets is removed again afterwards.
    monkeypatch.setenv(cli.UPDATED_ENV, "")
    monkeypatch.delenv(cli.UPDATED_ENV)
    settings = {"kollabor.updates.auto_update_enabled": True}
    monkeypatch.setattr(kollabor_config, "ConfigService", lambda *args, **kwargs: _DummyConfig(settings))
    checks = []

    async def newer(config):
        checks.append(config)
        return "9.9.9"

    monkeypatch.setattr(cli, "_newer_release_version", newer)
    monkeypatch.setattr(kollabor.binary, "kollab_argv", lambda: ["/opt/kollab"])
    installed = updates.AutoUpdateResult(True, "Kollab upgraded: v0.1.0 -> v9.9.9 (via binary).", "binary")
    monkeypatch.setattr(updates, "run_auto_update", lambda: installed)
    execs = []
    monkeypatch.setattr(cli.os, "execv", lambda *args: execs.append(args))

    def run():
        # Patched here, not in the fixture: pytest's capture swaps both streams per test phase.
        with patch.object(sys, "stdin", terminal.stdin), patch.object(sys, "stdout", stdout):
            cli._update_before_launch()

    return SimpleNamespace(
        cli=cli, settings=settings, checks=checks, execs=execs, stdout=stdout, terminal=terminal, run=run
    )


def test_launch_installs_a_newer_release_then_restarts_into_it(launch):
    launch.run()

    assert launch.execs == [("/opt/kollab", ["/opt/kollab", "--agent", "lapis"])]
    assert os.environ[launch.cli.UPDATED_ENV] == "9.9.9"
    assert "updating v" in launch.stdout.text and "-> v9.9.9" in launch.stdout.text
    assert "Restarting..." in launch.stdout.text


def test_the_restarted_launch_does_not_check_again(launch, monkeypatch):
    monkeypatch.setenv(launch.cli.UPDATED_ENV, "9.9.9")

    launch.run()

    assert launch.checks == [] and launch.execs == []
    # Popped, so the daemon this launch starts never inherits it.
    assert launch.cli.UPDATED_ENV not in os.environ


def test_launch_stays_on_this_version_when_the_install_fails(launch, monkeypatch):
    import kollabor.updates as updates

    failed = updates.AutoUpdateResult(False, "offline", "binary")
    monkeypatch.setattr(updates, "run_auto_update", lambda: failed)

    launch.run()

    assert launch.execs == [] and launch.cli.UPDATED_ENV not in os.environ


def test_launch_does_not_check_when_auto_update_is_off(launch):
    launch.settings["kollabor.updates.auto_update_enabled"] = False

    launch.run()

    assert launch.checks == [] and launch.execs == []


def test_a_failing_check_never_blocks_the_launch(launch, monkeypatch):
    async def broken(config):
        raise OSError("offline")

    monkeypatch.setattr(launch.cli, "_newer_release_version", broken)

    launch.run()

    assert launch.execs == []


@pytest.mark.parametrize(
    "argv",
    [["--detached"], ["--attach", "lapis"], ["--hub", "status"], ["-p"], ["--update"], ["--web-ui"], ["--version"]],
)
def test_only_an_interactive_app_launch_updates_first(launch, monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["kollab", *argv])

    launch.run()

    assert launch.checks == [] and launch.execs == []


def test_a_launch_without_a_terminal_does_not_update(launch, monkeypatch):
    from types import SimpleNamespace

    launch.terminal.stdin = SimpleNamespace(isatty=lambda: False)

    launch.run()

    assert launch.checks == [] and launch.execs == []


def test_no_daemon_launches_update_too_but_never_fork_a_daemon(launch, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["kollab", "--no-daemon"])

    with patch.object(sys, "stdin", launch.terminal.stdin):
        assert launch.cli._should_use_daemon() is False
    launch.run()

    assert launch.execs == [("/opt/kollab", ["/opt/kollab", "--no-daemon"])]
