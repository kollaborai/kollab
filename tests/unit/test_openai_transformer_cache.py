"""Focused tests for generic OpenAI cache usage normalization."""

from kollabor_ai.providers.transformers import OpenAIResponseTransformer


def test_nonstream_cache_creation_aliases():
    response = OpenAIResponseTransformer.transform_openai_response(
        {
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "total_tokens": 12,
                "prompt_tokens_details": {"cache_creation_tokens": 4},
            },
        },
        "gpt-4",
    )
    assert response.usage.cache_creation_tokens == 4
    assert response.usage.cache_read_tokens == 0


def test_nonstream_cache_creation_defaults_zero():
    response = OpenAIResponseTransformer.transform_openai_response(
        {"choices": [{"message": {"content": "ok"}}]}, "gpt-4"
    )
    assert response.usage.cache_creation_tokens == 0
    assert response.usage.cache_read_tokens == 0
