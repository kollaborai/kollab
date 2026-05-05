"""Regression tests for OpenAI-compatible streaming finish reasons."""

from kollabor_ai.providers.models import TextDelta
from kollabor_ai.providers.transformers import OpenAIResponseTransformer


def test_final_chunk_with_finish_reason_but_no_payload_is_preserved():
    chunk = {
        "id": "chatcmpl-test",
        "choices": [
            {
                "delta": {},
                "finish_reason": "tool_calls",
            }
        ],
        "usage": None,
    }

    response = OpenAIResponseTransformer.transform_openai_chunk(chunk, "gpt-test")

    assert response is not None
    assert response.is_final is True
    assert response.finish_reason == "tool_calls"
    assert isinstance(response.delta, TextDelta)
    assert response.delta.content == ""
