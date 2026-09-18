"""Tests for hosted image-generation transformation and private storage."""

import base64
import json
import os
from pathlib import Path

import pytest

from kollabor_ai.generated_image_artifacts import (
    GeneratedImageArtifactError,
    GeneratedImageArtifactStore,
    redact_generated_image_data,
)
from kollabor_ai.providers.models import (
    AuthType,
    GeneratedImageContent,
    ImageGenerationDelta,
    OpenAIResponsesConfig,
    ProviderType,
    TextDelta,
)
from kollabor_ai.providers.openai_responses_provider import OpenAIResponsesProvider
from kollabor_ai.providers.openai_responses_transformer import (
    OpenAIResponsesTransformer,
)

PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)
PNG_RESULT = base64.b64encode(PNG_BYTES).decode("ascii")


class _ByteStream:
    def __init__(self, chunks: list[bytes]):
        self.chunks = chunks

    async def aiter_bytes(self):
        for chunk in self.chunks:
            yield chunk


def _sse_event(event: str, payload: dict) -> bytes:
    return (
        f"event: {event}\n" f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
    ).encode()


def _oauth_provider(model: str = "gpt-5.6-luna") -> OpenAIResponsesProvider:
    return OpenAIResponsesProvider(
        OpenAIResponsesConfig(
            provider=ProviderType.OPENAI_RESPONSES,
            auth_type=AuthType.OAUTH,
            api_key="oauth-test-token",
            model=model,
            base_url="https://chatgpt.com/backend-api/codex",
        )
    )


def test_artifact_store_writes_private_png_and_returns_opaque_id(tmp_path: Path):
    store = GeneratedImageArtifactStore(tmp_path / "session-images")

    content = store.write_base64(
        PNG_RESULT,
        revised_prompt="a small seedling",
        provider_reference="ig_call_1",
    )

    assert isinstance(content, GeneratedImageContent)
    assert content.media_id.startswith("img_")
    assert content.media_type == "image/png"
    assert content.width == 1
    assert content.height == 1
    assert str(tmp_path) not in json.dumps(content.model_dump())
    assert PNG_RESULT not in json.dumps(content.model_dump())

    path = store._path_for_testing(content.media_id)
    assert path is not None
    assert path.read_bytes() == PNG_BYTES
    assert os.stat(store.root).st_mode & 0o777 == 0o700
    assert os.stat(path).st_mode & 0o777 == 0o600


def test_artifact_store_rejects_non_image_data(tmp_path: Path):
    store = GeneratedImageArtifactStore(tmp_path / "session-images")

    with pytest.raises(GeneratedImageArtifactError, match="format"):
        store.write_base64(base64.b64encode(b"not an image").decode("ascii"))


def test_transformer_persists_image_and_redacts_raw_response(tmp_path: Path):
    store = GeneratedImageArtifactStore(tmp_path / "session-images")
    response = {
        "id": "resp_image_1",
        "status": "completed",
        "output": [
            {
                "type": "image_generation_call",
                "id": "ig_call_1",
                "result": PNG_RESULT,
                "revised_prompt": "a small seedling",
            }
        ],
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }

    unified = OpenAIResponsesTransformer.transform_response(
        response, "gpt-5.6-luna", artifact_store=store
    )

    assert isinstance(unified.content[0], GeneratedImageContent)
    assert unified.content[0].media_id.startswith("img_")
    assert "Open: /artifact open img_" in unified.get_text_content()
    assert PNG_RESULT not in json.dumps(unified.model_dump())
    assert unified.raw_response is not None
    assert (
        unified.raw_response["output"][0]["result"] == "[generated image data redacted]"
    )


def test_redaction_is_scoped_to_image_generation_calls():
    payload = {
        "output": [
            {"type": "image_generation_call", "result": PNG_RESULT},
            {"type": "message", "content": [{"type": "text", "text": "ok"}]},
        ]
    }

    redacted = redact_generated_image_data(payload)

    assert redacted["output"][0]["result"] == "[generated image data redacted]"
    assert redacted["output"][1]["content"][0]["text"] == "ok"
    assert PNG_RESULT not in json.dumps(redacted)


@pytest.mark.parametrize(
    "model",
    ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol", "gpt-6-astra"],
)
def test_oauth_request_auto_exposes_hosted_image_tool(model: str):
    provider = _oauth_provider(model)
    request = provider._prepare_request(
        [{"role": "user", "content": "draw a seedling"}],
        tools=None,
        stream=True,
    )

    assert request["tools"] == [{"type": "image_generation"}]


def test_oauth_request_does_not_auto_expose_hosted_image_tool_for_unknown_model():
    provider = _oauth_provider("gpt-5.5")
    request = provider._prepare_request(
        [{"role": "user", "content": "draw a seedling"}],
        tools=None,
        stream=True,
    )

    assert "tools" not in request


def test_hosted_tools_are_preserved_and_public_route_does_not_auto_add():
    hosted = {"type": "image_generation", "quality": "high"}
    function = {
        "type": "function",
        "function": {
            "name": "lookup",
            "description": "Look something up",
            "parameters": {"type": "object"},
        },
    }
    provider = _oauth_provider()
    request = provider._prepare_request(
        [{"role": "user", "content": "draw and look up"}],
        tools=[function, hosted],
        stream=True,
    )

    assert request["tools"][0] == {
        "type": "function",
        "name": "lookup",
        "description": "Look something up",
        "parameters": {"type": "object"},
    }
    assert request["tools"][1] == hosted

    public_config = OpenAIResponsesConfig(
        provider=ProviderType.OPENAI_RESPONSES,
        api_key="sk-test-key",
        model="gpt-5.6-luna",
    )
    public_provider = OpenAIResponsesProvider(public_config)
    public_request = public_provider._prepare_request(
        [{"role": "user", "content": "draw"}], tools=None, stream=False
    )
    assert "tools" not in public_request


def test_image_generation_sse_event_is_typed_and_redacted():
    provider = _oauth_provider()
    event = provider._parse_sse_event(
        "response.image_generation_call.generating",
        json.dumps({"id": "ig_call_1"}).encode(),
    )

    assert event is not None
    assert isinstance(event.delta, ImageGenerationDelta)
    assert event.delta.status == "generating"

    final = provider._parse_sse_event(
        "response.completed",
        json.dumps(
            {
                "response": {
                    "status": "completed",
                    "output": [
                        {
                            "type": "image_generation_call",
                            "result": PNG_RESULT,
                        }
                    ],
                }
            }
        ).encode(),
    )
    assert final is not None
    assert PNG_RESULT not in json.dumps(final.raw_chunk)
    assert final._raw_payload is not None
    assert final._raw_payload["response"]["output"][0]["result"] == PNG_RESULT


@pytest.mark.asyncio
async def test_sse_reconciles_completed_image_item_into_empty_final_output():
    provider = _oauth_provider()
    response = _ByteStream(
        [
            _sse_event(
                "response.output_item.added",
                {
                    "item": {
                        "type": "image_generation_call",
                        "id": "ig_call_smoke",
                        "status": "in_progress",
                    }
                },
            ),
            _sse_event(
                "response.image_generation_call.generating",
                {
                    "type": "response.image_generation_call.generating",
                    "item_id": "ig_call_smoke",
                },
            ),
            _sse_event(
                "response.output_item.done",
                {
                    "item": {
                        "type": "image_generation_call",
                        "id": "ig_call_smoke",
                        "status": "completed",
                        "result": PNG_RESULT,
                    }
                },
            ),
            _sse_event(
                "response.output_text.delta",
                {"delta": "status: generated"},
            ),
            _sse_event(
                "response.completed",
                {
                    "response": {
                        "status": "completed",
                        "output": [],
                        "usage": {"input_tokens": 10, "output_tokens": 5},
                    }
                },
            ),
        ]
    )

    chunks = [chunk async for chunk in provider._parse_sse_stream(response)]
    final = chunks[-1]

    assert final.is_final is True
    assert final._raw_payload is not None
    output = final._raw_payload["response"]["output"]
    assert [
        item["id"] for item in output if item["type"] == "image_generation_call"
    ] == ["ig_call_smoke"]
    assert output[0]["result"] == PNG_RESULT
    assert PNG_RESULT not in json.dumps(final.raw_chunk)


@pytest.mark.asyncio
async def test_sse_parser_handles_large_line_without_truncating():
    provider = _oauth_provider()
    text = "x" * 100_000
    response = _ByteStream(
        [
            b"event: response.output_text.delta\n",
            b"data: " + json.dumps({"delta": text}).encode() + b"\n\n",
        ]
    )

    chunks = [chunk async for chunk in provider._parse_sse_stream(response)]

    assert len(chunks) == 1
    assert isinstance(chunks[0].delta, TextDelta)
    assert chunks[0].delta.content == text
