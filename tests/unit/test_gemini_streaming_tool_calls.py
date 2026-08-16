"""Gemini streaming tool calls must survive the accumulator.

Two defects made every streamed Gemini tool call vanish silently:

1. ``tool_call_id=None`` -- Gemini sends each functionCall complete in one
   part, so no tool is ever "open" for an id-less delta to attach to, and
   ``ToolCallAccumulator.add_delta`` dropped it with a warning.
2. ``str(args)`` -- the accumulator ``json.loads()`` the buffer, and a Python
   dict repr uses single quotes, so the tool never parsed as complete.

Observed live: a `hello` turn logged "Received tool delta with no id and no
current tool open, dropping" and returned nothing.
"""

import json

from kollabor_ai.providers.gemini_transformer import GeminiResponseTransformer
from kollabor_ai.providers.models import ToolCallDelta
from kollabor_ai.providers.transformers import ToolCallAccumulator


def _chunk(name, args, finish=None):
    return {
        "candidates": [
            {
                "content": {
                    "role": "model",
                    "parts": [{"functionCall": {"name": name, "args": args}}],
                },
                "finishReason": finish,
            }
        ]
    }


def _delta(name, args):
    response = GeminiResponseTransformer.transform_streaming_chunk(
        _chunk(name, args), "gemini-3.6-flash"
    )
    assert response is not None
    assert isinstance(response.delta, ToolCallDelta)
    return response.delta


# -- the two defects --------------------------------------------------------


def test_streamed_tool_call_carries_an_id():
    assert _delta("read_file", {"path": "/tmp/x"}).tool_call_id


def test_streamed_arguments_are_json_not_a_python_repr():
    delta = _delta("read_file", {"path": "/tmp/x"})
    assert json.loads(delta.tool_arguments_delta) == {"path": "/tmp/x"}
    assert "'" not in delta.tool_arguments_delta


def test_ids_are_unique_across_chunks():
    """A per-chunk index would collide and concatenate two calls into one."""
    first = _delta("read_file", {"path": "/a"})
    second = _delta("read_file", {"path": "/b"})
    assert first.tool_call_id != second.tool_call_id


# -- the round trip that actually failed live -------------------------------


def test_accumulator_completes_a_streamed_gemini_tool_call():
    accumulator = ToolCallAccumulator(legacy_mode=True)
    delta = _delta("get_weather", {"location": "NYC", "unit": "c"})
    accumulator.add_delta(
        delta.tool_call_id, delta.tool_name, delta.tool_arguments_delta
    )

    completed = accumulator.get_completed_tools()
    assert len(completed) == 1
    assert completed[0].name == "get_weather"
    assert completed[0].input == {"location": "NYC", "unit": "c"}


def test_two_distinct_calls_stay_distinct_through_the_accumulator():
    accumulator = ToolCallAccumulator(legacy_mode=True)
    for path in ("/a", "/b"):
        delta = _delta("read_file", {"path": path})
        accumulator.add_delta(
            delta.tool_call_id, delta.tool_name, delta.tool_arguments_delta
        )

    completed = accumulator.get_completed_tools()
    assert len(completed) == 2
    assert sorted(t.input["path"] for t in completed) == ["/a", "/b"]


def test_empty_args_still_complete():
    """A no-argument tool must not stall on the accumulator's empty check."""
    delta = _delta("get_time", {})
    assert delta.tool_arguments_delta == "{}"


# -- unchanged behaviour ----------------------------------------------------


def test_text_chunks_are_untouched():
    response = GeminiResponseTransformer.transform_streaming_chunk(
        {"candidates": [{"content": {"role": "model", "parts": [{"text": "hi"}]}}]},
        "gemini-3.6-flash",
    )
    assert response.delta.content == "hi"


def test_non_streaming_ids_are_still_index_based():
    unified = GeminiResponseTransformer.transform_response(
        {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"functionCall": {"name": "a", "args": {}}},
                            {"functionCall": {"name": "b", "args": {}}},
                        ]
                    }
                }
            ]
        },
        "gemini-3.6-flash",
    )
    assert [block.id for block in unified.content] == ["gemini_0", "gemini_1"]


# -- usage rides along with content -----------------------------------------


def _usage_chunk(parts, finish="STOP"):
    return {
        "candidates": [
            {"content": {"role": "model", "parts": parts}, "finishReason": finish}
        ],
        "usageMetadata": {
            "promptTokenCount": 11,
            "candidatesTokenCount": 22,
            "totalTokenCount": 33,
        },
    }


def test_usage_survives_a_final_chunk_that_also_has_text():
    """Gemini attaches usageMetadata to the chunk holding the last text part.

    The early `return` on that part used to skip the usage block entirely, so
    the status bar read `0 tok | $0.00` for every Gemini turn.
    """
    response = GeminiResponseTransformer.transform_streaming_chunk(
        _usage_chunk([{"text": "done"}]), "gemini-3.6-flash"
    )
    assert response.usage.total_tokens == 33
    assert response.usage.prompt_tokens == 11
    assert response.usage.completion_tokens == 22
    assert response.delta.content == "done"


def test_usage_survives_a_final_chunk_that_also_has_a_tool_call():
    response = GeminiResponseTransformer.transform_streaming_chunk(
        _usage_chunk([{"functionCall": {"name": "a", "args": {}}}]),
        "gemini-3.6-flash",
    )
    assert response.usage.total_tokens == 33


def test_usage_only_chunk_still_works():
    response = GeminiResponseTransformer.transform_streaming_chunk(
        _usage_chunk([]), "gemini-3.6-flash"
    )
    assert response.usage.total_tokens == 33
    assert response.is_final


def test_mid_stream_chunk_without_usage_reports_none():
    response = GeminiResponseTransformer.transform_streaming_chunk(
        {"candidates": [{"content": {"parts": [{"text": "hi"}]}}]},
        "gemini-3.6-flash",
    )
    assert response.usage is None
