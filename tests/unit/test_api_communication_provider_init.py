"""Regression tests for safe provider-initialization diagnostics."""

import asyncio
import logging
from types import SimpleNamespace

import pytest

import kollabor_ai.api_communication_service as api_module
import kollabor_ai.oauth as oauth_module


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [ValueError, RuntimeError])
async def test_provider_initialization_log_omits_provider_secret(
    error_type, monkeypatch, caplog
):
    secret = "AIzaSyDUMMY_PROVIDER_SECRET_20261106"
    profile = SimpleNamespace(
        name="redaction-test",
        auth_type="api_key",
        to_dict=lambda: {"provider": "openai", "api_key": secret},
    )
    service = api_module.APICommunicationService.__new__(
        api_module.APICommunicationService
    )
    service._profile = profile
    service._provider = None
    service._provider_error = None

    async def fail_provider_initialization(_config):
        raise error_type(f"provider failure included credential {secret}")

    class FailingProviderRegistry:
        get_provider = staticmethod(fail_provider_initialization)

    monkeypatch.setattr(api_module, "create_config_from_profile", lambda _: {})
    monkeypatch.setattr(api_module, "ProviderRegistry", FailingProviderRegistry)
    caplog.set_level(logging.WARNING, logger=api_module.__name__)

    await service._initialize_provider()

    assert secret not in caplog.text
    assert error_type.__name__ in caplog.text


def _reinitialization_profile(name, api_key="test-key-not-a-real-secret"):
    return SimpleNamespace(
        name=name,
        auth_type="api_key",
        api_key=api_key,
        to_dict=lambda: {
            "name": name,
            "provider": "openai",
            "api_key": api_key,
        },
    )


def _bare_reinitialization_service(profile, provider):
    service = api_module.APICommunicationService.__new__(
        api_module.APICommunicationService
    )
    service._profile = profile
    service._provider = provider
    service._provider_generation = 0
    service._provider_error = None
    service._media_resolver = None
    service.update_from_profile = lambda _profile: None
    service._configure_generated_image_store = lambda: None
    return service


@pytest.mark.asyncio
async def test_stale_failed_reinitialization_does_not_clear_later_provider(
    monkeypatch,
):
    failed_profile = _reinitialization_profile("stale-failure")
    later_profile = _reinitialization_profile("later-success")
    old_provider = SimpleNamespace(provider_name="old", model="old-model")
    later_provider = SimpleNamespace(provider_name="later", model="later-model")
    service = _bare_reinitialization_service(failed_profile, old_provider)
    failure_started = asyncio.Event()
    release_failure = asyncio.Event()

    async def get_provider(config):
        if config["name"] == failed_profile.name:
            failure_started.set()
            await release_failure.wait()
            raise RuntimeError("the earlier provider initialization failed")
        return later_provider

    class ControlledProviderRegistry:
        @staticmethod
        async def get_provider(config):
            return await get_provider(config)

    monkeypatch.setattr(api_module, "create_config_from_profile", lambda data: data)
    monkeypatch.setattr(api_module, "ProviderRegistry", ControlledProviderRegistry)

    stale_switch = asyncio.create_task(
        service.reinitialize_provider(failed_profile)
    )
    await asyncio.wait_for(failure_started.wait(), timeout=2)

    assert await service.reinitialize_provider(later_profile) is True
    assert service._provider is later_provider

    release_failure.set()
    assert await stale_switch is False
    assert service._profile is later_profile
    assert service._provider is later_provider
    assert service._provider_error is None


@pytest.mark.asyncio
async def test_stale_oauth_refresh_does_not_mutate_later_profile(monkeypatch):
    oauth_profile = SimpleNamespace(
        name="openai-oauth",
        auth_type="oauth",
        api_key="old-oauth-token",
        extra_headers={},
        model="gpt-5-codex",
        to_dict=lambda: {"name": "openai-oauth", "api_key": "old-oauth-token"},
    )
    native_profile = _reinitialization_profile("native", "sk-native")
    native_profile.extra_headers = {"X-Native": "preserved"}
    old_provider = SimpleNamespace(provider_name="old", model="old-model")
    native_provider = SimpleNamespace(provider_name="native", model="native-model")
    service = _bare_reinitialization_service(oauth_profile, old_provider)
    token_load_started = asyncio.Event()
    release_token_load = asyncio.Event()
    provider_configs = []

    class DelayedTokenStorage:
        async def load_tokens(self, *_args, **_kwargs):
            token_load_started.set()
            await release_token_load.wait()
            return SimpleNamespace(
                access_token="oauth-token-from-stale-call",
                account_id="stale-oauth-account",
            )

    class ControlledProviderRegistry:
        @staticmethod
        async def get_provider(config):
            provider_configs.append(config)
            return native_provider

    monkeypatch.setattr(oauth_module, "OAuthTokenStorage", DelayedTokenStorage)
    monkeypatch.setattr(api_module, "create_config_from_profile", lambda data: data)
    monkeypatch.setattr(api_module, "ProviderRegistry", ControlledProviderRegistry)

    stale_switch = asyncio.create_task(service.reinitialize_provider(oauth_profile))
    await asyncio.wait_for(token_load_started.wait(), timeout=2)

    assert await service.reinitialize_provider(native_profile) is True
    assert service._profile is native_profile
    assert service._provider is native_provider

    release_token_load.set()
    assert await stale_switch is False

    assert native_profile.api_key == "sk-native"
    assert native_profile.extra_headers == {"X-Native": "preserved"}
    assert "ChatGPT-Account-Id" not in native_profile.extra_headers
    assert len(provider_configs) == 1


@pytest.mark.asyncio
async def test_stale_401_oauth_refresh_does_not_mutate_later_profile(monkeypatch):
    oauth_profile = SimpleNamespace(
        name="openai-oauth",
        auth_type="oauth",
        api_key="old-oauth-token",
        extra_headers={},
        model="gpt-5-codex",
        to_dict=lambda: {"name": "openai-oauth", "api_key": "old-oauth-token"},
    )
    native_profile = _reinitialization_profile("native", "sk-native")
    native_profile.extra_headers = {"X-Native": "preserved"}
    native_provider = SimpleNamespace(provider_name="native", model="native-model")
    service = _bare_reinitialization_service(oauth_profile, native_provider)
    service._oauth_refresh_lock = asyncio.Lock()
    token_load_started = asyncio.Event()
    release_token_load = asyncio.Event()
    provider_configs = []

    class DelayedTokenStorage:
        async def load_tokens(self, *_args, **_kwargs):
            token_load_started.set()
            await release_token_load.wait()
            return SimpleNamespace(
                access_token="oauth-token-from-stale-call",
                account_id="stale-oauth-account",
            )

    class ControlledProviderRegistry:
        @staticmethod
        async def get_provider(config):
            provider_configs.append(config)
            return native_provider

    monkeypatch.setattr(oauth_module, "OAuthTokenStorage", DelayedTokenStorage)
    monkeypatch.setattr(api_module, "create_config_from_profile", lambda data: data)
    monkeypatch.setattr(api_module, "ProviderRegistry", ControlledProviderRegistry)

    stale_refresh = asyncio.create_task(
        service._refresh_oauth_after_401("old-oauth-token")
    )
    await asyncio.wait_for(token_load_started.wait(), timeout=2)

    assert await service.reinitialize_provider(native_profile) is True
    assert service._profile is native_profile
    assert service._provider is native_provider

    release_token_load.set()
    assert await stale_refresh is False

    assert native_profile.api_key == "sk-native"
    assert native_profile.extra_headers == {"X-Native": "preserved"}
    assert "ChatGPT-Account-Id" not in native_profile.extra_headers
    assert len(provider_configs) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [ValueError, RuntimeError])
async def test_reinitialize_provider_log_omits_provider_secret(
    error_type, monkeypatch, caplog
):
    secret = "sk-wrapper-log-secret-20261106"
    profile = _reinitialization_profile("wrapper-redaction", secret)
    service = _bare_reinitialization_service(profile, None)

    async def fail_provider_initialization(_config):
        raise error_type(f"provider failure included credential {secret}")

    class FailingProviderRegistry:
        get_provider = staticmethod(fail_provider_initialization)

    monkeypatch.setattr(api_module, "create_config_from_profile", lambda _: {})
    monkeypatch.setattr(api_module, "ProviderRegistry", FailingProviderRegistry)
    caplog.set_level(logging.WARNING, logger=api_module.__name__)

    assert await service.reinitialize_provider(profile) is False

    assert secret not in caplog.text
    assert error_type.__name__ in caplog.text
    assert service._provider_error == (
        f"provider failure included credential {secret}"
    )
