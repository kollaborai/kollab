"""Regression tests for provider error normalization and retry boundaries."""

import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from aiohttp import ClientConnectionError

from kollabor_ai.api_communication_service import APICommunicationService
from kollabor_ai.providers.anthropic_provider import AnthropicProvider
from kollabor_ai.providers.custom_provider import CustomConfig, CustomProvider
from kollabor_ai.providers.errors import (
    APIConnectionError,
    APITimeoutError,
    InvalidRequestError,
    ProviderError,
    RateLimitError,
    ServerError,
    TransientHTTPError,
    map_anthropic_error,
    map_openai_error,
    parse_retry_after,
    parse_retry_after_headers,
)
from kollabor_ai.providers.gemini_provider import GeminiProvider
from kollabor_ai.providers.models import (
    AnthropicConfig,
    GeminiConfig,
    OpenAIResponsesConfig,
    ProviderType,
    StreamingResponse,
    TextDelta,
)
from kollabor_ai.providers.openai_responses_provider import OpenAIResponsesProvider


def _http_status_error(status_code: int, headers=None, message="provider error"):
    request = httpx.Request("POST", "https://provider.test/v1/messages")
    response = httpx.Response(
        status_code,
        headers=headers or {},
        request=request,
        text=message,
    )
    return httpx.HTTPStatusError(message, request=request, response=response)


def _make_service(max_retries=5):
    config = MagicMock()
    config.get = lambda key, default=None: {
        "kollabor.llm.enable_streaming": False,
        "kollabor.llm.use_explicit_tool_accumulation": False,
        "kollabor.llm.max_retries": max_retries,
    }.get(key, default)

    profile = MagicMock()
    profile.get_model.return_value = "gpt-4"
    profile.get_temperature.return_value = 0.7
    profile.get_max_tokens.return_value = 4096
    profile.get_timeout.return_value = 30
    profile.provider = "openai"
    profile.name = "retry-hardening"
    profile.to_dict.return_value = {
        "provider": "openai",
        "model": "gpt-4",
        "api_key": "sk-test",
        "temperature": 0.7,
        "max_tokens": 4096,
        "timeout": 30,
    }

    service = APICommunicationService(config, Path("/tmp/retry-hardening"), profile)
    service._log_raw_interaction = MagicMock()
    service._initialized = True
    return service


def test_retry_after_accepts_http_date_and_invalid_values():
    now = datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc)
    retry_at = now + timedelta(seconds=17)

    assert parse_retry_after("17", now=now) == 17.0
    assert parse_retry_after(format_datetime(retry_at, usegmt=True), now=now) == 17.0
    assert parse_retry_after("not-a-delay", now=now) is None


@pytest.mark.parametrize(
    ("status_code", "expected_type"),
    [
        (408, APITimeoutError),
        (409, TransientHTTPError),
        (425, ProviderError),
        (501, ServerError),
        (529, ServerError),
    ],
)
def test_openai_http_statuses_map_to_retryable_types(status_code, expected_type):
    mapped = map_openai_error(
        _http_status_error(status_code),
        "openai_responses",
    )

    assert isinstance(mapped, expected_type)
    if status_code in (409, 501, 529):
        assert mapped.status_code == status_code


def test_early_data_status_is_not_classified_as_retryable():
    mapped = map_openai_error(_http_status_error(425), "openai_responses")

    assert type(mapped) is ProviderError
    assert mapped.error_code == "http_error_425"


def test_retry_after_headers_prefer_milliseconds_and_preserve_server_delay():
    assert parse_retry_after_headers({"Retry-After-Ms": "2500"}) == 2.5
    mapped = map_openai_error(
        _http_status_error(503, headers={"retry-after-ms": "2500"}),
        "openai_responses",
    )

    assert isinstance(mapped, ServerError)
    assert mapped.retry_after == 2.5


def test_conflict_status_preserves_retry_after():
    mapped = map_openai_error(
        _http_status_error(409, headers={"retry-after": "3"}),
        "openai_responses",
    )

    assert isinstance(mapped, TransientHTTPError)
    assert mapped.retry_after == 3.0


def test_anthropic_http_date_rate_limit_preserves_retry_after():
    now = datetime.now(timezone.utc)
    retry_at = now + timedelta(seconds=30)
    mapped = map_anthropic_error(
        _http_status_error(
            429,
            headers={"retry-after": format_datetime(retry_at, usegmt=True)},
            message="slow down",
        ),
        "anthropic",
    )

    assert isinstance(mapped, RateLimitError)
    assert mapped.retry_after is not None
    assert 29 <= mapped.retry_after <= 30


def test_retry_loop_uses_configured_count_for_typed_529():
    service = _make_service(max_retries=2)
    error = ServerError("gateway unavailable", "openai", status_code=529)
    provider = MagicMock()
    provider.provider_name = "openai"
    provider.call = AsyncMock(side_effect=error)
    service._provider = provider

    async def run():
        with patch.object(service, "_sleep_or_cancel", new_callable=AsyncMock) as sleep:
            with pytest.raises(ServerError):
                await service.call_llm([{"role": "user", "content": "hello"}])
        return sleep

    sleep = asyncio.run(run())
    assert provider.call.await_count == 3
    assert sleep.await_count == 2


def test_retry_loop_fails_fast_when_server_delay_exceeds_cap():
    service = _make_service(max_retries=5)
    error = RateLimitError("slow down", "openai", retry_after=121)
    provider = MagicMock()
    provider.provider_name = "openai"
    provider.call = AsyncMock(side_effect=error)
    service._provider = provider

    async def run():
        with patch.object(service, "_sleep_or_cancel", new_callable=AsyncMock) as sleep:
            with pytest.raises(RateLimitError):
                await service.call_llm([{"role": "user", "content": "hello"}])
        return sleep

    sleep = asyncio.run(run())
    assert provider.call.await_count == 1
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_fallback_backoff_uses_jitter():
    service = _make_service(max_retries=1)
    provider = MagicMock()
    provider.provider_name = "openai"
    provider.call = AsyncMock(
        side_effect=RateLimitError("slow down", "openai", retry_after=None)
    )
    service._provider = provider

    with patch.object(service, "_sleep_or_cancel", new_callable=AsyncMock) as sleep:
        with patch(
            "kollabor_ai.api_communication_service.random.uniform",
            return_value=0.75,
        ):
            with pytest.raises(RateLimitError):
                await service.call_llm([{"role": "user", "content": "hello"}])

    sleep.assert_awaited_once_with(3.75)


def test_permanent_typed_error_does_not_retry_because_message_mentions_500():
    service = _make_service(max_retries=5)
    error = InvalidRequestError(
        "invalid max_tokens value; provider documentation mentions 500",
        "openai",
        error_code="invalid_request",
    )
    provider = MagicMock()
    provider.provider_name = "openai"
    provider.call = AsyncMock(side_effect=error)
    service._provider = provider

    async def run():
        with patch.object(service, "_sleep_or_cancel", new_callable=AsyncMock) as sleep:
            with pytest.raises(InvalidRequestError):
                await service.call_llm([{"role": "user", "content": "hello"}])
        return sleep

    sleep = asyncio.run(run())
    assert provider.call.await_count == 1
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_partial_stream_failure_is_not_replayed():
    service = _make_service(max_retries=5)
    service.enable_streaming = True

    class PartialStreamProvider:
        provider_name = "openai"
        model = "gpt-4"

        def __init__(self):
            self.stream_calls = 0

        async def stream(self, messages, tools=None):
            self.stream_calls += 1
            yield StreamingResponse(delta=TextDelta(content="Hel"))
            raise APIConnectionError(
                "peer closed after partial output",
                "openai",
                error_code="connection_error",
            )

    provider = PartialStreamProvider()
    service._provider = provider
    callback = AsyncMock()

    with patch.object(service, "_sleep_or_cancel", new_callable=AsyncMock) as sleep:
        with pytest.raises(APIConnectionError):
            await service.call_llm(
                [{"role": "user", "content": "hello"}],
                streaming_callback=callback,
            )

    assert provider.stream_calls == 1
    callback.assert_awaited_once_with("Hel")
    sleep.assert_not_awaited()


def test_httpx_transport_error_maps_for_gemini_path():
    raw_error = httpx.RemoteProtocolError("incomplete chunked read")

    mapped = map_openai_error(raw_error, "gemini")

    assert isinstance(mapped, APIConnectionError)
    assert mapped.error_code == "connection_error"


@pytest.mark.asyncio
async def test_custom_provider_maps_http_status_and_retry_after():
    retry_at = datetime.now(timezone.utc) + timedelta(seconds=30)

    class FakeResponse:
        status = 429
        headers = {"retry-after": format_datetime(retry_at, usegmt=True)}

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def text(self):
            return "slow down"

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def post(self, *args, **kwargs):
            return FakeResponse()

    config = CustomConfig(
        provider=ProviderType.CUSTOM,
        api_key="local-key",
        model="local-model",
        base_url="http://localhost:1234/v1",
    )
    provider = CustomProvider(config)

    with patch(
        "kollabor_ai.providers.custom_provider.aiohttp.ClientSession",
        return_value=FakeSession(),
    ):
        with pytest.raises(RateLimitError) as exc_info:
            await provider._make_request([{"role": "user", "content": "hi"}])

    assert exc_info.value.retry_after is not None


@pytest.mark.asyncio
async def test_custom_provider_maps_connection_failure():
    class FailingSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def post(self, *args, **kwargs):
            raise ClientConnectionError("connection refused")

    config = CustomConfig(
        provider=ProviderType.CUSTOM,
        api_key="local-key",
        model="local-model",
        base_url="http://localhost:1234/v1",
    )
    provider = CustomProvider(config)

    with patch(
        "kollabor_ai.providers.custom_provider.aiohttp.ClientSession",
        return_value=FailingSession(),
    ):
        with pytest.raises(APIConnectionError) as exc_info:
            await provider._make_request([{"role": "user", "content": "hi"}])

    assert exc_info.value.error_code == "connection_error"


@pytest.mark.asyncio
async def test_gemini_provider_maps_httpx_transport_failure():
    config = GeminiConfig(
        provider=ProviderType.GEMINI,
        api_key="gemini-key",
        model="gemini-2.0-flash",
    )
    provider = GeminiProvider(config)
    provider._initialized = True
    provider._client = MagicMock()
    provider._client.post = AsyncMock(
        side_effect=httpx.RemoteProtocolError("peer closed")
    )

    with pytest.raises(APIConnectionError) as exc_info:
        await provider.call([{"role": "user", "content": "hi"}])

    assert exc_info.value.error_code == "connection_error"


@pytest.mark.asyncio
async def test_openai_responses_preserves_retryable_http_status():
    config = OpenAIResponsesConfig(
        provider=ProviderType.OPENAI_RESPONSES,
        api_key="sk-test-key",
        model="gpt-5.4",
    )
    provider = OpenAIResponsesProvider(config)
    provider._initialized = True
    provider._client = MagicMock()
    response = MagicMock()
    response.status_code = 529
    response.headers = {"content-type": "application/json"}
    response.json.return_value = {"error": {"message": "gateway unavailable"}}
    provider._client.post = AsyncMock(return_value=response)

    with pytest.raises(ServerError) as exc_info:
        await provider.call([{"role": "user", "content": "hi"}])

    assert exc_info.value.status_code == 529


@pytest.mark.asyncio
async def test_anthropic_preserves_retryable_http_status():
    config = AnthropicConfig(
        provider=ProviderType.ANTHROPIC,
        api_key="sk-ant-test-key",
        model="claude-test",
    )
    provider = AnthropicProvider(config)
    provider._initialized = True
    provider._client = MagicMock()
    response = MagicMock()
    response.status_code = 529
    response.headers = {}
    response.json.return_value = {"error": {"message": "gateway unavailable"}}
    provider._client.post = AsyncMock(return_value=response)

    with pytest.raises(ServerError) as exc_info:
        await provider.call([{"role": "user", "content": "hi"}])

    assert exc_info.value.status_code == 529
