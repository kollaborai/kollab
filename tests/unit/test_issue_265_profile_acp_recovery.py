"""Regressions for ACP profile recovery and profile-copy behavior."""

import asyncio
import json
import logging
from dataclasses import fields
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from kollabor_engine.routes import profiles as profile_routes

from kollabor.commands.system_commands.handlers.model import ModelCommandHandler
from kollabor.llm.llm_coordinator import LLMService
from kollabor_ai import LLMProfile, ProfileManager
from kollabor_ai.api_communication_service import APICommunicationService
from kollabor_ai.loadout_manager import LoadoutManager
from kollabor_ai.profile_manager import KEYRING_SENTINEL_PREFIX
from kollabor_ai.providers.errors import ProviderError

_PROFILE_ENV = (
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_AUTH_TOKEN",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_DEFAULT_OPUS_MODEL",
    "ANTHROPIC_BASE_URL",
    "OPENAI_API_KEY",
    "OPENAI_MODEL",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_MODEL",
    "AZURE_OPENAI_ENDPOINT",
    "GEMINI_API_KEY",
    "GEMINI_MODEL",
    "OPENROUTER_API_KEY",
    "OPENROUTER_MODEL",
    "XAI_API_KEY",
    "XAI_MODEL",
    "ZAI_API_KEY",
    "ZAI_MODEL",
    "MOONSHOT_API_KEY",
    "MOONSHOT_MODEL",
    "KOLLAB_MODEL",
    "KOLLAB_API_KEY",
    "KOLLAB_ANTHROPIC_COPY_265_API_KEY",
    "KOLLAB_NO_AUTO_DETECT",
)


@pytest.fixture

def isolated_profile_manager(monkeypatch, tmp_path):
    for name in _PROFILE_ENV:
        monkeypatch.delenv(name, raising=False)
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ProfileManager, "_detect_oauth_provider", lambda self: None)
    return ProfileManager(), home


def _write_config(home: Path, llm: dict) -> None:
    config_path = home / ".kollab" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps({"kollabor": {"llm": llm}}), encoding="utf-8")


def _acp_profile_data(tmp_path: Path) -> dict:
    return {
        "provider": "acp",
        "model": "",
        "command": ["claude-agent-acp", "--verbose"],
        "cwd": str(tmp_path),
        "workspace": str(tmp_path / "workspace"),
        "pass_env": ["CLAUDE_CONFIG_DIR"],
        "env": {"CLAUDE_CONFIG_DIR": str(tmp_path / "claude-config")},
        "install_guidance": "Install the test ACP adapter",
        "description": "Claude Code over ACP",
        "timeout": 17,
        "extra_headers": {"x-profile-test": "preserved"},
    }


def test_invalid_profile_is_skipped_and_reported_without_breaking_lists(
    isolated_profile_manager, tmp_path, caplog
):
    manager, home = isolated_profile_manager
    _write_config(
        home,
        {
            "active_profile": "working",
            "default_profile": "working",
            "profiles": {
                "broken-acp": {"provider": "acp", "command": []},
                " ": {"provider": "anthropic", "model": "invalid-name"},
                "working": {"provider": "anthropic", "model": "claude-test"},
            },
        },
    )
    with caplog.at_level(logging.WARNING, logger="kollabor_ai.profile_manager"):
        manager = ProfileManager()

    assert manager.get_profile("working") is not None
    assert manager.get_profile("broken-acp") is None
    assert "broken-acp" in manager.get_invalid_profiles()
    assert "<blank-name>" in manager.get_invalid_profiles()
    assert "broken-acp" in caplog.text
    assert any(profile.name == "working" for profile in manager.list_profiles())


def test_missing_active_and_default_profiles_are_both_warned(
    isolated_profile_manager, tmp_path, caplog
):
    _initial_manager, home = isolated_profile_manager
    _write_config(
        home,
        {
            "active_profile": "missing-active",
            "default_profile": "missing-default",
            "profiles": {},
        },
    )
    with caplog.at_level(logging.WARNING, logger="kollabor_ai.profile_manager"):
        ProfileManager()

    assert "missing-active" in caplog.text
    assert "missing-default" in caplog.text


def test_duplicate_acp_profile_preserves_and_persists_full_command(
    isolated_profile_manager, tmp_path
):
    manager, home = isolated_profile_manager
    source = LLMProfile.from_dict("claude-code-custom", _acp_profile_data(tmp_path))
    assert manager.add_profile(source)

    clone = manager.duplicate_profile("claude-code-custom", "claude-code-custom-copy")

    assert clone.name == "claude-code-custom-copy"
    assert clone.command == source.command
    assert clone.cwd == source.cwd
    assert clone.workspace == source.workspace
    assert clone.pass_env == source.pass_env
    assert clone.env == source.env
    assert clone.env is not source.env
    assert clone.install_guidance == source.install_guidance
    for profile_field in fields(source):
        if profile_field.name != "name":
            assert getattr(clone, profile_field.name) == getattr(source, profile_field.name)

    saved = json.loads((home / ".kollab" / "config.json").read_text(encoding="utf-8"))
    saved_copy = saved["kollabor"]["llm"]["profiles"]["claude-code-custom-copy"]
    assert saved_copy["command"] == list(source.command)
    assert saved_copy["cwd"] == source.cwd
    assert saved_copy["workspace"] == source.workspace
    assert saved_copy["pass_env"] == list(source.pass_env)
    assert saved_copy["env"] == source.env
    assert saved_copy["install_guidance"] == source.install_guidance


def test_profiles_api_copies_acp_from_server_without_requiring_model_text(
    isolated_profile_manager, tmp_path, monkeypatch
):
    manager, _home = isolated_profile_manager
    source = LLMProfile.from_dict("claude-code-custom", _acp_profile_data(tmp_path))
    assert manager.add_profile(source)
    monkeypatch.setattr(profile_routes, "_get_profile_manager", lambda: manager)

    request = profile_routes.CreateProfileRequest(
        name="claude-code-custom-copy",
        copy_from="claude-code-custom",
    )
    response = asyncio.run(profile_routes.create_profile(request))

    assert response["command"] == list(source.command)
    assert response["cwd"] == source.cwd
    assert response["workspace"] == source.workspace
    assert response["pass_env"] == list(source.pass_env)
    assert response["env_keys"] == ["CLAUDE_CONFIG_DIR"]
    assert "env" not in response


def test_profiles_api_rejects_new_acp_profile_without_command(
    isolated_profile_manager, monkeypatch
):
    manager, _home = isolated_profile_manager
    monkeypatch.setattr(profile_routes, "_get_profile_manager", lambda: manager)
    request = profile_routes.CreateProfileRequest(
        name="missing-command", provider="acp", model=""
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(profile_routes.create_profile(request))

    assert exc.value.status_code == 422
    assert "command" in exc.value.detail.lower()


def test_model_copy_command_preserves_acp_command(
    isolated_profile_manager, tmp_path
):
    manager, _home = isolated_profile_manager
    source = LLMProfile.from_dict(
        "claude-code-custom", _acp_profile_data(tmp_path)
    )
    assert manager.add_profile(source)
    handler = ModelCommandHandler(
        command_registry=None, event_bus=None, profile_manager=manager
    )

    result = asyncio.run(
        handler.handle_model(
            SimpleNamespace(args=["copy", "claude-code-custom", "cli-copy"])
        )
    )

    assert result.success
    copied = manager.get_profile("cli-copy")
    assert copied is not None
    assert copied.command == source.command
    assert copied.env == source.env
    assert manager.get_active_profile().name != "cli-copy"


class _Config:
    def get(self, _key, default=None):
        return default


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("anthropic", "profile 'zai-primary'"),
        ("acp", "ACP harness"),
    ],
)
def test_none_provider_stream_raises_named_safe_provider_error(
    tmp_path, provider, expected
):
    profile = LLMProfile.from_dict(
        "zai-primary",
        {
            "provider": provider,
            "model": "glm-test" if provider != "acp" else "",
            "command": ["claude-agent-acp"] if provider == "acp" else None,
            "base_url": "https://api.z.ai/api/anthropic",
        },
    )
    service = APICommunicationService(_Config(), tmp_path, profile)
    service._provider = None

    with pytest.raises(ProviderError) as exc:
        asyncio.run(
            service.call_llm([{"role": "user", "content": "hello"}])
        )

    assert expected in exc.value.safe_message
    assert "NoneType" not in exc.value.safe_message
    assert exc.value.error_code == "provider_unavailable"


def test_profiles_api_keeps_valid_list_and_exposes_skipped_profile_diagnostics(
    isolated_profile_manager, monkeypatch
):
    manager, _home = isolated_profile_manager
    manager._invalid_profile_errors["broken-acp"] = "ACP command is missing"
    monkeypatch.setattr(profile_routes, "_get_profile_manager", lambda: manager)

    response = asyncio.run(profile_routes.list_profiles())

    assert response["count"] == len(response["profiles"])
    assert response["invalid_profiles"] == {
        "broken-acp": "ACP command is missing"
    }


def test_missing_default_loadout_is_warned_and_ignored(
    isolated_profile_manager, caplog
):
    manager, _home = isolated_profile_manager

    class DefaultConfig(_Config):
        def get(self, key, default=None):
            if key == LoadoutManager.DEFAULT_CONFIG_KEY:
                return "missing-profile-265"
            return default

    loadouts = LoadoutManager(manager, DefaultConfig())
    with caplog.at_level(logging.WARNING, logger="kollabor_ai.loadout_manager"):
        value = loadouts.get_default()

    assert value is None
    assert "missing-profile-265" in caplog.text
    assert "does not match" in caplog.text


def test_llm_service_routes_acp_switch_to_harness_without_native_factory(
    isolated_profile_manager, tmp_path, monkeypatch
):
    manager, _home = isolated_profile_manager
    profile = LLMProfile.from_dict(
        "claude-code-custom", _acp_profile_data(tmp_path)
    )
    assert manager.add_profile(profile)

    class EventBus:
        def __init__(self, orchestrator):
            self.orchestrator = orchestrator

        def get_service(self, name):
            assert name == "acp_orchestrator"
            return self.orchestrator

    orchestrator = Mock()
    orchestrator.start = AsyncMock()
    orchestrator.close = AsyncMock()
    service = LLMService.__new__(LLMService)
    service._provider_lock = asyncio.Lock()
    service.profile_manager = manager
    service.event_bus = EventBus(orchestrator)
    service.api_service = Mock()
    service.conversation_logger = Mock()
    service._current_provider = object()
    monkeypatch.setattr(
        LLMService, "_announce_hub_model_switch", AsyncMock()
    )

    switched = asyncio.run(service.switch_profile(profile.name, persist=False))

    assert switched is True
    orchestrator.start.assert_awaited_once_with(profile)
    orchestrator.close.assert_not_awaited()
    service.api_service.reinitialize_provider.assert_not_called()
    assert manager.get_active_profile().name == profile.name
    assert service._current_provider is None
    service.conversation_logger.set_provider.assert_called_once_with("acp")


def test_model_profile_switch_delegates_to_acp_aware_llm_service(
    isolated_profile_manager, tmp_path
):
    manager, _home = isolated_profile_manager
    profile = LLMProfile.from_dict(
        "claude-code-custom", _acp_profile_data(tmp_path)
    )
    assert manager.add_profile(profile)

    class SwitchingService:
        def __init__(self):
            self.api_service = Mock()
            self.switch_profile = AsyncMock(side_effect=self.switch)

        async def switch(self, name):
            assert manager.set_active_profile(name, persist=False)
            return True

    class EventBus:
        def __init__(self, service):
            self.service = service

        def get_service(self, name):
            return self.service if name == "llm_service" else None

    service = SwitchingService()
    handler = ModelCommandHandler(
        command_registry=None,
        event_bus=EventBus(service),
        profile_manager=manager,
    )

    result = asyncio.run(handler._switch_profile(profile.name))

    assert result.success
    assert "ACP adapter: claude-agent-acp" in result.message
    service.switch_profile.assert_awaited_once_with(profile.name)
    service.api_service.reinitialize_provider.assert_not_called()


def test_copying_invalid_skipped_profile_returns_actionable_error(
    isolated_profile_manager, monkeypatch
):
    manager, _home = isolated_profile_manager
    manager._invalid_profile_errors["broken-acp"] = (
        "ACP command is missing; restore the command"
    )
    monkeypatch.setattr(profile_routes, "_get_profile_manager", lambda: manager)

    request = profile_routes.CreateProfileRequest(
        name="broken-acp-copy", copy_from="broken-acp"
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(profile_routes.create_profile(request))

    assert exc.value.status_code == 422
    assert "restore the command" in exc.value.detail


def test_failed_profile_persistence_does_not_leave_in_memory_profile(
    isolated_profile_manager, monkeypatch
):
    manager, _home = isolated_profile_manager
    monkeypatch.setattr(manager, "save_profile_values_to_config", lambda _profile: False)
    monkeypatch.setattr(profile_routes, "_get_profile_manager", lambda: manager)
    request = profile_routes.CreateProfileRequest(
        name="failed-save", provider="anthropic", model="claude-test"
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(profile_routes.create_profile(request))

    assert exc.value.status_code == 500
    assert manager.get_profile("failed-save") is None


def test_environment_backed_api_key_is_not_copied_or_persisted(
    isolated_profile_manager, monkeypatch
):
    manager, home = isolated_profile_manager
    source = LLMProfile.from_dict(
        "anthropic-auto-265",
        {
            "provider": "anthropic",
            "model": "claude-test",
            "api_key": "source-secret-value",
            "api_key_from_env": True,
        },
    )
    assert manager.add_profile(source)
    new_name = "anthropic-copy-265"

    with pytest.raises(ValueError, match="environment-backed API key") as exc:
        manager.duplicate_profile(source.name, new_name)
    assert "KOLLAB_ANTHROPIC_COPY_265_API_KEY" in str(exc.value)
    assert manager.get_profile(new_name) is None

    monkeypatch.setenv("KOLLAB_ANTHROPIC_COPY_265_API_KEY", "target-secret-value")
    copied = manager.duplicate_profile(source.name, new_name)

    assert copied.api_key == ""
    assert copied.api_key_from_env is True
    assert copied.get_api_key() == "target-secret-value"
    saved = json.loads((home / ".kollab" / "config.json").read_text(encoding="utf-8"))
    saved_copy = saved["kollabor"]["llm"]["profiles"][new_name]
    assert "api_key" not in saved_copy
    assert "source-secret-value" not in (home / ".kollab" / "config.json").read_text(
        encoding="utf-8"
    )


def test_duplicate_profile_keeps_keyring_sentinel_reference(
    isolated_profile_manager
):
    manager, home = isolated_profile_manager
    sentinel = f"{KEYRING_SENTINEL_PREFIX}shared-account"
    source = LLMProfile.from_dict(
        "keyring-source",
        {"provider": "anthropic", "model": "claude-test", "api_key": sentinel},
    )
    assert manager.add_profile(source)

    copied = manager.duplicate_profile("keyring-source", "keyring-copy")

    assert copied.api_key == sentinel
    saved = json.loads((home / ".kollab" / "config.json").read_text(encoding="utf-8"))
    assert (
        saved["kollabor"]["llm"]["profiles"]["keyring-copy"]["api_key"]
        == sentinel
    )


def test_ui_copy_reports_environment_credential_requirements(
    isolated_profile_manager, monkeypatch
):
    manager, _home = isolated_profile_manager
    source = LLMProfile.from_dict(
        "anthropic-auto-265",
        {
            "provider": "anthropic",
            "model": "claude-test",
            "api_key": "source-secret-value",
            "api_key_from_env": True,
        },
    )
    assert manager.add_profile(source)
    monkeypatch.setattr(profile_routes, "_get_profile_manager", lambda: manager)
    request = profile_routes.CreateProfileRequest(
        name="anthropic-copy-265", copy_from="anthropic-auto-265"
    )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(profile_routes.create_profile(request))

    assert exc.value.status_code == 422
    assert "KOLLAB_ANTHROPIC_COPY_265_API_KEY" in exc.value.detail
    assert manager.get_profile("anthropic-copy-265") is None
