import subprocess
from dataclasses import dataclass

import pytest

import kollabor.application as application_module
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


def test_auto_update_dispatches_to_uv_tool_when_installed_package(
    tmp_path, monkeypatch
):
    import kollabor.updates.auto_update as auto_update

    calls = []

    def fake_which(name):
        return "/usr/bin/uv" if name == "uv" else None

    def fake_run_cmd(*args):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "upgraded\n", "")

    monkeypatch.setattr(auto_update.shutil, "which", fake_which)
    monkeypatch.setattr(auto_update, "_run_cmd", fake_run_cmd)

    result = auto_update.run_auto_update(repo_root=tmp_path)

    assert result.success is True
    assert result.method == "uv"
    assert calls == [("uv", "tool", "upgrade", "kollab")]


def test_auto_update_falls_back_to_current_python_pip(tmp_path, monkeypatch):
    import kollabor.updates.auto_update as auto_update

    calls = []

    monkeypatch.setattr(auto_update.shutil, "which", lambda name: None)
    monkeypatch.setattr(auto_update.sys, "executable", "/venv/bin/python")

    def fake_run_cmd(*args):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "upgraded\n", "")

    monkeypatch.setattr(auto_update, "_run_cmd", fake_run_cmd)

    result = auto_update.run_auto_update(repo_root=tmp_path)

    assert result.success is True
    assert result.method == "pip"
    assert calls == [
        ("/venv/bin/python", "-m", "pip", "install", "--upgrade", "kollab")
    ]


@pytest.mark.asyncio
async def test_startup_auto_updates_when_release_available_and_enabled(monkeypatch):
    release = _release()
    app = object.__new__(TerminalLLMChat)
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
    app.config = _DummyConfig({"kollabor.updates.auto_update_enabled": False})
    app.version_check_service = _DummyVersionCheckService(release)
    app.renderer = _DummyRenderer()

    def fail_run_auto_update():
        raise AssertionError("auto update should not run")

    monkeypatch.setattr(application_module, "run_auto_update", fail_run_auto_update)

    await app._check_for_updates()

    messages = app.renderer.message_coordinator.messages
    assert any("Update available" in message for message in messages)
