"""OpenRouter: explicit cache breakpoints, reasoning_details continuity, usage.

Docs relied on (not live-verified, no OpenRouter credentials here):
  * prompt-caching: Anthropic needs ``cache_control`` on text parts, at most four
    breakpoints; OpenAI/DeepSeek/Grok/Moonshot/Groq/Gemini 2.5+ cache automatically.
  * reasoning-tokens: ``reasoning_details`` come back on ``message`` / ``delta`` and
    must be sent back unmodified, in order, on the assistant message.
  * usage-accounting: ``prompt_tokens_details.cached_tokens`` / ``cache_write_tokens``.

The fake client uses real OpenAI SDK models, so unknown fields (``reasoning_details``,
``cache_write_tokens``) go through the same ``model_dump()`` the live SDK does.
"""

import copy
import json

import pytest
from openai.types.chat import ChatCompletion, ChatCompletionChunk

from kollabor_ai.providers.models import OpenRouterConfig, ProviderType
from kollabor_ai.providers.openrouter_provider import OpenRouterProvider

CLAUDE = "anthropic/claude-sonnet-4.5"
EPHEMERAL = {"type": "ephemeral"}


class _Completions:
    """Mirrors the SDK's strict signature: unknown kwargs raise TypeError."""

    def __init__(self, result):
        self.result = result
        self.kwargs = None

    async def create(
        self,
        *,
        model,
        messages,
        max_tokens=None,
        stream=None,
        stream_options=None,
        tools=None,
        temperature=None,
        top_p=None,
        reasoning_effort=None,
        extra_body=None,
    ):
        self.kwargs = {
            "model": model,
            "messages": messages,
            "stream": stream,
            "extra_body": extra_body,
        }
        if not stream:
            return self.result

        async def _gen():
            for chunk in self.result:
                yield chunk

        return _gen()


class _Client:
    def __init__(self, result):
        self.chat = type("Chat", (), {})()
        self.chat.completions = _Completions(result)

    async def close(self):
        pass


class _ModelInfo:
    async def get_model_limits(self, _model):
        return {}

    def compute_effective_max_tokens(self, requested, _model, _input_tokens):
        return requested or 1024


def _provider(result, model=CLAUDE):
    provider = OpenRouterProvider(
        OpenRouterConfig(api_key="sk-or-test", model=model, max_tokens=800)
    )
    provider._client = _Client(result)
    provider._model_info = _ModelInfo()
    provider._initialized = True
    return provider


def _completion(message=None, usage=None):
    return ChatCompletion.model_validate(
        {
            "id": "gen-1",
            "object": "chat.completion",
            "created": 1,
            "model": CLAUDE,
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": "ok",
                        **(message or {}),
                    },
                }
            ],
            "usage": usage
            or {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
        }
    )


def _chunk(delta=None, finish_reason=None, usage=None, choices=True):
    return ChatCompletionChunk.model_validate(
        {
            "id": "gen-1",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": CLAUDE,
            "choices": (
                [{"index": 0, "delta": delta or {}, "finish_reason": finish_reason}]
                if choices
                else []
            ),
            **({"usage": usage} if usage else {}),
        }
    )


def _wire(provider):
    return provider.last_request_payload["messages"]


def _count_breakpoints(payload):
    return json.dumps(payload).count('"cache_control"')


async def _drain(provider, messages):
    return [r async for r in provider.stream(messages)]


# --- explicit cache breakpoints ------------------------------------------------


@pytest.mark.asyncio
async def test_anthropic_gets_system_and_rolling_breakpoints():
    provider = _provider(_completion())
    await provider.call(
        [
            {"role": "system", "content": "You are Kollab."},
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": [{"type": "text", "text": "second"}]},
        ]
    )
    sys_msg, first, answer, last = _wire(provider)
    # string content converted to a parts array to carry the breakpoint
    assert sys_msg["content"] == [
        {"type": "text", "text": "You are Kollab.", "cache_control": EPHEMERAL}
    ]
    assert last["content"] == [
        {"type": "text", "text": "second", "cache_control": EPHEMERAL}
    ]
    assert answer["content"][0]["cache_control"] == EPHEMERAL
    assert first["content"] == "first"  # older turn untouched
    assert _count_breakpoints(provider.last_request_payload) == 3


@pytest.mark.asyncio
async def test_tool_tail_is_tagged_and_empty_parts_are_skipped():
    provider = _provider(_completion())
    await provider.call(
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "run it"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "t", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "result text"},
            {"role": "user", "content": [{"type": "text", "text": "  "}]},
        ]
    )
    sys_msg, user, assistant, tool, blank = _wire(provider)
    assert tool["content"][0]["cache_control"] == EPHEMERAL
    assert user["content"][0]["cache_control"] == EPHEMERAL  # next-newest taggable
    assert assistant["content"] is None  # nothing to tag, left alone
    assert blank["content"] == [{"type": "text", "text": "  "}]  # blank never tagged
    assert _count_breakpoints(provider.last_request_payload) == 3


@pytest.mark.asyncio
async def test_breakpoints_stay_within_anthropic_limit_on_long_history():
    provider = _provider(_completion())
    history = [{"role": "system", "content": "sys"}]
    for i in range(30):
        history.append({"role": "user", "content": f"q{i}"})
        history.append({"role": "assistant", "content": f"a{i}"})
    await provider.call(history)
    assert _count_breakpoints(provider.last_request_payload) <= 4


@pytest.mark.asyncio
async def test_mid_conversation_system_message_gets_no_rolling_tag():
    provider = _provider(_completion())
    await provider.call(
        [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "q"},
            {"role": "system", "content": "mid-conversation note"},
        ]
    )
    _, user, note = _wire(provider)
    assert note["content"] == "mid-conversation note"
    assert user["content"][0]["cache_control"] == EPHEMERAL


@pytest.mark.asyncio
async def test_automatic_cache_routes_get_no_cache_control():
    for model in ("openai/gpt-5", "deepseek/deepseek-v3.2", "google/gemini-2.5-pro"):
        provider = _provider(_completion(), model=model)
        await provider.call(
            [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
        )
        assert _count_breakpoints(provider.last_request_payload) == 0
        assert _wire(provider)[1]["content"] == "hi"


@pytest.mark.asyncio
async def test_callers_messages_are_never_mutated():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": [{"type": "text", "text": "hello"}]},
        {
            "role": "assistant",
            "content": "hi",
            "provider_reasoning": {
                "provider": "openrouter",
                "model": CLAUDE,
                "items": [{"type": "reasoning.text", "text": "t", "format": "x"}],
            },
        },
        {"role": "user", "content": "again", "agent_hud": True},
    ]
    before = copy.deepcopy(messages)
    provider = _provider(_completion())
    await provider.call(messages)
    assert messages == before
    await _drain(_provider([_chunk({"content": "x"}, "stop")]), messages)
    assert messages == before


# --- reasoning_details round trip -----------------------------------------------


_FRAGMENTS = [
    {
        "type": "reasoning.text",
        "text": "Let me",
        "signature": None,
        "format": "anthropic-claude-v1",
        "index": 0,
    },
    {
        "type": "reasoning.text",
        "text": " think",
        "signature": None,
        "format": "anthropic-claude-v1",
        "index": 0,
    },
    {
        "type": "reasoning.text",
        "text": "",
        "signature": "sig-abc",
        "format": "anthropic-claude-v1",
        "index": 0,
    },
]


@pytest.mark.asyncio
async def test_stream_collects_fragmented_reasoning_once_on_first_final_chunk():
    usage = {
        "prompt_tokens": 20,
        "completion_tokens": 5,
        "total_tokens": 25,
        "prompt_tokens_details": {"cached_tokens": 7, "cache_write_tokens": 11},
    }
    chunks = [
        _chunk(
            {"role": "assistant", "content": "", "reasoning_details": [_FRAGMENTS[0]]}
        ),
        _chunk({"content": "", "reasoning_details": [_FRAGMENTS[1]]}),
        _chunk({"content": "", "reasoning_details": [_FRAGMENTS[2]]}),
        _chunk({"content": "Answer"}),
        _chunk({"content": ""}, finish_reason="stop"),
        _chunk(usage=usage, choices=False),
    ]
    provider = _provider(chunks)
    responses = await _drain(provider, [{"role": "user", "content": "hi"}])

    carrying = [r for r in responses if r.provider_reasoning is not None]
    assert len(carrying) == 1
    assert (
        carrying[0].is_final and carrying[0].usage is None
    )  # first final, not the usage chunk
    assert carrying[0].provider_reasoning == {
        "provider": "openrouter",
        "model": CLAUDE,
        "items": _FRAGMENTS,  # verbatim, in arrival order
    }
    # cache accounting lands on the trailing usage chunk
    assert responses[-1].usage.cache_read_tokens == 7
    assert responses[-1].usage.cache_creation_tokens == 11
    assert responses[-1].usage.prompt_tokens == 20


@pytest.mark.asyncio
async def test_tool_call_turn_carries_reasoning_and_passes_tool_deltas_through():
    detail = {
        "type": "reasoning.encrypted",
        "data": "blob",
        "id": "rs_1",
        "format": "openai-responses-v1",
        "index": 0,
    }
    call = {"index": 0, "id": "call_1", "type": "function"}
    chunks = [
        _chunk({"role": "assistant", "content": None, "reasoning_details": [detail]}),
        _chunk({"tool_calls": [{**call, "function": {"name": "t", "arguments": ""}}]}),
        _chunk({"tool_calls": [{"index": 0, "function": {"arguments": '{"a":1}'}}]}),
        _chunk({"content": ""}, finish_reason="tool_calls"),
    ]
    provider = _provider(chunks, model="openai/gpt-5")
    responses = await _drain(provider, [{"role": "user", "content": "go"}])

    args = "".join(
        r.delta.tool_arguments_delta or ""
        for r in responses
        if hasattr(r.delta, "tool_arguments_delta")
    )
    assert args == '{"a":1}'
    carrying = [r for r in responses if r.provider_reasoning is not None]
    assert len(carrying) == 1 and carrying[0].finish_reason == "tool_calls"
    assert carrying[0].provider_reasoning["items"] == [detail]


@pytest.mark.asyncio
async def test_stream_without_reasoning_has_no_provider_reasoning():
    provider = _provider([_chunk({"content": "hi"}), _chunk({"content": ""}, "stop")])
    responses = await _drain(provider, [{"role": "user", "content": "hi"}])
    assert all(r.provider_reasoning is None for r in responses)


@pytest.mark.asyncio
async def test_nonstream_captures_reasoning_details_and_cache_usage():
    details = [
        {
            "type": "reasoning.encrypted",
            "data": "blob",
            "id": "rs_1",
            "format": "openai-responses-v1",
            "index": 0,
        },
        {
            "type": "reasoning.summary",
            "summary": "s",
            "id": "rs_1",
            "format": "openai-responses-v1",
            "index": 1,
        },
    ]
    provider = _provider(
        _completion(
            {"reasoning": "s", "reasoning_details": details},
            {
                "prompt_tokens": 30,
                "completion_tokens": 3,
                "total_tokens": 33,
                "prompt_tokens_details": {"cached_tokens": 12, "cache_write_tokens": 9},
            },
        ),
        model="openai/gpt-5",
    )
    response = await provider.call([{"role": "user", "content": "hi"}])

    assert response.provider == ProviderType.OPENROUTER
    assert response.provider_reasoning == {
        "provider": "openrouter",
        "model": "openai/gpt-5",
        "items": details,
    }
    assert response.usage.cache_read_tokens == 12
    assert response.usage.cache_creation_tokens == 9


@pytest.mark.asyncio
async def test_nonstream_without_reasoning_details_is_none():
    response = await _provider(_completion()).call([{"role": "user", "content": "hi"}])
    assert response.provider_reasoning is None


def _assistant(provider_name, model, items, role="assistant"):
    return {
        "role": role,
        "content": "done",
        "provider_reasoning": {
            "provider": provider_name,
            "model": model,
            "items": items,
        },
    }


@pytest.mark.asyncio
async def test_reasoning_details_resent_only_on_matching_provider_and_model():
    items = [
        {
            "type": "reasoning.text",
            "text": "t",
            "signature": "s",
            "format": "anthropic-claude-v1",
            "index": 0,
        }
    ]
    provider = _provider(_completion())
    await provider.call(
        [
            {"role": "user", "content": "q"},
            _assistant("openrouter", CLAUDE, items),
            _assistant("openrouter", "openai/gpt-5", items),
            _assistant("anthropic", CLAUDE, items),
            _assistant("openrouter", CLAUDE, items, role="user"),
            _assistant("openrouter", CLAUDE, []),
        ]
    )
    _, match, other_model, other_provider, not_assistant, empty = _wire(provider)
    assert match["reasoning_details"] == items
    for message in (other_model, other_provider, not_assistant, empty):
        assert "reasoning_details" not in message


@pytest.mark.asyncio
async def test_provider_reasoning_key_never_reaches_the_wire():
    items = [{"type": "reasoning.text", "text": "secret-thought", "format": "x"}]
    messages = [
        {"role": "user", "content": "q"},
        _assistant("openrouter", CLAUDE, items),
        _assistant("openrouter", "other/model", items),
    ]
    for model in (CLAUDE, "openai/gpt-5"):
        provider = _provider(_completion(), model=model)
        await provider.call(messages)
        sent = provider._client.chat.completions.kwargs
        for payload in (provider.last_request_payload, sent):
            assert "provider_reasoning" not in json.dumps(payload)
        # a non-matching model must not leak the thought text either
        if model != CLAUDE:
            assert "secret-thought" not in json.dumps(provider.last_request_payload)

    provider = _provider([_chunk({"content": "x"}, "stop")])
    await _drain(provider, messages)
    assert "provider_reasoning" not in json.dumps(provider.last_request_payload)


# --- OpenRouter body fields ---------------------------------------------------------


@pytest.mark.asyncio
async def test_routing_kwargs_travel_in_extra_body():
    provider = _provider(_completion())
    await provider.call(
        [{"role": "user", "content": "hi"}],
        models=["a/b", "c/d"],
        route="fallback",
        provider={"order": ["Anthropic"]},
    )
    # the strict fake raises TypeError if any of these were top-level kwargs
    assert provider._client.chat.completions.kwargs["extra_body"] == {
        "models": ["a/b", "c/d"],
        "route": "fallback",
        "provider": {"order": ["Anthropic"]},
    }
