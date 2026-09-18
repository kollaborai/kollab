from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor_ai.api_communication_service import APICommunicationService
from kollabor_ai.message_content import EphemeralImageStore, normalize_message_content
from kollabor_ai.providers.errors import ProviderError


@pytest.mark.asyncio
async def test_image_request_is_rejected_before_provider_io(tmp_path: Path):
    config = MagicMock()
    config.get = lambda key, default=None: {
        "kollabor.llm.enable_streaming": False,
        "kollabor.llm.use_explicit_tool_accumulation": False,
        "kollabor.llm.max_retries": 0,
    }.get(key, default)

    profile = MagicMock()
    profile.provider = "openai"
    profile.name = "vision-gate-test"
    profile.get_model.return_value = "model-not-in-catalog"
    profile.get_temperature.return_value = 0.7
    profile.get_max_tokens.return_value = 4096
    profile.get_timeout.return_value = 30
    profile.to_dict.return_value = {
        "provider": "openai",
        "model": "model-not-in-catalog",
        "api_key": "sk-test",
    }

    service = APICommunicationService(config, tmp_path, profile)
    provider = MagicMock()
    provider.provider_name = "openai"
    provider.model = "model-not-in-catalog"
    provider.supports_vision = False
    provider.call = AsyncMock()
    service._provider = provider
    service._initialized = True

    store = EphemeralImageStore()
    content = normalize_message_content(
        [{"type": "image", "image": "data:image/png;base64,iVBORw0KGgo="}],
        store,
    )

    with pytest.raises(ProviderError, match="does not accept image input"):
        await service.call_llm([{"role": "user", "content": content}])

    provider.call.assert_not_awaited()


def test_token_estimate_keeps_tool_call_payloads_visible():
    tool_calls = [
        {
            "id": "call-1",
            "function": {"name": "read", "arguments": '{"path":"/tmp/a"}'},
        }
    ]

    assert APICommunicationService._estimate_tokens(tool_calls) > 1


def test_image_capability_resolves_before_provider_initialization(tmp_path: Path):
    config = MagicMock()
    config.get = lambda key, default=None: {
        "kollabor.llm.raw_log_max_file_mb": 0,
        "kollabor.llm.raw_log_max_total_mb": 0,
        "kollabor.llm.enable_streaming": False,
        "kollabor.llm.use_explicit_tool_accumulation": False,
    }.get(key, default)

    profile = MagicMock()
    profile.provider = "openai"
    profile.name = "vision-preinit-test"
    profile.get_provider.return_value = "openai"
    profile.get_model.return_value = "gpt-5.6-luna"
    profile.get_temperature.return_value = 0.7
    profile.get_max_tokens.return_value = 4096
    profile.get_timeout.return_value = 30

    service = APICommunicationService(config, tmp_path, profile)

    assert service._provider is None
    assert service.supports_image_input() is True
