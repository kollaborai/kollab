"""Every streaming provider must report a max-output-tokens stop as "length".

queue_processor auto-continues a truncated reply only when
api_service.last_stop_reason == "length", and the service takes that value from
the last truthy StreamingResponse.finish_reason. Each test feeds canned wire
chunks that end in a token-cap stop and reads the stop reason the same way.
"""

import json

import pytest

from kollabor_ai.providers.custom_provider import CustomConfig, CustomProvider
from kollabor_ai.providers.gemini_transformer import GeminiResponseTransformer
from kollabor_ai.providers.models import OpenAIResponsesConfig, ProviderType
from kollabor_ai.providers.openai_responses_provider import OpenAIResponsesProvider
from kollabor_ai.providers.openai_responses_transformer import (
    OpenAIResponsesTransformer,
)
from kollabor_ai.providers.transformers import (
    AnthropicResponseTransformer,
    OpenAIResponseTransformer,
)


def last_stop_reason(chunks):
    """Mirror APICommunicationService: the last truthy finish_reason wins."""
    reasons = [c.finish_reason for c in chunks if c and c.finish_reason]
    return reasons[-1] if reasons else "stop"


# --- Anthropic ---------------------------------------------------------------


def _anthropic(stop_reason):
    wire = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 9}}},
        {
            "type": "message_delta",
            "delta": {"stop_reason": stop_reason},
            "usage": {"output_tokens": 8},
        },
        {"type": "message_stop"},
    ]
    return [
        AnthropicResponseTransformer.transform_anthropic_chunk(c, "claude")
        for c in wire
    ]


def test_anthropic_stream_max_tokens_is_length():
    assert last_stop_reason(_anthropic("max_tokens")) == "length"


def test_anthropic_stream_end_turn_is_not_length():
    assert last_stop_reason(_anthropic("end_turn")) != "length"


# --- OpenAI chat and OpenRouter (both stream through transform_openai_chunk) --


def test_openai_chat_stream_length_survives_trailing_usage_chunk():
    wire = [
        {"choices": [{"delta": {"content": "cut o"}, "finish_reason": None}]},
        {"choices": [{"delta": {}, "finish_reason": "length"}]},
        {
            "choices": [],
            "usage": {"prompt_tokens": 9, "completion_tokens": 8, "total_tokens": 17},
        },
    ]
    chunks = [OpenAIResponseTransformer.transform_openai_chunk(c, "gpt") for c in wire]
    assert last_stop_reason(chunks) == "length"


# --- Gemini ------------------------------------------------------------------

_GEMINI_USAGE = {
    "promptTokenCount": 9,
    "candidatesTokenCount": 8,
    "totalTokenCount": 17,
}


def _gemini(*wire):
    return [
        GeminiResponseTransformer.transform_streaming_chunk(c, "gemini") for c in wire
    ]


def test_gemini_stream_max_tokens_is_length():
    chunks = _gemini(
        {"candidates": [{"content": {"role": "model", "parts": [{"text": "cut o"}]}}]},
        {
            "candidates": [
                {"content": {"role": "model", "parts": []}, "finishReason": "MAX_TOKENS"}
            ],
            "usageMetadata": _GEMINI_USAGE,
        },
    )
    assert last_stop_reason(chunks) == "length"


def test_gemini_stream_max_tokens_chunk_without_usage_is_not_dropped():
    chunks = _gemini(
        {"candidates": [{"content": {"role": "model"}, "finishReason": "MAX_TOKENS"}]}
    )
    assert last_stop_reason(chunks) == "length"


def test_gemini_stream_normal_stop_is_not_length():
    chunks = _gemini(
        {
            "candidates": [
                {"content": {"role": "model", "parts": []}, "finishReason": "STOP"}
            ],
            "usageMetadata": _GEMINI_USAGE,
        }
    )
    assert last_stop_reason(chunks) != "length"


def test_gemini_call_max_tokens_is_length():
    response = GeminiResponseTransformer.transform_response(
        {
            "candidates": [
                {
                    "content": {"role": "model", "parts": [{"text": "cut o"}]},
                    "finishReason": "MAX_TOKENS",
                }
            ],
            "usageMetadata": _GEMINI_USAGE,
        },
        "gemini",
    )
    assert response.finish_reason == "length"


# --- OpenAI Responses and ChatGPT/Codex --------------------------------------

_CAPPED = {
    "status": "incomplete",
    "incomplete_details": {"reason": "max_output_tokens"},
    "output": [],
    "usage": {"input_tokens": 9, "output_tokens": 8},
}
_DONE = {
    "status": "completed",
    "output": [],
    "usage": {"input_tokens": 9, "output_tokens": 8},
}


class _Bytes:
    """Stand-in for an httpx streaming response: yields raw SSE bytes."""

    def __init__(self, *events):
        self._events = events

    async def aiter_bytes(self):
        for event, payload in self._events:
            yield f"event: {event}\ndata: {json.dumps(payload)}\n\n".encode()


async def _responses_chunks(final_event, final_response):
    provider = OpenAIResponsesProvider(
        OpenAIResponsesConfig(
            provider=ProviderType.OPENAI_RESPONSES, api_key="sk-test", model="gpt-5.4"
        )
    )
    stream = _Bytes(
        ("response.output_text.delta", {"delta": "cut o"}),
        (final_event, {"response": final_response}),
    )
    return [c async for c in provider._parse_sse_stream(stream)]


@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["response.incomplete", "response.completed"])
async def test_responses_stream_max_output_tokens_is_length(event):
    chunks = await _responses_chunks(event, _CAPPED)
    assert last_stop_reason(chunks) == "length"
    # The terminal event must also still deliver the final chunk and its usage.
    assert chunks[-1].is_final is True
    assert chunks[-1].usage is not None


@pytest.mark.asyncio
async def test_responses_stream_normal_completion_is_not_length():
    chunks = await _responses_chunks("response.completed", _DONE)
    assert last_stop_reason(chunks) == "stop"


_MESSAGE = {
    "type": "message",
    "role": "assistant",
    "content": [{"type": "output_text", "text": "cut o"}],
}


@pytest.mark.parametrize("output", [[], [_MESSAGE]], ids=["empty", "partial-message"])
def test_responses_call_max_output_tokens_is_length(output):
    response = OpenAIResponsesTransformer.transform_response(
        {**_CAPPED, "output": output}, "gpt-5.4"
    )
    assert response.finish_reason == "length"


# --- Custom (OpenAI-compatible) ----------------------------------------------


class _Lines:
    """Stand-in for aiohttp's StreamReader: async-iterates byte lines."""

    def __init__(self, lines):
        self._lines = lines

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for line in self._lines:
            yield line


class _Response:
    status = 200

    def __init__(self, lines):
        self.content = _Lines(lines)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False


class _Session:
    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return False

    def post(self, *args, **kwargs):
        return _Response(self._lines)


async def _custom_chunks(monkeypatch, *payloads):
    lines = [b"data: " + json.dumps(p).encode() + b"\n" for p in payloads]
    lines.append(b"data: [DONE]\n")
    monkeypatch.setattr(
        "kollabor_ai.providers.custom_provider.aiohttp.ClientSession",
        lambda *a, **kw: _Session(lines),
    )
    provider = CustomProvider(
        CustomConfig(
            provider=ProviderType.CUSTOM,
            api_key="unused",
            model="test-model",
            base_url="http://localhost:1234/v1",
        )
    )
    return [
        c
        async for c in provider._stream_impl(
            messages=[{"role": "user", "content": "hi"}]
        )
    ]


_CONTENT = {"choices": [{"delta": {"content": "cut o"}}]}
_LENGTH = {"choices": [{"delta": {}, "finish_reason": "length"}]}
_USAGE = {
    "choices": [],
    "usage": {"prompt_tokens": 9, "completion_tokens": 8, "total_tokens": 17},
}


@pytest.mark.asyncio
async def test_custom_stream_length_survives_trailing_usage_chunk(monkeypatch):
    chunks = await _custom_chunks(monkeypatch, _CONTENT, _LENGTH, _USAGE)
    assert last_stop_reason(chunks) == "length"
    assert any(c.usage and c.usage.total_tokens == 17 for c in chunks)


@pytest.mark.asyncio
async def test_custom_stream_length_is_emitted_when_server_omits_usage(monkeypatch):
    chunks = await _custom_chunks(monkeypatch, _CONTENT, _LENGTH)
    assert last_stop_reason(chunks) == "length"


@pytest.mark.asyncio
async def test_custom_stream_normal_stop_is_not_length(monkeypatch):
    stop = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
    chunks = await _custom_chunks(monkeypatch, _CONTENT, stop, _USAGE)
    assert last_stop_reason(chunks) == "stop"
