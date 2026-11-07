"""switch_profile must sync + persist the active profile.

Regression guard for the `/login openai` (and `/setup`, config modal) bug
where the runtime provider switched but profile_manager active state and the
persisted config were never updated -- so the new profile reverted on restart
and the status bar showed the wrong profile.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kollabor.llm.llm_coordinator import LLMService
from kollabor_ai import api_communication_service as api_communication_service_module
from kollabor_ai.api_communication_service import APICommunicationService
from kollabor_ai.profile_manager import LLMProfile


def _coord(profile_auth: str = ""):
    """Minimal LLMCoordinator stand-in with the attrs switch_profile touches."""
    coord = MagicMock()
    coord._provider_lock = asyncio.Lock()
    profile = SimpleNamespace(
        auth_type=profile_auth,
        provider="anthropic",
        model="claude-x",
        api_key="k",
        extra_headers={},
        revision=0,
        to_dict=lambda: {"name": "p", "provider": "anthropic"},
    )
    coord.profile_manager.get_profile.return_value = profile
    coord.profile_manager.set_active_profile = MagicMock(return_value=True)
    coord.api_service.reinitialize_provider = AsyncMock(return_value=True)
    coord.conversation_logger.set_provider = MagicMock()
    coord._provider_registry.get_provider = AsyncMock(
        return_value=SimpleNamespace(provider_name="anthropic", model="claude-x")
    )
    return coord


@pytest.mark.asyncio
async def test_switch_profile_persists_active_by_default():
    coord = _coord()
    with patch(
        "kollabor.llm.llm_coordinator.create_config_from_profile", return_value={}
    ):
        ok = await LLMService.switch_profile(coord, "openai-oauth")

    assert ok is True
    coord.api_service.reinitialize_provider.assert_awaited_once_with(
        coord.profile_manager.get_profile.return_value
    )
    coord.profile_manager.set_active_profile.assert_called_once_with(
        "openai-oauth", persist=True
    )


@pytest.mark.asyncio
async def test_switch_profile_session_only_when_persist_false():
    coord = _coord()
    with patch(
        "kollabor.llm.llm_coordinator.create_config_from_profile", return_value={}
    ):
        ok = await LLMService.switch_profile(coord, "openai-oauth", persist=False)

    assert ok is True
    coord.api_service.reinitialize_provider.assert_awaited_once_with(
        coord.profile_manager.get_profile.return_value
    )
    coord.profile_manager.set_active_profile.assert_called_once_with(
        "openai-oauth", persist=False
    )


@pytest.mark.asyncio
async def test_switch_profile_unknown_profile_does_not_persist():
    coord = _coord()
    coord.profile_manager.get_profile.return_value = None

    ok = await LLMService.switch_profile(coord, "nope")

    assert ok is False
    coord.profile_manager.set_active_profile.assert_not_called()


@pytest.mark.asyncio
async def test_switch_profile_provider_failure_does_not_persist():
    coord = _coord()
    coord._provider_registry.get_provider = AsyncMock(side_effect=RuntimeError("boom"))
    with patch(
        "kollabor.llm.llm_coordinator.create_config_from_profile", return_value={}
    ):
        ok = await LLMService.switch_profile(coord, "openai-oauth")

    assert ok is False
    coord.profile_manager.set_active_profile.assert_not_called()


@pytest.mark.asyncio
async def test_switch_profile_api_reinitialize_failure_does_not_persist():
    """A request-path provider failure must not leave a stale profile active."""
    coord = _coord()
    coord.api_service.reinitialize_provider = AsyncMock(return_value=False)
    with patch(
        "kollabor.llm.llm_coordinator.create_config_from_profile", return_value={}
    ):
        ok = await LLMService.switch_profile(coord, "openai-oauth")

    assert ok is False
    coord.profile_manager.set_active_profile.assert_not_called()


@pytest.mark.asyncio
async def test_switch_profile_survives_persist_failure():
    """A set_active_profile error must not fail the runtime switch."""
    coord = _coord()
    coord.profile_manager.set_active_profile.side_effect = RuntimeError("disk full")
    with patch(
        "kollabor.llm.llm_coordinator.create_config_from_profile", return_value={}
    ):
        ok = await LLMService.switch_profile(coord, "openai-oauth")

    # Runtime switch still succeeded even though persistence raised.
    assert ok is True


def _real_api_switch_coord(tmp_path, monkeypatch, *, fail_target_initialization=False):
    """Build a coordinator around a real API service for rollback regressions."""
    previous_profile = LLMProfile(
        name="previous",
        provider="anthropic",
        model="claude-old",
        api_key="previous-test-key",
    )
    target_profile = LLMProfile(
        name="target",
        provider="openai",
        model="gpt-new",
        api_key="target-test-key",
    )
    previous_provider = SimpleNamespace(
        provider_name="anthropic", model=previous_profile.model
    )
    target_provider = SimpleNamespace(provider_name="openai", model=target_profile.model)
    api_providers = {
        previous_profile.name: previous_provider,
        target_profile.name: target_provider,
    }
    api_provider_names = []

    async def get_api_provider(provider_config):
        name = provider_config["profile"]
        api_provider_names.append(name)
        if fail_target_initialization and name == target_profile.name:
            raise RuntimeError("synthetic target provider failure")
        return api_providers[name]

    class _Config:
        def get(self, _key, default=None):
            return default

    monkeypatch.setattr(
        api_communication_service_module,
        "create_config_from_profile",
        lambda profile_data: {"profile": profile_data["name"]},
    )
    monkeypatch.setattr(
        api_communication_service_module,
        "ProviderRegistry",
        SimpleNamespace(get_provider=AsyncMock(side_effect=get_api_provider)),
    )
    api_service = APICommunicationService(_Config(), tmp_path, previous_profile)

    coord = _coord()
    coord.profile_manager.get_profile.return_value = target_profile
    coord.profile_manager.get_active_profile.return_value = previous_profile
    coord.api_service = api_service
    coord._current_provider = previous_provider
    coord._provider_registry.get_provider = AsyncMock(return_value=target_provider)
    return (
        coord,
        api_service,
        previous_profile,
        previous_provider,
        api_provider_names,
    )


@pytest.mark.asyncio
async def test_switch_profile_api_reinitialize_failure_restores_api_runtime(
    tmp_path, monkeypatch
):
    coord, api_service, previous_profile, previous_provider, api_provider_names = (
        _real_api_switch_coord(
            tmp_path, monkeypatch, fail_target_initialization=True
        )
    )
    assert await api_service.initialize() is True
    assert api_service._provider is previous_provider

    with patch(
        "kollabor.llm.llm_coordinator.create_config_from_profile", return_value={}
    ):
        ok = await LLMService.switch_profile(coord, "target")

    assert ok is False
    assert api_service._profile is previous_profile
    assert api_service._provider is previous_provider
    assert api_service.get_provider_error() is None
    assert api_provider_names == ["previous", "target", "previous"]
    coord.profile_manager.set_active_profile.assert_not_called()


@pytest.mark.asyncio
async def test_switch_profile_coordinator_registry_failure_restores_api_runtime(
    tmp_path, monkeypatch
):
    coord, api_service, previous_profile, previous_provider, api_provider_names = (
        _real_api_switch_coord(tmp_path, monkeypatch)
    )
    assert await api_service.initialize() is True
    assert api_service._provider is previous_provider
    coord._provider_registry.get_provider = AsyncMock(
        side_effect=RuntimeError("synthetic coordinator registry failure")
    )

    with patch(
        "kollabor.llm.llm_coordinator.create_config_from_profile", return_value={}
    ):
        ok = await LLMService.switch_profile(coord, "target")

    assert ok is False
    assert api_service._profile is previous_profile
    assert api_service._provider is previous_provider
    assert api_service.get_provider_error() is None
    assert api_provider_names == ["previous", "target", "previous"]
    coord.profile_manager.set_active_profile.assert_not_called()



@pytest.mark.asyncio
async def test_request_service_reinitialize_clears_old_provider_error(tmp_path):
    """A failed old profile must not poison the first request after a switch."""

    class _Config:
        def get(self, _key, default=None):
            return default

    bad = LLMProfile(name="claude", provider="anthropic", model="claude-sonnet")
    good = LLMProfile(
        name="openrouter",
        provider="openrouter",
        model="tencent/hy3:free",
        base_url="https://openrouter.ai/api/v1",
        api_key="test-key",
    )
    service = APICommunicationService(_Config(), tmp_path, bad)
    provider = SimpleNamespace(provider_name="openrouter", model=good.model)

    with patch(
        "kollabor_ai.api_communication_service.ProviderRegistry.get_provider",
        new=AsyncMock(return_value=provider),
    ):
        assert await service.initialize() is False
        assert "anthropic" in (service.get_provider_error() or "").lower()

        assert await service.reinitialize_provider(good) is True

    assert service.get_provider_error() is None
    assert service.is_provider_available() is True
    assert service.provider_type == "openrouter"
