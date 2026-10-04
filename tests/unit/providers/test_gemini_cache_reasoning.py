"""Gemini: tool round trip, thought signatures, stable prefix, cache/thinking usage.

Nothing here is live-verified (no Gemini credentials); every assertion is on the
exact request or usage shape the current docs describe:
https://ai.google.dev/gemini-api/docs/generate-content/thought-signatures
https://ai.google.dev/gemini-api/docs/generate-content/function-calling
https://ai.google.dev/api/generate-content (UsageMetadata)
"""

import json
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, Mock, patch

import pytest

from kollabor_ai.providers.gemini_provider import (
    SKIP_SIGNATURE_VALIDATION,
    GeminiProvider,
)
from kollabor_ai.providers.gemini_transformer import (
    GeminiResponseTransformer,
    GeminiStreamState,
)
from kollabor_ai.providers.models import (
    GeminiConfig,
    ProviderType,
    TextContent,
    TextDelta,
    ThinkingContent,
    ThinkingDelta,
    ToolCallDelta,
    ToolUseContent,
)

MODEL = "gemini-3.6-flash"
SIG = "SIG-CALL-1"


def make_provider(model=MODEL):
    return GeminiProvider(
        GeminiConfig(provider=ProviderType.GEMINI, api_key="example-key", model=model)
    )


def reasoning(model=MODEL, provider="gemini", **item):
    base = {"type": "functionCall", "id": "call_a", "api_id": False}
    return {"provider": provider, "model": model, "items": [{**base, **item}]}


def tool_call(call_id, name, args):
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(args)},
    }


def tool_history(**assistant_extra):
    """user -> assistant(two parallel calls) -> two tool results."""
    return [
        {"role": "system", "content": "be brief"},
        {"role": "user", "content": "weather in NYC and LA?"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                tool_call("call_a", "get_weather", {"city": "NYC"}),
                tool_call("call_b", "get_weather", {"city": "LA"}),
            ],
            **assistant_extra,
        },
        {"role": "tool", "tool_call_id": "call_a", "content": "72F"},
        {"role": "tool", "tool_call_id": "call_b", "content": "80F"},
    ]


def contents(messages, model=MODEL):
    return make_provider(model)._prepare_request(messages, tools=None)["contents"]


def stream(*chunks):
    state = GeminiStreamState()
    out = []
    for chunk in chunks:
        out.extend(
            GeminiResponseTransformer.transform_streaming_chunk(chunk, MODEL, state)
        )
    return out


def chunk(parts, finish=None, usage=None):
    body = {"candidates": [{"content": {"role": "model", "parts": parts}}]}
    if finish:
        body["candidates"][0]["finishReason"] = finish
    if usage:
        body["usageMetadata"] = usage
    return body


USAGE = {
    "promptTokenCount": 100,
    "cachedContentTokenCount": 40,
    "candidatesTokenCount": 20,
    "thoughtsTokenCount": 30,
    "totalTokenCount": 150,
}


# -- request shape: functionCall / functionResponse --------------------------


def test_tool_round_trip_request_shape():
    assert contents(tool_history(), model="gemini-2.0-flash") == [
        {"role": "user", "parts": [{"text": "weather in NYC and LA?"}]},
        {
            "role": "model",
            "parts": [
                {"functionCall": {"name": "get_weather", "args": {"city": "NYC"}}},
                {"functionCall": {"name": "get_weather", "args": {"city": "LA"}}},
            ],
        },
        {
            "role": "user",
            "parts": [
                {
                    "functionResponse": {
                        "name": "get_weather",
                        "response": {"result": "72F"},
                    }
                },
                {
                    "functionResponse": {
                        "name": "get_weather",
                        "response": {"result": "80F"},
                    }
                },
            ],
        },
    ]


def test_results_never_use_the_tool_role():
    roles = {c["role"] for c in contents(tool_history())}
    assert roles == {"user", "model"}


def test_text_before_calls_and_malformed_args():
    messages = tool_history()
    messages[2]["content"] = "checking"
    messages[2]["tool_calls"][0]["function"]["arguments"] = "{not json"
    model_turn = contents(messages)[1]["parts"]
    assert model_turn[0] == {"text": "checking"}
    assert model_turn[1]["functionCall"]["args"] == {}


def test_orphan_tool_result_becomes_text_not_a_bad_functionresponse():
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "tool", "tool_call_id": "gone", "content": "late result"},
    ]
    parts = contents(messages)[0]["parts"]
    assert parts == [{"text": "hi"}, {"text": "[tool result gone] late result"}]


def test_note_between_call_and_result_does_not_precede_the_result():
    messages = tool_history()
    messages.insert(3, {"role": "system", "content": "hub: peer joined"})
    parts = contents(messages)[2]["parts"]
    assert "functionResponse" in parts[0] and "functionResponse" in parts[1]
    assert parts[2] == {"text": "hub: peer joined"}


# -- thought signatures: re-emit rules ---------------------------------------


def test_signature_reemitted_on_matching_part_only():
    messages = tool_history(provider_reasoning=reasoning(thoughtSignature=SIG))
    calls = contents(messages)[1]["parts"]
    assert calls[0]["thoughtSignature"] == SIG
    assert "thoughtSignature" not in calls[1]  # parallel: first call only


@pytest.mark.parametrize(
    "stored",
    [
        reasoning(model="gemini-2.5-pro", thoughtSignature=SIG),
        reasoning(provider="openai_responses", thoughtSignature=SIG),
        None,
    ],
)
def test_signature_not_sent_on_mismatch_or_other_provider(stored):
    messages = tool_history(provider_reasoning=stored)
    payload = json.dumps(make_provider()._prepare_request(messages, tools=None))
    assert SIG not in payload
    # Gemini 3 still needs *a* signature: the documented stand-in, first call only.
    calls = contents(messages)[1]["parts"]
    assert calls[0]["thoughtSignature"] == SKIP_SIGNATURE_VALIDATION
    assert "thoughtSignature" not in calls[1]


def test_no_stand_in_for_models_that_do_not_validate():
    calls = contents(tool_history(), model="gemini-2.5-flash")[1]["parts"]
    assert all("thoughtSignature" not in part for part in calls)


def test_provider_issued_ids_are_echoed_synthetic_ones_are_not():
    api = reasoning(id="call_a", api_id=True, thoughtSignature=SIG)
    api["items"].append({"type": "functionCall", "id": "call_b", "api_id": False})
    turns = contents(tool_history(provider_reasoning=api))
    calls, results = turns[1]["parts"], turns[2]["parts"]
    assert calls[0]["functionCall"]["id"] == "call_a"
    assert "id" not in calls[1]["functionCall"]
    assert results[0]["functionResponse"]["id"] == "call_a"
    assert "id" not in results[1]["functionResponse"]


def test_text_signature_goes_on_the_last_text_part():
    messages = [
        {"role": "user", "content": "hi"},
        {
            "role": "assistant",
            "content": "hello",
            "provider_reasoning": {
                "provider": "gemini",
                "model": MODEL,
                "items": [{"type": "text", "thoughtSignature": "TEXT-SIG"}],
            },
        },
    ]
    assert contents(messages)[1]["parts"] == [
        {"text": "hello", "thoughtSignature": "TEXT-SIG"}
    ]


def test_provider_reasoning_never_reaches_the_wire():
    stored = reasoning(thoughtSignature=SIG)
    messages = tool_history(provider_reasoning=stored)
    payload = json.dumps(make_provider()._prepare_request(messages, tools=None))
    assert "provider_reasoning" not in payload
    assert "api_id" not in payload  # item internals
    assert payload.count(SIG) == 1  # only as the part's own thoughtSignature


# -- stable prefix -----------------------------------------------------------


def test_mid_conversation_system_note_keeps_the_prefix_stable():
    provider = make_provider()
    turn_one = [
        {"role": "system", "content": "stable rules"},
        {"role": "user", "content": "first"},
    ]
    turn_two = [
        *turn_one,
        {"role": "assistant", "content": "answer"},
        {"role": "system", "content": "hub roster: lapis online"},
        {"role": "user", "content": "second"},
    ]
    one = provider._prepare_request(turn_one, tools=None)
    two = provider._prepare_request(turn_two, tools=None)

    assert two["systemInstruction"] == one["systemInstruction"]
    assert two["contents"][: len(one["contents"])] == one["contents"]
    assert two["contents"][-1]["parts"] == [
        {"text": "hub roster: lapis online"},
        {"text": "second"},
    ]


# -- streaming: all parts, usage last, reasoning once ------------------------


def test_multi_part_chunk_yields_every_part_in_order_usage_on_last():
    responses = stream(
        chunk(
            [
                {"text": "let me check"},
                {
                    "functionCall": {"name": "a", "args": {"x": 1}},
                    "thoughtSignature": SIG,
                },
                {"functionCall": {"name": "b", "args": {}}},
            ],
            finish="STOP",
            usage=USAGE,
        )
    )
    assert [type(r.delta) for r in responses] == [
        TextDelta,
        ToolCallDelta,
        ToolCallDelta,
    ]
    assert responses[1].delta.tool_name == "a"
    assert json.loads(responses[1].delta.tool_arguments_delta) == {"x": 1}
    assert responses[1].delta.tool_call_id != responses[2].delta.tool_call_id
    assert [r.usage is not None for r in responses] == [False, False, True]
    assert [r.is_final for r in responses] == [False, False, True]
    assert [r.finish_reason for r in responses] == [None, None, "STOP"]


def test_provider_reasoning_once_on_the_final_response_and_ids_agree():
    responses = stream(
        chunk([{"text": "hi"}]),
        chunk([{"functionCall": {"name": "a", "args": {}}, "thoughtSignature": SIG}]),
        chunk([{"text": "", "thoughtSignature": "TEXT-SIG"}], finish="STOP"),
    )
    carriers = [r for r in responses if r.provider_reasoning]
    assert carriers == [responses[-1]] and responses[-1].is_final
    pr = responses[-1].provider_reasoning
    assert (pr["provider"], pr["model"]) == ("gemini", MODEL)
    call_id = responses[1].delta.tool_call_id
    assert pr["items"] == [
        {
            "type": "functionCall",
            "id": call_id,
            "api_id": False,
            "thoughtSignature": SIG,
        },
        {"type": "text", "thoughtSignature": "TEXT-SIG"},
    ]


def test_signature_only_closer_is_not_lost_or_shown():
    responses = stream(
        chunk(
            [{"text": "", "thoughtSignature": "TEXT-SIG"}], finish="STOP", usage=USAGE
        )
    )
    assert len(responses) == 1
    assert responses[0].delta == TextDelta(content="")
    assert responses[0].usage.cache_read_tokens == 40
    assert responses[0].provider_reasoning["items"] == [
        {"type": "text", "thoughtSignature": "TEXT-SIG"}
    ]


def test_no_signatures_no_calls_means_no_provider_reasoning():
    assert stream(chunk([{"text": "hi"}], finish="STOP"))[-1].provider_reasoning is None


def test_thought_summary_is_thinking_never_text():
    responses = stream(chunk([{"text": "pondering", "thought": True}, {"text": "ans"}]))
    assert [type(r.delta) for r in responses] == [ThinkingDelta, TextDelta]
    unified = GeminiResponseTransformer.transform_response(
        chunk([{"text": "pondering", "thought": True}, {"text": "ans"}], "STOP"), MODEL
    )
    assert [type(b) for b in unified.content] == [ThinkingContent, TextContent]
    assert unified.get_text_content() == "ans"


# -- usage: cached + thinking tokens -----------------------------------------


def test_usage_maps_cached_and_thinking_tokens_in_stream_and_call():
    streamed = stream(chunk([{"text": "x"}], finish="STOP", usage=USAGE))[-1].usage
    called = GeminiResponseTransformer.transform_response(
        chunk([{"text": "x"}], "STOP", USAGE), MODEL
    ).usage
    for usage in (streamed, called):
        assert usage.prompt_tokens == 100  # promptTokenCount already includes the cache
        assert usage.cache_read_tokens == 40
        assert usage.completion_tokens == 50  # candidates 20 + thoughts 30
        assert usage.total_tokens == 150


def test_usage_total_falls_back_to_the_sum_and_counts_tool_use_prompt():
    usage = stream(
        chunk(
            [{"text": "x"}],
            finish="STOP",
            usage={
                "promptTokenCount": 10,
                "toolUsePromptTokenCount": 5,
                "candidatesTokenCount": 3,
                "thoughtsTokenCount": 2,
            },
        )
    )[-1].usage
    assert (usage.prompt_tokens, usage.completion_tokens, usage.total_tokens) == (
        15,
        5,
        20,
    )
    assert usage.cache_read_tokens == 0


# -- non-stream capture, then the full round trip ----------------------------


def test_call_response_carries_provider_reasoning_and_round_trips():
    response = GeminiResponseTransformer.transform_response(
        chunk(
            [
                {"text": "checking"},
                {
                    "functionCall": {"id": "g3-id-1", "name": "a", "args": {"x": 1}},
                    "thoughtSignature": SIG,
                },
            ],
            "STOP",
            USAGE,
        ),
        MODEL,
    )
    tool = response.get_tool_uses()[0]
    assert isinstance(tool, ToolUseContent) and tool.id == "g3-id-1"
    assert response.provider_reasoning == {
        "provider": "gemini",
        "model": MODEL,
        "items": [
            {
                "type": "functionCall",
                "id": "g3-id-1",
                "api_id": True,
                "thoughtSignature": SIG,
            }
        ],
    }

    history = [
        {"role": "user", "content": "go"},
        {
            "role": "assistant",
            "content": "checking",
            "tool_calls": [tool_call(tool.id, tool.name, tool.input)],
            "provider_reasoning": response.provider_reasoning,
        },
        {"role": "tool", "tool_call_id": tool.id, "content": "done"},
    ]
    model_turn, result_turn = contents(history)[1:]
    assert model_turn["parts"] == [
        {"text": "checking"},
        {
            "functionCall": {"id": "g3-id-1", "name": "a", "args": {"x": 1}},
            "thoughtSignature": SIG,
        },
    ]
    assert result_turn["parts"] == [
        {
            "functionResponse": {
                "id": "g3-id-1",
                "name": "a",
                "response": {"result": "done"},
            }
        }
    ]


def test_streamed_call_round_trips_with_its_synthetic_id():
    responses = stream(
        chunk(
            [{"functionCall": {"name": "a", "args": {}}, "thoughtSignature": SIG}],
            finish="STOP",
        )
    )
    call_id = responses[-1].delta.tool_call_id
    history = [
        {"role": "user", "content": "go"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [tool_call(call_id, "a", {})],
            "provider_reasoning": responses[-1].provider_reasoning,
        },
        {"role": "tool", "tool_call_id": call_id, "content": "done"},
    ]
    model_turn, result_turn = contents(history)[1:]
    assert model_turn["parts"] == [
        {"functionCall": {"name": "a", "args": {}}, "thoughtSignature": SIG}
    ]
    assert result_turn["parts"][0]["functionResponse"] == {
        "name": "a",
        "response": {"result": "done"},
    }


@pytest.mark.asyncio
async def test_provider_stream_threads_one_state_through_every_chunk():
    lines = [
        "data: " + json.dumps(c)
        for c in (
            chunk([{"text": "hi"}]),
            chunk(
                [{"functionCall": {"name": "a", "args": {}}, "thoughtSignature": SIG}]
            ),
            chunk([], finish="STOP", usage=USAGE),
        )
    ]
    with patch("httpx.AsyncClient") as client_class:
        response = Mock(status_code=200, raise_for_status=Mock())

        async def aiter_lines():
            for line in lines:
                yield line

        response.aiter_lines = aiter_lines

        @asynccontextmanager
        async def open_stream(*args, **kwargs):
            yield response

        client_class.return_value = AsyncMock(stream=open_stream)
        provider = make_provider()
        await provider.initialize()
        responses = [
            r async for r in provider.stream([{"role": "user", "content": "x"}])
        ]

    carriers = [r for r in responses if r.provider_reasoning]
    assert carriers == [responses[-1]]
    assert carriers[0].provider_reasoning["items"][0]["thoughtSignature"] == SIG
