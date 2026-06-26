"""Context-budget guard regression tests.

A single turn that injects a large payload (e.g. reading a big file) can push a
request past the model's context window; the call then returns empty
(stop_reason=model_context_window_exceeded) and every later turn inherits the
same too-large history and fails the same way. _enforce_token_budget bounds the
request (not the stored history) so the wire payload always fits.
"""

import types
import unittest

from kollabor_ai.api_communication_service import APICommunicationService


def _service(context_window=256000, max_tokens=16384, config=None):
    svc = APICommunicationService.__new__(APICommunicationService)
    svc._provider = types.SimpleNamespace(
        config=types.SimpleNamespace(
            context_window=context_window, max_tokens=max_tokens
        )
    )
    svc.config = config
    return svc


def _msg(role, text):
    return {"role": role, "content": text}


class TestContextBudgetGuard(unittest.TestCase):
    def test_oversized_history_is_trimmed_to_fit(self):
        svc = _service()
        # ~100k tokens each at ~3 chars/token; three of them blow any window.
        big = "x" * 300_000
        history = [
            _msg("user", big),
            _msg("assistant", big),
            _msg("user", big),
            _msg("assistant", "ok"),
            _msg("user", "hello?"),
        ]
        kept = svc._enforce_token_budget(list(history))

        budget = 256000 - 16384 - 60000 - 4000
        after = sum(svc._message_tokens(m) for m in kept)
        self.assertLessEqual(after, budget)
        # current turn is always preserved
        self.assertEqual(kept[-1]["content"], "hello?")
        # oldest turns were dropped
        self.assertLess(len(kept), len(history))

    def test_small_history_untouched(self):
        svc = _service()
        history = [_msg("user", "hi"), _msg("assistant", "hello"), _msg("user", "?")]
        self.assertEqual(svc._enforce_token_budget(list(history)), history)

    def test_unknown_window_is_left_alone(self):
        # No window configured -> never risk over-trimming.
        svc = _service(context_window=0)
        big = "x" * 2_000_000
        history = [_msg("user", big), _msg("user", big)]
        self.assertEqual(len(svc._enforce_token_budget(list(history))), 2)

    def test_trimmed_window_starts_on_clean_user_turn(self):
        svc = _service()
        big = "x" * 300_000
        history = [
            _msg("user", big),
            {"role": "tool", "content": "result", "tool_call_id": "t1"},
            _msg("assistant", big),
            _msg("user", "current"),
        ]
        kept = svc._enforce_token_budget(list(history))
        # never leads with an orphan tool result or assistant turn
        self.assertNotEqual(kept[0].get("role"), "tool")
        self.assertNotIn("tool_call_id", kept[0])

    def test_overhead_is_configurable(self):
        # A huge configured overhead shrinks the budget and forces more trimming.
        class Cfg:
            def get(self, key, default):
                if key == "kollabor.llm.context_overhead_tokens":
                    return 200000
                return default

        svc = _service(config=Cfg())
        body = "x" * 120_000  # ~40k tokens
        history = [_msg("user", body), _msg("assistant", body), _msg("user", "now")]
        kept = svc._enforce_token_budget(list(history))
        self.assertEqual(kept[-1]["content"], "now")
        self.assertLess(len(kept), len(history))


if __name__ == "__main__":
    unittest.main()
