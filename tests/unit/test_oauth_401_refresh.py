"""A 401 on an OAuth profile refreshes the token, rebuilds the provider and replays once."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor_ai.api_communication_service import APICommunicationService
from kollabor_ai.providers.errors import AuthenticationError

MESSAGES = [{"role": "user", "content": "hi"}]


def _auth_error():
    return AuthenticationError(
        "token expired", "openai_responses", error_code="authentication_error"
    )


def _service(auth_type="oauth"):
    config = MagicMock()
    config.get = lambda key, default=None: {
        "kollabor.llm.enable_streaming": False,
        "kollabor.llm.max_retries": 0,
    }.get(key, default)
    profile = MagicMock()
    profile.get_model.return_value = "gpt-5"
    profile.get_temperature.return_value = 0.7
    profile.get_max_tokens.return_value = 4096
    profile.get_timeout.return_value = 30
    profile.provider = "openai_responses"
    profile.name = "openai-oauth"
    profile.auth_type = auth_type
    profile.api_key = "old-token"
    service = APICommunicationService(config, None, profile)
    service._provider = MagicMock(provider_name="openai_responses")
    service._initialized = True
    return service


def _wire(service, results, refreshed_key="new-token"):
    service._call_provider_nonstream = AsyncMock(side_effect=results)

    async def refresh():
        await asyncio.sleep(0)
        if refreshed_key:
            service._profile.api_key = refreshed_key

    service._refresh_oauth_token = AsyncMock(side_effect=refresh)
    service._initialize_provider = AsyncMock()


@pytest.mark.asyncio
async def test_401_refreshes_token_rebuilds_provider_and_replays_once():
    service = _service()
    _wire(service, [_auth_error(), "ok"])

    assert await service.call_llm(MESSAGES) == "ok"

    assert service._call_provider_nonstream.await_count == 2
    service._refresh_oauth_token.assert_awaited_once()
    service._initialize_provider.assert_awaited_once()


@pytest.mark.asyncio
async def test_failed_refresh_surfaces_the_401():
    service = _service()
    _wire(service, [_auth_error()], refreshed_key=None)

    with pytest.raises(AuthenticationError):
        await service.call_llm(MESSAGES)

    assert service._call_provider_nonstream.await_count == 1
    service._initialize_provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_second_401_is_not_retried_again():
    service = _service()
    _wire(service, [_auth_error(), _auth_error()])

    with pytest.raises(AuthenticationError):
        await service.call_llm(MESSAGES)

    assert service._call_provider_nonstream.await_count == 2
    service._refresh_oauth_token.assert_awaited_once()


@pytest.mark.asyncio
async def test_api_key_profile_does_not_refresh():
    service = _service(auth_type="")
    _wire(service, [_auth_error()])

    with pytest.raises(AuthenticationError):
        await service.call_llm(MESSAGES)

    service._refresh_oauth_token.assert_not_awaited()


@pytest.mark.asyncio
async def test_concurrent_401s_refresh_once():
    service = _service()
    _wire(service, [_auth_error(), _auth_error(), "ok", "ok"])

    results = await asyncio.gather(
        service.call_llm(MESSAGES), service.call_llm(MESSAGES)
    )

    assert results == ["ok", "ok"]
    service._refresh_oauth_token.assert_awaited_once()
