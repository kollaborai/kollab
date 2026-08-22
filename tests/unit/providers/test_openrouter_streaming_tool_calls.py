"""Regression test for OpenRouter streaming tool-call argument loss.

Root cause (fixed): OpenRouterProvider.stream() ran its own EXPLICIT
ToolCallAccumulator and yielded only the *completed* tool with
tool_arguments_delta=None. The APICommunicationService layer then re-accumulated
those name-only deltas, could never rebuild the arguments, and dropped the call
entirely -- surfacing as ``INCONSISTENT_TOOL_STOP: stop_reason=tool_calls but no
tool calls`` and a dead agent turn. Every streamed tool call was silently lost.

The fix makes the provider pass raw incremental deltas straight through so the
service's accumulator (the authoritative one) receives the argument fragments.

This test feeds the exact z-ai/glm-5.2-over-OpenRouter chunk shape captured from
a real failing turn and asserts the arguments survive reassembly.
"""

import pytest

from kollabor_ai.providers.models import OpenRouterConfig, ToolCallDelta
from kollabor_ai.providers.openrouter_provider import OpenRouterProvider
from kollabor_ai.providers.transformers import ToolCallAccumulator


class _FakeChunk:
    def __init__(self, payload):
        self._payload = payload

    def model_dump(self):
        return self._payload


# Exact delta shape OpenRouter emits for GLM tool calls: the id + name arrive
# in the first chunk with empty arguments, the JSON arrives in a follow-up chunk
# with id/name=None, and a final content="" chunk carries finish_reason.
_GLM_TOOL_CHUNKS = [
    {"choices": [{"delta": {"role": "assistant", "content": None, "tool_calls": [
        {"index": 0, "id": "call_abc", "type": "function",
         "function": {"name": "get_weather", "arguments": ""}}]},
        "finish_reason": None}]},
    {"choices": [{"delta": {"content": None, "tool_calls": [
        {"index": 0, "id": None, "type": None,
         "function": {"name": None, "arguments": '{"city":"Paris"}'}}]},
        "finish_reason": None}]},
    {"choices": [{"delta": {"content": "", "tool_calls": None},
                  "finish_reason": "tool_calls"}]},
]

_OPENROUTER_TRAILING_USAGE_CHUNKS = [
    {"choices": [{"delta": {"content": "ok"}, "finish_reason": None}]},
    {
        "choices": [{"delta": {"content": ""}, "finish_reason": "stop"}],
        "usage": None,
    },
    {
        "choices": [],
        "usage": {
            "prompt_tokens": 37,
            "completion_tokens": 2,
            "total_tokens": 39,
        },
    },
]


class _FakeCompletions:
    def __init__(self, chunks):
        self._chunks = chunks

    async def create(self, **_kwargs):
        async def _gen():
            for payload in self._chunks:
                yield _FakeChunk(payload)
        return _gen()


class _FakeChat:
    def __init__(self, chunks):
        self.completions = _FakeCompletions(chunks)


class _FakeClient:
    def __init__(self, chunks):
        self.chat = _FakeChat(chunks)

    async def close(self):
        pass


class _FakeModelInfo:
    async def get_model_limits(self, _model):
        return {}

    def compute_effective_max_tokens(self, requested, _model, _input_tokens):
        return requested or 1024


def _make_provider(chunks=_GLM_TOOL_CHUNKS):
    provider = OpenRouterProvider(
        OpenRouterConfig(
            api_key="sk-or-test",
            model="z-ai/glm-5.2",
            base_url="https://openrouter.ai/api/v1",
            max_tokens=800,
        )
    )
    provider._client = _FakeClient(chunks)
    provider._model_info = _FakeModelInfo()
    provider._initialized = True
    return provider


@pytest.mark.asyncio
async def test_streamed_tool_call_arguments_reach_service_accumulator():
    provider = _make_provider()

    # The service layer runs a LEGACY accumulator (use_explicit=False default)
    # and calls get_completed_tools() once the stream ends.
    service_accumulator = ToolCallAccumulator(legacy_mode=True)
    saw_arguments = False

    async for sr in provider.stream(
        [{"role": "user", "content": "weather in Paris?"}],
        tools=[{"name": "get_weather", "description": "x",
                "parameters": {"type": "object",
                               "properties": {"city": {"type": "string"}}}}],
    ):
        if isinstance(sr.delta, ToolCallDelta):
            if sr.delta.tool_arguments_delta:
                saw_arguments = True
            service_accumulator.add_delta(
                tool_call_id=sr.delta.tool_call_id,
                name=sr.delta.tool_name,
                arguments_delta=sr.delta.tool_arguments_delta,
            )

    # Regression guard: the argument fragments must be passed through, not
    # swallowed into a name-only completed-tool delta.
    assert saw_arguments, "provider dropped tool-call argument fragments"

    completed = service_accumulator.get_completed_tools()
    assert [(t.name, t.input) for t in completed] == [
        ("get_weather", {"city": "Paris"})
    ]


@pytest.mark.asyncio
async def test_stream_consumes_usage_after_finish_reason_chunk():
    """OpenRouter's usage-only chunk can arrive after finish_reason."""
    provider = _make_provider(_OPENROUTER_TRAILING_USAGE_CHUNKS)

    responses = []
    async for response in provider.stream(
        [{"role": "user", "content": "report status"}]
    ):
        responses.append(response)

    assert responses[-1].usage is not None
    assert responses[-1].usage.prompt_tokens == 37
    assert responses[-1].usage.completion_tokens == 2
    assert responses[-1].usage.total_tokens == 39
