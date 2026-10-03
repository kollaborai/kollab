"""Request preparation sends the whole history: nothing is trimmed to fit.

Oversized tool output is bounded where it is produced (file-read caps,
_cap_tool_output, the tool-batch budget) and compaction shrinks the stored
history. The request builder only keeps the window valid for the API.
"""

import types
import unittest

from kollabor_ai.api_communication_service import APICommunicationService


def _service():
    svc = APICommunicationService.__new__(APICommunicationService)
    svc._provider = types.SimpleNamespace(
        config=types.SimpleNamespace(context_window=128000, max_tokens=16384)
    )
    svc.config = None
    return svc


def _msg(role, text):
    return {"role": role, "content": text}


def _tool(text, call_id="t1"):
    return {"role": "tool", "content": text, "metadata": {"tool_call_id": call_id}}


class TestPrepareMessages(unittest.TestCase):
    def test_oversized_history_is_sent_whole(self):
        # ~100k tokens each: far past a 128k window, and still nothing is dropped.
        big = "x" * 300_000
        history = [
            _msg("user", big),
            _msg("assistant", big),
            _msg("user", big),
            _msg("assistant", "ok"),
            _msg("user", "hello?"),
        ]
        out = _service()._prepare_messages(history)
        self.assertEqual(
            [(m["role"], m["content"]) for m in out],
            [(m["role"], m["content"]) for m in history],
        )

    def test_window_cut_mid_exchange_starts_on_a_user_turn(self):
        # e.g. compaction kept a window that begins with a tool result
        history = [_tool("r"), _msg("assistant", "a1"), _msg("user", "q2")]
        out = _service()._prepare_messages(history)
        self.assertEqual(out, [_msg("user", "q2")])

    def test_sole_orphan_tool_result_becomes_recovery_input(self):
        """Never send a function output without its function call."""
        out = APICommunicationService._strip_leading_orphans(
            [{"role": "tool", "content": "x", "tool_call_id": "t1"}]
        )
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["role"], "user")
        self.assertIn("context recovery", out[0]["content"])

    def test_empty_input_becomes_recovery_input(self):
        """The provider must never receive an empty input array."""
        out = APICommunicationService._strip_leading_orphans([])
        self.assertEqual(len(out), 1)
        self.assertIn("context recovery", out[0]["content"])


if __name__ == "__main__":
    unittest.main()
