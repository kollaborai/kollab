"""Tests for the unified raw conversation log model.

The on-disk shape of ``_raw.jsonl`` is a public surface — external tools
read it for debugging, audits, and replay. Pin the schema here so any
field rename or rearrangement is caught at test time, not by a user
complaining their parser broke.
"""

import json
import unittest

from kollabor_ai.raw_log import (
    SCHEMA_VERSION,
    LocalMessage,
    ProfileSnapshot,
    RawInteraction,
    RawRequest,
    RawResponse,
)


class RawLogShapeTests(unittest.TestCase):
    def test_schema_version_is_int(self):
        self.assertIsInstance(SCHEMA_VERSION, int)
        self.assertGreaterEqual(SCHEMA_VERSION, 1)

    def test_default_interaction_serializes_to_expected_shape(self):
        interaction = RawInteraction()
        d = interaction.to_dict()

        # Top-level keys are the contract
        expected_top = {
            "schema_version",
            "turn_id",
            "continuation_of",
            "timestamp",
            "session_id",
            "duration_s",
            "cancelled",
            "error",
            "profile",
            "request",
            "response",
        }
        self.assertEqual(set(d.keys()), expected_top)

        # Request keys
        self.assertEqual(
            set(d["request"].keys()),
            {"conversation_local", "wire_request", "wire_provider", "tools"},
        )

        # Response keys
        self.assertEqual(
            set(d["response"].keys()),
            {
                "content",
                "token_usage",
                "tool_calls",
                "stop_reason",
                "thinking",
                "raw_chunks",
            },
        )

        # Profile keys
        self.assertEqual(
            set(d["profile"].keys()),
            {"provider", "model", "base_url", "streaming"},
        )

    def test_round_trip_through_json(self):
        """Whole structure must survive json.dumps -> json.loads."""
        interaction = RawInteraction(
            turn_id="abc",
            session_id="sess1",
            duration_s=1.23,
            profile=ProfileSnapshot(
                provider="anthropic",
                model="glm-5.1",
                base_url="https://api.example.com",
                streaming=True,
            ),
            request=RawRequest(
                conversation_local=[
                    LocalMessage(role="user", content="hi"),
                ],
                wire_request={"messages": [{"role": "user", "content": "hi"}]},
                wire_provider="anthropic",
                tools=[],
            ),
            response=RawResponse(
                content="hello",
                token_usage={"prompt_tokens": 5, "completion_tokens": 3},
                stop_reason="end_turn",
                raw_chunks=[{"type": "message_delta", "usage": {"input_tokens": 5}}],
            ),
        )
        serialized = json.dumps(interaction.to_dict())
        loaded = json.loads(serialized)
        self.assertEqual(loaded["turn_id"], "abc")
        self.assertEqual(loaded["profile"]["model"], "glm-5.1")
        self.assertEqual(loaded["request"]["wire_provider"], "anthropic")
        self.assertEqual(loaded["response"]["token_usage"]["prompt_tokens"], 5)
        self.assertEqual(
            loaded["response"]["raw_chunks"][0]["usage"]["input_tokens"], 5
        )

    def test_continuation_of_is_optional(self):
        """continuation_of is None for fresh turns, set for follow-ups."""
        fresh = RawInteraction(turn_id="t1")
        self.assertIsNone(fresh.continuation_of)
        follow_up = RawInteraction(turn_id="t2", continuation_of="t1")
        self.assertEqual(follow_up.continuation_of, "t1")


if __name__ == "__main__":
    unittest.main()
