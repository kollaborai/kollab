"""Tests for AnthropicResponseTransformer.transform_anthropic_chunk.

Covers usage extraction across anthropic streaming events, including the
z.ai glm-5.1 case where input_tokens is reported in message_delta rather
than message_start.
"""

import unittest

from kollabor_ai.providers.transformers import AnthropicResponseTransformer


class AnthropicStreamingUsageTests(unittest.TestCase):
    def test_message_start_input_tokens(self):
        chunk = {
            "type": "message_start",
            "message": {
                "usage": {
                    "input_tokens": 42,
                    "cache_creation_input_tokens": 3,
                    "cache_read_input_tokens": 5,
                }
            },
        }
        result = AnthropicResponseTransformer.transform_anthropic_chunk(
            chunk, "claude"
        )
        self.assertIsNotNone(result)
        self.assertIsNotNone(result.usage)
        self.assertEqual(result.usage.prompt_tokens, 50)
        self.assertEqual(result.usage.cache_creation_tokens, 3)
        self.assertEqual(result.usage.cache_read_tokens, 5)
        self.assertEqual(result.usage.completion_tokens, 0)

    def test_message_delta_output_only(self):
        chunk = {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 17},
        }
        result = AnthropicResponseTransformer.transform_anthropic_chunk(
            chunk, "claude"
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.finish_reason, "end_turn")
        self.assertIsNotNone(result.usage)
        self.assertEqual(result.usage.prompt_tokens, 0)
        self.assertEqual(result.usage.completion_tokens, 17)

    def test_message_delta_reports_input_tokens_glm(self):
        """z.ai glm-5.1 sends final input_tokens in message_delta."""
        chunk = {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {
                "input_tokens": 10,
                "output_tokens": 2,
                "cache_read_input_tokens": 0,
            },
        }
        result = AnthropicResponseTransformer.transform_anthropic_chunk(
            chunk, "glm-5.1"
        )
        self.assertIsNotNone(result)
        self.assertIsNotNone(result.usage)
        self.assertEqual(result.usage.prompt_tokens, 10)
        self.assertEqual(result.usage.completion_tokens, 2)
        self.assertEqual(result.usage.total_tokens, 12)
        self.assertEqual(result.usage.cache_read_tokens, 0)

    def test_message_delta_reports_input_tokens_with_cache(self):
        chunk = {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "cache_creation_input_tokens": 20,
                "cache_read_input_tokens": 80,
            },
        }
        result = AnthropicResponseTransformer.transform_anthropic_chunk(
            chunk, "glm-5.1"
        )
        self.assertIsNotNone(result)
        self.assertIsNotNone(result.usage)
        self.assertEqual(result.usage.prompt_tokens, 200)
        self.assertEqual(result.usage.cache_creation_tokens, 20)
        self.assertEqual(result.usage.cache_read_tokens, 80)
        self.assertEqual(result.usage.completion_tokens, 50)

    def test_message_delta_no_usage_returns_none_usage(self):
        chunk = {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {},
        }
        result = AnthropicResponseTransformer.transform_anthropic_chunk(
            chunk, "claude"
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.finish_reason, "end_turn")
        self.assertIsNone(result.usage)


if __name__ == "__main__":
    unittest.main()
