"""reasoning_content continuity and cache accounting for OpenAI-compatible chat.

Covers the openai, azure_openai and custom providers: cached-token field
aliases, capture of reasoning_content into provider_reasoning, replay on later
requests only where the model takes it back, and no local metadata on the wire.
"""

import asyncio

from kollabor_ai.providers import custom_provider
from kollabor_ai.providers.azure_provider import AzureOpenAIProvider
from kollabor_ai.providers.custom_provider import CustomConfig, CustomProvider
from kollabor_ai.providers.models import (
    AzureOpenAIConfig,
    OpenAIConfig,
    ProviderType,
)
from kollabor_ai.providers.openai_provider import OpenAIProvider
from kollabor_ai.providers.transformers import (
    OpenAIResponseTransformer,
    _openai_cache_tokens,
    reasoning_replay_mode,
)


def _stored(provider, model, text):
    return {
        "provider": provider.value,
        "model": model,
        "items": [{"type": "reasoning_content", "text": text}],
    }


def _history(provider, model):
    """user / assistant(tool call, with reasoning) / tool / assistant (answer)."""
    return [
        {"role": "user", "content": "first"},
        {
            "role": "assistant",
            "content": "older answer",
            "provider_reasoning": _stored(provider, model, "old thinking"),
        },
        {"role": "user", "content": "run it"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "c1",
                    "type": "function",
                    "function": {"name": "shell", "arguments": "{}"},
                }
            ],
            "provider_reasoning": _stored(provider, model, "loop thinking"),
        },
        {"role": "tool", "tool_call_id": "c1", "content": "ok"},
    ]


TOOLS = [{"name": "shell", "description": "run", "parameters": {"type": "object"}}]


def _custom(model):
    return CustomProvider(
        CustomConfig(
            provider=ProviderType.CUSTOM,
            api_key="k",
            model=model,
            base_url="http://localhost:1234/v1",
        )
    )


def _openai(model):
    return OpenAIProvider(
        OpenAIConfig(provider=ProviderType.OPENAI, api_key="sk-test", model=model)
    )


def _azure(model, deployment=None):
    return AzureOpenAIProvider(
        AzureOpenAIConfig(
            provider=ProviderType.AZURE_OPENAI,
            api_key="a" * 32,
            azure_endpoint="https://example.openai.azure.com",
            model=model,
            deployment_id=deployment,
        )
    )


# --- cached token field aliases -------------------------------------------


def test_cache_aliases_across_vendors():
    deepseek = {"prompt_tokens": 100, "prompt_cache_hit_tokens": 64}
    kimi = {"prompt_tokens": 100, "cached_tokens": 48}
    zai = {"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 32}}
    azure = {
        "prompt_tokens": 100,
        "prompt_tokens_details": {"cached_tokens": 16, "cache_write_tokens": 8},
    }
    assert _openai_cache_tokens(deepseek) == (64, 0)
    assert _openai_cache_tokens(kimi) == (48, 0)
    assert _openai_cache_tokens(zai) == (32, 0)
    assert _openai_cache_tokens(azure) == (16, 8)


def test_usage_only_with_deepseek_hit_fields_is_a_usage_chunk():
    chunk = {
        "choices": [],
        "usage": {"prompt_cache_hit_tokens": 5, "prompt_cache_miss_tokens": 7},
    }
    response = OpenAIResponseTransformer.transform_openai_chunk(chunk, "deepseek-v4-pro")
    assert response is not None and response.usage.cache_read_tokens == 5


# --- policy ----------------------------------------------------------------


def test_replay_policy_table():
    assert reasoning_replay_mode("deepseek-v4-pro") == "all"
    assert reasoning_replay_mode("DeepSeek/deepseek-v4-flash") == "all"
    assert reasoning_replay_mode("glm-5.2") == "loop"
    assert reasoning_replay_mode("zai-org/glm-4.7") == "loop"
    assert reasoning_replay_mode("kimi-k2.6") == "loop"
    for model in ("gpt-5.4", "gpt-6-luna", "grok-4.5", "qwen3.6-plus", "llama-4-scout"):
        assert reasoning_replay_mode(model) is None


# --- replay ----------------------------------------------------------------


def test_deepseek_requires_every_assistant_message_when_tools_present():
    provider = _custom("deepseek-v4-pro")
    payload = provider._build_payload(
        _history(ProviderType.CUSTOM, "deepseek-v4-pro"), TOOLS, stream=False
    )
    assistants = [m for m in payload["messages"] if m["role"] == "assistant"]
    assert [m["reasoning_content"] for m in assistants] == [
        "old thinking",
        "loop thinking",
    ]
    assert all("provider_reasoning" not in m for m in payload["messages"])


def test_deepseek_pads_gaps_and_other_models_reasoning():
    provider = _custom("deepseek-v4-pro")
    history = _history(ProviderType.CUSTOM, "glm-5.2")  # another model's thinking
    history[1].pop("provider_reasoning")
    payload = provider._build_payload(history, TOOLS, stream=False)
    assistants = [m for m in payload["messages"] if m["role"] == "assistant"]
    assert [m["reasoning_content"] for m in assistants] == ["", ""]


def test_deepseek_without_tools_sends_none():
    provider = _custom("deepseek-v4-pro")
    payload = provider._build_payload(
        _history(ProviderType.CUSTOM, "deepseek-v4-pro"), None, stream=False
    )
    assert not any("reasoning_content" in m for m in payload["messages"])


def test_glm_replays_only_the_live_tool_loop_and_matching_provider():
    provider = _custom("glm-5.2")
    payload = provider._build_payload(
        _history(ProviderType.CUSTOM, "glm-5.2"), TOOLS, stream=False
    )
    assistants = [m for m in payload["messages"] if m["role"] == "assistant"]
    assert "reasoning_content" not in assistants[0]  # before the last user turn
    assert assistants[1]["reasoning_content"] == "loop thinking"

    other = provider._build_payload(
        _history(ProviderType.OPENAI, "glm-5.2"), TOOLS, stream=False
    )
    assert not any("reasoning_content" in m for m in other["messages"])


def test_openai_provider_replays_for_a_compatible_endpoint_model():
    provider = _openai("kimi-k2.6")
    request = provider._prepare_request_params(
        _history(ProviderType.OPENAI, "kimi-k2.6"), TOOLS, stream=False
    )
    loop = [m for m in request["messages"] if m["role"] == "assistant"][-1]
    assert loop["reasoning_content"] == "loop thinking"


# --- nothing local reaches the wire -----------------------------------------


def test_provider_reasoning_never_reaches_the_wire():
    for model in ("gpt-5.4", "grok-4.5", "qwen3.6-plus"):
        for payload in (
            _openai(model)._prepare_request_params(
                _history(ProviderType.OPENAI, model), TOOLS, stream=True
            ),
            _azure(model)._prepare_request_params(
                _history(ProviderType.AZURE_OPENAI, model),
                TOOLS,
                stream=True,
                model="deployment-x",  # the kwargs path Azure.call/stream use
            ),
            _custom(model)._build_payload(
                _history(ProviderType.CUSTOM, model), TOOLS, stream=True
            ),
        ):
            for message in payload["messages"]:
                assert "provider_reasoning" not in message
                assert "reasoning_content" not in message
            assert "provider_reasoning" not in payload


def test_azure_streams_with_usage_and_targets_the_deployment():
    provider = _azure("gpt-5.4", deployment="prod-gpt")
    request = provider._prepare_request_params(
        [{"role": "user", "content": "hi"}], None, stream=True
    )
    assert request["stream_options"] == {"include_usage": True}
    assert request["model"] == "prod-gpt"
    assert "prompt_cache_key" not in request  # see decision in the report


# --- capture ---------------------------------------------------------------


class _Raw:
    def __init__(self, data):
        self._data = data

    def model_dump(self):
        return self._data


async def _chunks(items):
    for item in items:
        yield _Raw(item)


def _sse(delta, finish=None, usage=None):
    chunk = {"choices": [{"delta": delta, "finish_reason": finish}]}
    if usage:
        chunk["usage"] = usage
    return chunk


def _collect(model, provider):
    items = [
        _sse({"reasoning_content": "think "}),
        _sse({"reasoning_content": "hard"}),
        _sse({"content": "answer"}),
        _sse({}, finish="stop"),
        {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 1}},
    ]

    async def run():
        return [
            r
            async for r in OpenAIResponseTransformer.iter_chunks(
                _chunks(items), model, provider
            )
        ]

    return asyncio.run(run())


def test_stream_captures_reasoning_once_on_the_first_final_chunk():
    responses = _collect("deepseek-v4-pro", ProviderType.OPENAI)
    carriers = [r for r in responses if r.provider_reasoning]
    assert len(carriers) == 1 and carriers[0].is_final
    assert carriers[0].provider_reasoning == _stored(
        ProviderType.OPENAI, "deepseek-v4-pro", "think hard"
    )


def test_stream_ignores_reasoning_for_models_that_do_not_replay():
    assert not any(r.provider_reasoning for r in _collect("gpt-5.4", ProviderType.OPENAI))


def test_nonstream_captures_reasoning_content():
    response = OpenAIResponseTransformer.transform_openai_response(
        {
            "choices": [
                {
                    "message": {"content": "ok", "reasoning_content": "because"},
                    "finish_reason": "stop",
                }
            ]
        },
        "glm-5.2",
        ProviderType.CUSTOM,
    )
    assert response.provider_reasoning == _stored(
        ProviderType.CUSTOM, "glm-5.2", "because"
    )


# --- custom provider over SSE ------------------------------------------------


class _FakeContent:
    def __init__(self, lines):
        self._lines = lines

    def __aiter__(self):
        async def gen():
            for line in self._lines:
                yield line.encode()

        return gen()


class _FakeResponse:
    status = 200
    headers: dict = {}

    def __init__(self, lines):
        self.content = _FakeContent(lines)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


class _FakeSession:
    def __init__(self, lines):
        self.lines = lines
        self.payload = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    def post(self, _url, **kwargs):
        self.payload = kwargs["json"]
        return _FakeResponse(self.lines)


def test_custom_stream_captures_reasoning_and_deepseek_cache_hits():
    import json

    def line(obj):
        return "data: " + json.dumps(obj) + "\n"

    lines = [
        line({"choices": [{"delta": {"reasoning_content": "plan"}}]}),
        line({"choices": [{"delta": {"content": "hi"}}]}),
        line({"choices": [{"delta": {}, "finish_reason": "stop"}]}),
        line(
            {
                "choices": [],
                "usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 4,
                    "total_tokens": 104,
                    "prompt_cache_hit_tokens": 80,
                },
            }
        ),
        "data: [DONE]\n",
    ]
    session = _FakeSession(lines)
    provider = _custom("deepseek-v4-pro")
    original = custom_provider.aiohttp.ClientSession
    custom_provider.aiohttp.ClientSession = lambda: session
    try:

        async def run():
            return [
                r
                async for r in provider.stream(
                    _history(ProviderType.CUSTOM, "deepseek-v4-pro"), TOOLS
                )
            ]

        responses = asyncio.run(run())
    finally:
        custom_provider.aiohttp.ClientSession = original

    final = responses[-1]
    assert final.is_final and final.usage.cache_read_tokens == 80
    assert final.provider_reasoning == _stored(
        ProviderType.CUSTOM, "deepseek-v4-pro", "plan"
    )
    assert session.payload["stream_options"] == {"include_usage": True}
    sent = [m for m in session.payload["messages"] if m["role"] == "assistant"]
    assert [m["reasoning_content"] for m in sent] == ["old thinking", "loop thinking"]
