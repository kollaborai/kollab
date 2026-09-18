import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor_ai.api_communication_service import APICommunicationService
from kollabor_ai.message_content import EphemeralImageStore, normalize_message_content
from kollabor_ai.providers.errors import ProviderError
from kollabor_ai.providers.models import StreamingResponse, TextDelta, UsageInfo

PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
PNG_RESULT = base64.b64encode(PNG_BYTES).decode("ascii")


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


@pytest.mark.asyncio
async def test_streamed_generated_image_is_persisted_and_raw_log_is_redacted(
    tmp_path: Path,
):
    config = MagicMock()
    config.get = lambda key, default=None: default

    profile = MagicMock()
    profile.provider = "openai_responses"
    profile.name = "generated-image-stream-test"
    profile.get_model.return_value = "gpt-5.6-luna"
    profile.get_temperature.return_value = 0.7
    profile.get_max_tokens.return_value = 4096
    profile.get_timeout.return_value = 30

    service = APICommunicationService(config, tmp_path, profile)

    class _Provider:
        model = "gpt-5.6-luna"

        def set_generated_image_store(self, store):
            self.store = store

        async def stream(self, **kwargs):
            response = {
                "status": "completed",
                "output": [
                    {
                        "type": "image_generation_call",
                        "id": "ig_call_1",
                        "result": PNG_RESULT,
                    }
                ],
                "usage": {"input_tokens": 10, "output_tokens": 5},
            }
            final = StreamingResponse(
                delta=TextDelta(content=""),
                usage=UsageInfo(
                    prompt_tokens=10,
                    completion_tokens=5,
                    total_tokens=15,
                ),
                is_final=True,
                raw_chunk={
                    "event": "response.completed",
                    "response": {
                        "status": "completed",
                        "output": [
                            {
                                "type": "image_generation_call",
                                "id": "ig_call_1",
                                "result": "[generated image data redacted]",
                            }
                        ],
                    },
                },
            )
            final._raw_payload = {"event": "response.completed", "response": response}
            yield final

    service._provider = _Provider()
    service._initialized = True
    service.set_session_id("session-1")

    content = await service._call_provider_stream(
        [{"role": "user", "content": "draw a seedling"}]
    )

    assert "Generated image: img_" in content
    assert len(service.last_generated_images) == 1
    assert PNG_RESULT not in json.dumps(service.last_raw_chunks)
    media_id = service.last_generated_images[0].media_id
    path = service._generated_image_store._path_for_testing(media_id)
    assert path is not None
    assert path.read_bytes() == PNG_BYTES
