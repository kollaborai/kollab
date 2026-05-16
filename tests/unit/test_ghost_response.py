import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from kollabor_ai.api_communication_service import APICommunicationService
from kollabor_ai.providers.errors import EmptyResponseError
from kollabor_ai.providers.models import StreamingResponse, TextDelta


class EmptyStreamProvider:
    provider_name = "anthropic"

    async def stream(self, messages, tools=None):
        if False:
            yield None


class EmptyThenTextProvider:
    provider_name = "anthropic"

    def __init__(self) -> None:
        self.calls = 0

    async def stream(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            if False:
                yield None
            return
        yield StreamingResponse(
            delta=TextDelta(content="ok"),
            raw_chunk={"type": "content_block_delta"},
        )


def make_service() -> APICommunicationService:
    config = MagicMock()
    config.get = lambda key, default=None: {
        "kollabor.llm.enable_streaming": True,
        "kollabor.llm.use_explicit_tool_accumulation": False,
        "kollabor.llm.debug_tool_stream_path": False,
    }.get(key, default)

    profile = MagicMock()
    profile.provider = "anthropic"
    profile.name = "ghost-test"
    profile.get_model.return_value = "glm-test"
    profile.get_temperature.return_value = 0.7
    profile.get_max_tokens.return_value = 4096
    profile.get_timeout.return_value = 30
    profile.to_dict.return_value = {
        "provider": "anthropic",
        "model": "glm-test",
        "api_key": "sk-test",
        "temperature": 0.7,
        "max_tokens": 4096,
        "timeout": 30,
    }

    service = APICommunicationService(config, Path("/tmp/test_raw"), profile)
    service._provider = EmptyStreamProvider()
    service._initialized = True
    return service


class TestGhostResponseHandling(unittest.IsolatedAsyncioTestCase):
    async def test_empty_stream_raises_empty_response_error(self) -> None:
        service = make_service()

        with self.assertRaises(EmptyResponseError):
            await service._call_provider_stream(
                [{"role": "user", "content": "review this and report back"}],
                tools=[],
            )

    async def test_call_llm_retries_empty_stream_before_succeeding(self) -> None:
        service = make_service()
        provider = EmptyThenTextProvider()
        service._provider = provider
        on_rate_limit = AsyncMock()

        with patch(
            "kollabor_ai.api_communication_service.asyncio.sleep",
            new=AsyncMock(),
        ):
            content = await service.call_llm(
                [{"role": "user", "content": "review this and report back"}],
                tools=[],
                on_rate_limit=on_rate_limit,
            )

        self.assertEqual(content, "ok")
        self.assertEqual(provider.calls, 2)
        on_rate_limit.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
