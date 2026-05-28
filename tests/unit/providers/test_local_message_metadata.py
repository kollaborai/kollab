"""Provider request builders must strip local-only message metadata."""

import unittest
from unittest.mock import AsyncMock, MagicMock

from kollabor_ai.providers.custom_provider import CustomConfig, CustomProvider
from kollabor_ai.providers.models import OpenAIConfig, OpenRouterConfig, ProviderType
from kollabor_ai.providers.openai_provider import OpenAIProvider
from kollabor_ai.providers.openrouter_provider import OpenRouterProvider


def hud_messages():
    return [
        {
            "role": "user",
            "content": "<agent_hud>\n+ done\n</agent_hud>",
            "agent_hud": True,
            "agent_hud_sources": ["hub"],
        }
    ]


class LocalMessageMetadataTests(unittest.TestCase):
    def test_openai_request_strips_agent_hud_metadata_without_mutating_input(self):
        config = OpenAIConfig(
            provider=ProviderType.OPENAI,
            api_key="sk-test-key",
            model="gpt-5.4",
        )
        provider = OpenAIProvider(config)
        messages = hud_messages()

        request = provider._prepare_request_params(
            messages=messages,
            tools=None,
            stream=False,
        )

        self.assertEqual(
            request["messages"],
            [{"role": "user", "content": "<agent_hud>\n+ done\n</agent_hud>"}],
        )
        self.assertTrue(messages[0]["agent_hud"])
        self.assertEqual(messages[0]["agent_hud_sources"], ["hub"])


class LocalMessageMetadataAsyncTests(unittest.IsolatedAsyncioTestCase):
    async def test_openrouter_request_strips_agent_hud_metadata(self):
        config = OpenRouterConfig(
            provider=ProviderType.OPENROUTER,
            api_key="sk-or-test-key",
            model="openai/gpt-4",
            max_tokens=16,
        )
        provider = OpenRouterProvider(config)
        provider._initialized = True
        provider._model_info.get_model_limits = AsyncMock(return_value=None)
        provider._model_info.compute_effective_max_tokens = MagicMock(return_value=16)
        mock_response = MagicMock()
        mock_response.model_dump.return_value = {
            "choices": [
                {
                    "message": {"role": "assistant", "content": "ok"},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
            },
        }
        provider._client = MagicMock()
        provider._client.chat.completions.create = AsyncMock(
            return_value=mock_response
        )

        messages = hud_messages()
        await provider.call(messages)

        request = provider.last_request_payload
        self.assertEqual(
            request["messages"],
            [{"role": "user", "content": "<agent_hud>\n+ done\n</agent_hud>"}],
        )
        self.assertTrue(messages[0]["agent_hud"])

    async def test_custom_request_strips_agent_hud_metadata(self):
        class FakeResponse:
            status = 200

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            async def json(self):
                return {"ok": True}

            async def text(self):
                return ""

        class FakeSession:
            def __init__(self):
                self.payload = None

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return None

            def post(self, _url, **kwargs):
                self.payload = kwargs["json"]
                return FakeResponse()

        fake_session = FakeSession()
        config = CustomConfig(
            provider=ProviderType.CUSTOM,
            api_key="test-key",
            model="local-model",
            base_url="http://localhost:1234/v1",
        )
        provider = CustomProvider(config)

        import kollabor_ai.providers.custom_provider as custom_provider

        original_client_session = custom_provider.aiohttp.ClientSession
        custom_provider.aiohttp.ClientSession = lambda: fake_session
        try:
            messages = hud_messages()
            await provider._make_request(messages)
        finally:
            custom_provider.aiohttp.ClientSession = original_client_session

        self.assertEqual(
            fake_session.payload["messages"],
            [{"role": "user", "content": "<agent_hud>\n+ done\n</agent_hud>"}],
        )
        self.assertTrue(messages[0]["agent_hud"])


if __name__ == "__main__":
    unittest.main()
