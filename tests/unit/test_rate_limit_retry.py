"""Unit tests for rate-limit retry logic in APICommunicationService.

Tests the retry loop in call_llm():
  - max_retries = 5
  - base_delay = 5.0s with exponential backoff
  - catches RateLimitError and "429" in error message
  - uses retry_after from error when available
  - delay capped at 120s
  - non-rate-limit errors raise immediately

Run: python -m pytest tests/unit/test_rate_limit_retry.py -v
"""

import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from kollabor_ai.api_communication_service import APICommunicationService
from kollabor_ai.providers.errors import (
    AuthenticationError,
    RateLimitError,
    ServerError,
)


def _make_service() -> APICommunicationService:
    config = MagicMock()
    config.get = lambda key, default=None: {
        "kollabor.llm.enable_streaming": False,
        "kollabor.llm.use_explicit_tool_accumulation": False,
    }.get(key, default)

    profile = MagicMock()
    profile.get_model.return_value = "gpt-4"
    profile.get_temperature.return_value = 0.7
    profile.get_max_tokens.return_value = 4096
    profile.get_timeout.return_value = 30
    profile.provider = "openai"
    profile.name = "test-profile"
    profile.to_dict.return_value = {
        "provider": "openai",
        "model": "gpt-4",
        "api_key": "sk-test",
        "temperature": 0.7,
        "max_tokens": 4096,
        "timeout": 30,
    }

    raw_dir = Path("/tmp/test_raw")
    service = APICommunicationService(config, raw_dir, profile)
    service.current_session_id = "test-session"
    return service


def _make_provider(raise_error=None, return_value="ok"):
    provider = MagicMock()
    provider.provider_name = "openai"
    provider.model = "gpt-4"
    if raise_error:
        provider.call = AsyncMock(side_effect=raise_error)
    else:
        provider.call = AsyncMock(
            return_value=MagicMock(
                get_text_content=lambda: return_value,
                get_tool_uses=lambda: [],
                get_thinking_content=lambda: None,
                usage=MagicMock(
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                ),
                finish_reason="stop",
            )
        )
    return provider


class TestRateLimitRetry(unittest.IsolatedAsyncioTestCase):
    """Rate-limit retry logic in call_llm()."""

    async def test_no_retry_on_non_rate_limit_error(self) -> None:
        service = _make_service()
        error = AuthenticationError("bad key", "openai")
        service._provider = _make_provider(raise_error=error)
        service._initialized = True

        with patch("kollabor_ai.api_communication_service.asyncio.sleep") as mock_sleep:
            with self.assertRaises(AuthenticationError):
                await service.call_llm([{"role": "user", "content": "hi"}])
            mock_sleep.assert_not_called()

    async def test_retry_on_rate_limit_error(self) -> None:
        service = _make_service()
        rl_error = RateLimitError("slow down", "openai", retry_after=0.01)
        service._provider = _make_provider(raise_error=rl_error)
        service._initialized = True

        with patch("kollabor_ai.api_communication_service.asyncio.sleep") as mock_sleep:
            with self.assertRaises(RateLimitError):
                await service.call_llm([{"role": "user", "content": "hi"}])

        # 5 retries (attempts 0..4 sleep, attempt 5 raises)
        self.assertEqual(mock_sleep.call_count, 5)

    async def test_retry_on_429_in_message(self) -> None:
        service = _make_service()
        # Generic error with 429 in message (not RateLimitError instance)
        error = RuntimeError("Server returned 429 Too Many Requests")
        service._provider = _make_provider(raise_error=error)
        service._initialized = True

        with patch("kollabor_ai.api_communication_service.asyncio.sleep") as mock_sleep:
            with self.assertRaises(RuntimeError):
                await service.call_llm([{"role": "user", "content": "hi"}])

        self.assertGreater(mock_sleep.call_count, 0)

    async def test_success_after_retry(self) -> None:
        service = _make_service()
        rl_error = RateLimitError("slow down", "openai", retry_after=0.01)
        provider = _make_provider(return_value="hello world")
        # fail first, succeed second
        provider.call = AsyncMock(side_effect=[rl_error, provider.call.return_value])
        service._provider = provider
        service._initialized = True

        with patch("kollabor_ai.api_communication_service.asyncio.sleep") as mock_sleep:
            result = await service.call_llm([{"role": "user", "content": "hi"}])

        self.assertEqual(result, "hello world")
        self.assertEqual(mock_sleep.call_count, 1)

    async def test_exhausted_retries_raises(self) -> None:
        service = _make_service()
        rl_error = RateLimitError("slow down", "openai", retry_after=0.01)
        service._provider = _make_provider(raise_error=rl_error)
        service._initialized = True

        with patch("kollabor_ai.api_communication_service.asyncio.sleep"):
            with self.assertRaises(RateLimitError):
                await service.call_llm([{"role": "user", "content": "hi"}])

    async def test_retry_after_from_error(self) -> None:
        service = _make_service()
        rl_error = RateLimitError("slow down", "openai", retry_after=7.5)
        service._provider = _make_provider(raise_error=rl_error)
        service._initialized = True

        delays = []
        with patch(
            "kollabor_ai.api_communication_service.asyncio.sleep",
            side_effect=lambda d: delays.append(d),
        ):
            with self.assertRaises(RateLimitError):
                await service.call_llm([{"role": "user", "content": "hi"}])

        # All delays should be 7.5 (retry_after overrides exponential backoff)
        for d in delays:
            self.assertEqual(d, 7.5)

    async def test_exponential_backoff_when_no_retry_after(self) -> None:
        service = _make_service()
        rl_error = RateLimitError("slow down", "openai", retry_after=None)
        service._provider = _make_provider(raise_error=rl_error)
        service._initialized = True

        delays = []
        with patch(
            "kollabor_ai.api_communication_service.asyncio.sleep",
            side_effect=lambda d: delays.append(d),
        ):
            with self.assertRaises(RateLimitError):
                await service.call_llm([{"role": "user", "content": "hi"}])

        # base_delay=5.0, exponential: 5, 10, 20, 40, 80 (5 retries)
        self.assertEqual(delays[0], 5.0)  # 5 * 2^0
        self.assertEqual(delays[1], 10.0)  # 5 * 2^1
        self.assertEqual(delays[2], 20.0)  # 5 * 2^2
        self.assertEqual(delays[3], 40.0)  # 5 * 2^3
        self.assertEqual(delays[4], 80.0)  # 5 * 2^4

    async def test_delay_capped_at_120(self) -> None:
        service = _make_service()
        rl_error = RateLimitError("slow down", "openai", retry_after=None)
        service._provider = _make_provider(raise_error=rl_error)
        service._initialized = True

        delays = []
        with patch(
            "kollabor_ai.api_communication_service.asyncio.sleep",
            side_effect=lambda d: delays.append(d),
        ):
            with self.assertRaises(RateLimitError):
                await service.call_llm([{"role": "user", "content": "hi"}])

        for d in delays:
            self.assertLessEqual(d, 120.0)

    async def test_server_error_retries_then_raises(self) -> None:
        service = _make_service()
        error = ServerError("internal error", "openai", status_code=500)
        service._provider = _make_provider(raise_error=error)
        service._initialized = True

        with patch("kollabor_ai.api_communication_service.asyncio.sleep") as mock_sleep:
            with self.assertRaises(ServerError):
                await service.call_llm([{"role": "user", "content": "hi"}])
            # server errors (500) now retry with exponential backoff
            mock_sleep.assert_called()

    async def test_stats_tracked_on_rate_limit_failure(self) -> None:
        service = _make_service()
        rl_error = RateLimitError("slow down", "openai", retry_after=0.01)
        service._provider = _make_provider(raise_error=rl_error)
        service._initialized = True

        with patch("kollabor_ai.api_communication_service.asyncio.sleep"):
            with self.assertRaises(RateLimitError):
                await service.call_llm([{"role": "user", "content": "hi"}])

        # Each retry attempt increments failed_requests
        stats = service.get_connection_stats()
        self.assertGreater(stats["failed_requests"], 0)


if __name__ == "__main__":
    unittest.main()
