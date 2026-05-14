"""Regression: AnthropicProvider._prepare_request must not mutate input.

Before the fix, consecutive same-role messages were merged by overwriting
the first message's `content` field in place. Because the function
appends message dicts by reference, that mutation propagated back to the
caller's `messages` list — and to anything else (like the raw conversation
log) that shared the reference. The raw log then appeared to show
duplicate user data even though the API received a single merged message.
"""

import unittest

from kollabor_ai.providers.anthropic_provider import AnthropicProvider
from kollabor_ai.providers.models import AnthropicConfig


class AnthropicProviderMutationTests(unittest.TestCase):
    def setUp(self):
        config = AnthropicConfig(
            api_key="test-key",
            model="glm-5.1",
            max_tokens=16,
            base_url="https://api.example.com",
        )
        self.provider = AnthropicProvider(config)

    def test_prepare_request_does_not_mutate_input_messages(self):
        messages = [
            {"role": "system", "content": "you are helpful"},
            {"role": "user", "content": "<sys_msg>vault stuff</sys_msg>"},
            {"role": "user", "content": "<sys_msg>nudge stuff</sys_msg>"},
            {"role": "user", "content": "where are we"},
        ]
        # Snapshot exact content of each input message before the call
        before = [{"role": m["role"], "content": m["content"]} for m in messages]

        self.provider._prepare_request(messages)

        # Every input message must be byte-for-byte unchanged
        for i, m in enumerate(messages):
            self.assertEqual(
                m["role"],
                before[i]["role"],
                f"messages[{i}] role mutated",
            )
            self.assertEqual(
                m["content"],
                before[i]["content"],
                f"messages[{i}] content mutated by merger",
            )
            self.assertIsInstance(
                m["content"],
                str,
                f"messages[{i}] content changed from str to {type(m['content']).__name__}",
            )

    def test_prepare_request_still_merges_for_api(self):
        """The fix preserves the merge behavior — API still gets one user message."""
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "a"},
            {"role": "user", "content": "b"},
            {"role": "user", "content": "c"},
        ]
        request = self.provider._prepare_request(messages)
        wire_messages = request["messages"]
        self.assertEqual(len(wire_messages), 1)
        self.assertEqual(wire_messages[0]["role"], "user")
        # Three string messages should merge into a 3-block content array
        self.assertIsInstance(wire_messages[0]["content"], list)
        self.assertEqual(len(wire_messages[0]["content"]), 3)


if __name__ == "__main__":
    unittest.main()
