"""Provider stream fixes from the tool-loop handoff: tasks 13, 14, 15, 16, 29."""

import json
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from kollabor_ai.api_communication_service import APICommunicationService
from kollabor_ai.providers.errors import ProviderError
from kollabor_ai.providers.models import (
    OpenAIResponsesConfig,
    ProviderType,
    StreamingResponse,
    TextContent,
    TextDelta,
    ToolCallDelta,
    ToolUseContent,
)
from kollabor_ai.providers.openai_responses_provider import OpenAIResponsesProvider
from kollabor_ai.providers.transformers import (
    AnthropicResponseTransformer,
    OpenAIResponseTransformer,
    ToolCallAccumulator,
)


def _provider():
    return OpenAIResponsesProvider(
        OpenAIResponsesConfig(
            provider=ProviderType.OPENAI_RESPONSES, api_key="sk-test", model="gpt-5.4"
        )
    )


class _Raw:
    """Stand-in for an httpx streaming response: yields raw SSE bytes."""

    def __init__(self, *blocks):
        self._blocks = blocks

    async def aiter_bytes(self):
        for block in self._blocks:
            yield block


def _sse(event, payload):
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n".encode()


async def _drain(stream):
    return [c async for c in _provider()._parse_sse_stream(stream)]


def _stop(chunks):
    reasons = [c.finish_reason for c in chunks if c and c.finish_reason]
    return reasons[-1] if reasons else "stop"


# --- task 13: the Responses stream surfaces the server's real error ----------


@pytest.mark.asyncio
async def test_response_failed_raises_the_server_message():
    stream = _Raw(
        _sse("response.output_text.delta", {"delta": "par"}),
        _sse(
            "response.failed",
            {
                "response": {
                    "error": {"code": "server_error", "message": "model hit a snag"}
                }
            },
        ),
    )
    with pytest.raises(ProviderError) as exc:
        await _drain(stream)
    assert "model hit a snag" in str(exc.value)
    assert exc.value.error_code == "server_error"


@pytest.mark.asyncio
async def test_error_event_raises_the_server_message():
    stream = _Raw(
        _sse(
            "error",
            {"type": "error", "code": "rate_limit_exceeded", "message": "slow down"},
        )
    )
    with pytest.raises(ProviderError) as exc:
        await _drain(stream)
    assert str(exc.value) == "slow down"
    assert exc.value.error_code == "rate_limit_exceeded"


@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["response.completed", "response.failed", "error"])
async def test_malformed_terminal_event_raises(event):
    with pytest.raises(ProviderError) as exc:
        await _drain(_Raw(f"event: {event}\ndata: {{not json\n\n".encode()))
    assert exc.value.error_code == "malformed_stream_event"


@pytest.mark.asyncio
async def test_malformed_delta_is_still_skipped():
    stream = _Raw(
        b"event: response.output_text.delta\ndata: {nope\n\n",
        _sse("response.output_text.delta", {"delta": "ok"}),
    )
    assert [c.delta.content for c in await _drain(stream)] == ["ok"]


# --- task 29: a tool-call stop is reported as "tool_calls" -------------------


def _anthropic_stop(stop):
    wire = {
        "type": "message_delta",
        "delta": {"stop_reason": stop},
        "usage": {"output_tokens": 3},
    }
    return [AnthropicResponseTransformer.transform_anthropic_chunk(wire, "claude")]


def test_anthropic_stream_tool_use_is_tool_calls():
    assert _stop(_anthropic_stop("tool_use")) == "tool_calls"
    assert _stop(_anthropic_stop("end_turn")) != "tool_calls"


_CALL = {"type": "function_call", "call_id": "call_1", "name": "ls", "arguments": "{}"}
_USAGE = {"input_tokens": 1, "output_tokens": 1}


@pytest.mark.asyncio
async def test_responses_stream_function_call_is_tool_calls():
    final = {"status": "completed", "output": [_CALL], "usage": _USAGE}
    chunks = await _drain(_Raw(_sse("response.completed", {"response": final})))
    assert _stop(chunks) == "tool_calls"


@pytest.mark.asyncio
async def test_responses_stream_text_only_is_not_tool_calls():
    final = {"status": "completed", "output": [], "usage": _USAGE}
    chunks = await _drain(_Raw(_sse("response.completed", {"response": final})))
    assert _stop(chunks) != "tool_calls"


@pytest.mark.asyncio
async def test_responses_stream_token_cap_still_beats_tool_calls():
    final = {
        "status": "incomplete",
        "incomplete_details": {"reason": "max_output_tokens"},
        "output": [_CALL],
        "usage": _USAGE,
    }
    chunks = await _drain(_Raw(_sse("response.incomplete", {"response": final})))
    assert _stop(chunks) == "length"


# --- task 15: a no-parameter tool call is not dropped ------------------------


def test_accumulator_completes_named_tool_with_no_arguments():
    acc = ToolCallAccumulator()
    acc.add_delta("call_1", "ping", None)
    acc.add_delta(None, None, "")
    assert [(t.name, t.input) for t in acc.get_completed_tools()] == [("ping", {})]


def test_accumulator_still_drops_truncated_arguments():
    acc = ToolCallAccumulator()
    acc.add_delta("call_1", "ping", '{"a": ')
    assert acc.get_completed_tools() == []


def test_anthropic_no_parameter_tool_survives_the_stream():
    wire = [
        {
            "type": "content_block_start",
            "content_block": {
                "type": "tool_use",
                "id": "toolu_1",
                "name": "ping",
                "input": {},
            },
        },
        {
            "type": "content_block_delta",
            "delta": {"type": "input_json_delta", "partial_json": ""},
        },
    ]
    acc = ToolCallAccumulator()
    for chunk in wire:
        sr = AnthropicResponseTransformer.transform_anthropic_chunk(chunk, "claude")
        if sr and isinstance(sr.delta, ToolCallDelta):
            d = sr.delta
            acc.add_delta(d.tool_call_id, d.tool_name, d.tool_arguments_delta)
    assert [(t.id, t.name, t.input) for t in acc.get_completed_tools()] == [
        ("toolu_1", "ping", {})
    ]


# --- task 14: OpenAI chat streams use real tool ids and read every entry -----


class _Chunk:
    def __init__(self, payload):
        self._payload = payload

    def model_dump(self):
        return self._payload


async def _chat(*payloads):
    async def gen():
        for payload in payloads:
            yield _Chunk(payload)

    return [r async for r in OpenAIResponseTransformer.iter_chunks(gen(), "gpt")]


def _tc(index, id=None, name=None, args=""):
    return {"index": index, "id": id, "function": {"name": name, "arguments": args}}


def _chunk(*entries, finish=None, usage=None):
    payload = {
        "choices": [{"delta": {"tool_calls": list(entries)}, "finish_reason": finish}]
    }
    if usage:
        payload["usage"] = usage
    return payload


def _assemble(responses):
    acc = ToolCallAccumulator()
    for r in responses:
        if isinstance(r.delta, ToolCallDelta):
            acc.add_delta(
                r.delta.tool_call_id, r.delta.tool_name, r.delta.tool_arguments_delta
            )
    return [(t.id, t.name, t.input) for t in acc.get_completed_tools()]


@pytest.mark.asyncio
async def test_chat_stream_keeps_the_real_tool_ids():
    out = await _chat(
        _chunk(_tc(0, "call_a", "ls")),
        _chunk(_tc(0, None, None, '{"p": 1}')),
        _chunk(_tc(1, "call_b", "cat")),
        _chunk(_tc(1, None, None, '{"f": "x"}'), finish="tool_calls"),
    )
    assert _assemble(out) == [("call_a", "ls", {"p": 1}), ("call_b", "cat", {"f": "x"})]


@pytest.mark.asyncio
async def test_chat_stream_reads_every_entry_and_routes_interleaved_fragments():
    out = await _chat(
        _chunk(_tc(0, "call_a", "ls"), _tc(1, "call_b", "cat")),
        _chunk(_tc(0, None, None, '{"p": 1}')),
        _chunk(_tc(1, None, None, '{"f": "x"}')),
    )
    assert len(out) == 4
    assert _assemble(out) == [("call_a", "ls", {"p": 1}), ("call_b", "cat", {"f": "x"})]


@pytest.mark.asyncio
async def test_chat_stream_backend_without_ids_gets_distinct_synthetic_ids():
    tools = _assemble(
        await _chat(
            _chunk(_tc(0, None, "ls", '{"p": 1}')),
            _chunk(_tc(1, None, "cat", '{"f": "x"}')),
        )
    )
    assert [t[1:] for t in tools] == [("ls", {"p": 1}), ("cat", {"f": "x"})]
    assert tools[0][0] != tools[1][0]
    assert all(t[0].startswith("call_") for t in tools)


@pytest.mark.asyncio
async def test_multi_call_chunk_puts_finish_reason_and_usage_on_the_last_piece():
    usage = {"prompt_tokens": 5, "completion_tokens": 2, "total_tokens": 7}
    out = await _chat(
        _chunk(
            _tc(0, "call_a", "ls", "{}"),
            _tc(1, "call_b", "cat", "{}"),
            finish="tool_calls",
            usage=usage,
        )
    )
    assert [r.finish_reason for r in out] == [None, "tool_calls"]
    assert [r.usage is not None for r in out] == [False, True]


# --- task 16: every system message reaches the Responses instructions --------


def test_responses_instructions_keep_every_system_message():
    params = _provider()._prepare_request(
        [
            {"role": "system", "content": "first rules"},
            {"role": "system", "content": "second rules"},
            {"role": "user", "content": "hi"},
        ],
        None,
        stream=True,
    )
    assert "first rules" in params["instructions"]
    assert "second rules" in params["instructions"]


# --- task 29 on the real ChatGPT/Codex wire: the tool call arrives only as
# --- output_item.done and response.completed carries an EMPTY output ---------

_DONE_CALL = {
    "id": "fc_1",
    "type": "function_call",
    "call_id": "call_1",
    "name": "ls",
    "arguments": '{"path": "."}',
}


def _codex_tool_turn(event="response.completed", final=None):
    final = final or {"status": "completed", "output": [], "usage": _USAGE}
    return _Raw(
        _sse("response.output_text.delta", {"delta": "let me look"}),
        _sse("response.output_item.added", {"item": {**_DONE_CALL, "arguments": ""}}),
        _sse("response.output_item.done", {"item": _DONE_CALL}),
        _sse(event, {"response": final}),
    )


@pytest.mark.asyncio
async def test_codex_stream_tool_call_with_empty_final_output_is_tool_calls():
    assert _stop(await _drain(_codex_tool_turn())) == "tool_calls"


@pytest.mark.asyncio
async def test_codex_stream_token_cap_still_beats_tool_calls():
    final = {
        "status": "incomplete",
        "incomplete_details": {"reason": "max_output_tokens"},
        "output": [],
        "usage": _USAGE,
    }
    chunks = await _drain(_codex_tool_turn("response.incomplete", final))
    assert _stop(chunks) == "length"


@pytest.mark.asyncio
async def test_codex_stream_text_only_turn_is_not_tool_calls():
    final = {"status": "completed", "output": [], "usage": _USAGE}
    stream = _Raw(
        _sse("response.output_text.delta", {"delta": "hi"}),
        _sse("response.output_item.done", {"item": {"type": "message", "content": []}}),
        _sse("response.completed", {"response": final}),
    )
    assert _stop(await _drain(stream)) != "tool_calls"


class _OkResponse(_Raw):
    status_code = 200


class _Client:
    """Stand-in for httpx.AsyncClient: .stream() is an async context manager."""

    def __init__(self, response):
        self._response = response

    @asynccontextmanager
    async def stream(self, *args, **kwargs):
        yield self._response


async def _call_via_stream(raw):
    provider = _provider()
    provider._initialized = True
    provider._client = _Client(_OkResponse(*raw._blocks))
    messages = [{"role": "user", "content": "list files"}]
    return await provider._call_via_stream(messages, None)


@pytest.mark.asyncio
async def test_codex_call_via_stream_keeps_tool_calls_when_final_output_is_empty():
    unified = await _call_via_stream(_codex_tool_turn())

    calls = [c for c in unified.content if isinstance(c, ToolUseContent)]
    assert [(c.id, c.name, c.input) for c in calls] == [("call_1", "ls", {"path": "."})]
    assert [c.text for c in unified.content if isinstance(c, TextContent)] == [
        "let me look"
    ]
    assert unified.finish_reason == "tool_calls"


@pytest.mark.asyncio
async def test_call_via_stream_does_not_duplicate_a_call_the_final_output_has():
    final = {"status": "completed", "output": [_DONE_CALL], "usage": _USAGE}
    unified = await _call_via_stream(_codex_tool_turn(final=final))

    ids = [c.id for c in unified.content if isinstance(c, ToolUseContent)]
    assert ids == ["call_1"]


# --- task 15, explicit accumulation mode --------------------------------------


class _ToolStreamProvider:
    provider_name = "anthropic"

    def __init__(self, *deltas):
        self._deltas = deltas

    async def stream(self, messages, tools=None, **kwargs):
        for delta in self._deltas:
            yield StreamingResponse(
                delta=delta, raw_chunk={"type": "content_block_delta"}
            )
        yield StreamingResponse(
            delta=TextDelta(content=""),
            is_final=True,
            finish_reason="tool_calls",
            raw_chunk={"type": "message_stop"},
        )


def _explicit_service(provider, tmp_path):
    config = MagicMock()
    config.get = lambda key, default=None: {
        "kollabor.llm.enable_streaming": True,
        "kollabor.llm.use_explicit_tool_accumulation": True,
        "kollabor.llm.debug_tool_stream_path": False,
    }.get(key, default)
    profile = MagicMock()
    profile.provider = "anthropic"
    profile.name = "explicit-test"
    profile.get_model.return_value = "claude-test"
    profile.get_temperature.return_value = 0.7
    profile.get_max_tokens.return_value = 4096
    profile.get_timeout.return_value = 30
    profile.to_dict.return_value = {
        "provider": "anthropic",
        "model": "claude-test",
        "api_key": "sk-test",
        "temperature": 0.7,
        "max_tokens": 4096,
        "timeout": 30,
    }
    service = APICommunicationService(config, Path(tmp_path), profile)
    service._provider = provider
    service._initialized = True
    return service


@pytest.mark.asyncio
async def test_explicit_mode_keeps_a_tool_call_with_no_arguments(tmp_path):
    provider = _ToolStreamProvider(
        ToolCallDelta(tool_call_id="call_1", tool_name="ls", tool_arguments_delta="")
    )
    service = _explicit_service(provider, tmp_path)

    await service._call_provider_stream([{"role": "user", "content": "list"}])

    tools = service.last_tool_calls
    assert [(t.id, t.name, t.input) for t in tools] == [("call_1", "ls", {})]


@pytest.mark.asyncio
async def test_explicit_mode_does_not_duplicate_calls_completed_mid_stream(tmp_path):
    provider = _ToolStreamProvider(
        ToolCallDelta(
            tool_call_id="call_1", tool_name="search", tool_arguments_delta='{"q": "x"}'
        ),
        ToolCallDelta(tool_call_id="call_2", tool_name="ls", tool_arguments_delta=""),
    )
    service = _explicit_service(provider, tmp_path)

    await service._call_provider_stream([{"role": "user", "content": "go"}])

    tools = service.last_tool_calls
    assert [(t.id, t.input) for t in tools] == [("call_1", {"q": "x"}), ("call_2", {})]
