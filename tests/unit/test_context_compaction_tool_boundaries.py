"""Tests for context compaction tool call boundary handling.

Verifies that _find_split_point never breaks an assistant+tool_calls /
tool_result group, which would cause 400 errors from OpenAI-compatible APIs.
"""

import unittest
from datetime import datetime
from unittest.mock import MagicMock

from kollabor_events.data_models import ConversationMessage
from plugins.context_compaction_plugin import ContextCompactionPlugin


def _msg(role, content="msg", **meta):
    """Helper to create ConversationMessage with metadata."""
    return ConversationMessage(
        role=role, content=content, metadata=meta, timestamp=datetime.now()
    )


def _system():
    return _msg("system", "You are helpful.")


def _user(content="hello"):
    return _msg("user", content)


def _assistant(content="hi", tool_calls=None):
    meta = {}
    if tool_calls:
        meta["tool_calls"] = tool_calls
    return ConversationMessage(
        role="assistant", content=content, metadata=meta, timestamp=datetime.now()
    )


def _tool_result(tool_call_id="call_123"):
    return _msg("tool", "result output", tool_call_id=tool_call_id)


def _make_plugin():
    """Create a minimal plugin instance for testing."""
    bus = MagicMock()
    renderer = MagicMock()
    config = MagicMock()
    config.get = MagicMock(return_value=True)
    return ContextCompactionPlugin("test", bus, renderer, config)


SAMPLE_TOOL_CALLS = [
    {"id": "call_1", "function": {"name": "read_file", "arguments": "{}"}}
]


class TestFindSplitPoint(unittest.TestCase):
    """Test _find_split_point respects tool call boundaries."""

    def setUp(self):
        self.plugin = _make_plugin()

    # ---- basic cases (no tool calls) ----

    def test_short_history_returns_zero(self):
        history = [_system(), _user(), _assistant()]
        self.assertEqual(self.plugin._find_split_point(history, keep_recent=4), 0)

    def test_normal_split_no_tools(self):
        history = [
            _system(),
            _user("q1"),
            _assistant("a1"),
            _user("q2"),
            _assistant("a2"),
            _user("q3"),
            _assistant("a3"),
            _user("q4"),
            _assistant("a4"),
        ]
        # keep_recent=4 -> split at 5
        split = self.plugin._find_split_point(history, keep_recent=4)
        self.assertEqual(split, 5)

    # ---- split lands ON a tool result ----

    def test_split_on_tool_result_walks_back(self):
        """Split should never start to_keep on a tool result message."""
        history = [
            _system(),
            _user("q1"),
            _assistant("a1"),
            _user("q2"),
            _assistant("used tool", tool_calls=SAMPLE_TOOL_CALLS),
            _tool_result("call_1"),  # index 5 -- naive split lands here
            _user("q3"),
            _assistant("a3"),
            _user("q4"),
        ]
        # keep_recent=4 -> naive split = 9-4 = 5 (the tool result)
        split = self.plugin._find_split_point(history, keep_recent=4)
        # Should walk back to include the assistant with tool_calls
        self.assertLessEqual(split, 4)
        # to_keep should start with the assistant that has tool_calls
        self.assertEqual(history[split].role, "assistant")
        self.assertTrue(self.plugin._has_tool_calls(history[split]))

    def test_split_on_second_tool_result(self):
        """When assistant made multiple tool calls, all results stay together."""
        tc = [
            {"id": "call_A", "function": {"name": "read", "arguments": "{}"}},
            {"id": "call_B", "function": {"name": "write", "arguments": "{}"}},
        ]
        history = [
            _system(),
            _user("q1"),
            _assistant("a1"),
            _user("q2"),
            _assistant("multi-tool", tool_calls=tc),  # index 4
            _tool_result("call_A"),  # index 5
            _tool_result("call_B"),  # index 6
            _user("q3"),
            _assistant("a3"),
        ]
        # keep_recent=3 -> naive split = 9-3 = 6 (second tool result)
        split = self.plugin._find_split_point(history, keep_recent=3)
        self.assertLessEqual(split, 4)
        self.assertEqual(history[split].role, "assistant")

    # ---- split lands right AFTER assistant with tool_calls ----

    def test_split_after_assistant_with_tool_calls(self):
        """If msg before split is assistant+tool_calls, pull it into to_keep."""
        history = [
            _system(),
            _user("q1"),
            _assistant("a1"),
            _user("q2"),
            _assistant("a2"),
            _assistant("tools", tool_calls=SAMPLE_TOOL_CALLS),  # index 5
            _tool_result("call_1"),  # index 6
            _user("q3"),
            _assistant("a3"),
        ]
        # keep_recent=3 -> naive split = 9-3 = 6 (tool result)
        split = self.plugin._find_split_point(history, keep_recent=3)
        self.assertLessEqual(split, 5)

    # ---- to_summarize ends with assistant+tool_calls ----

    def test_summarize_side_doesnt_end_with_orphaned_assistant(self):
        """to_summarize shouldn't end with an assistant that has tool_calls
        whose results are in to_keep."""
        history = [
            _system(),
            _user("q1"),
            _assistant("a1"),
            _user("q2"),
            _assistant("tools", tool_calls=SAMPLE_TOOL_CALLS),  # index 4
            _tool_result("call_1"),  # index 5
            _assistant("after tools"),  # index 6
            _user("q3"),
            _assistant("a3"),
        ]
        # keep_recent=3 -> naive split = 9-3 = 6
        # to_summarize = [0:6], to_keep = [6:9]
        # to_summarize[-1] is tool_result at 5. But wait...
        # let me recalculate: history has 9 items, keep_recent=3
        # naive split=6, to_summarize=[0:6], to_keep=[6:9]
        # to_summarize ends with _tool_result at index 5
        # the assistant at 4 has tool_calls, tool result at 5 matches
        # this is actually fine -- both are in to_summarize
        split = self.plugin._find_split_point(history, keep_recent=3)
        self.assertEqual(split, 6)

    def test_orphaned_assistant_at_boundary(self):
        """Assistant with tool_calls at end of to_summarize, results in to_keep."""
        history = [
            _system(),
            _user("q1"),
            _assistant("a1"),
            _user("q2"),
            _assistant("a2"),
            _assistant("tools", tool_calls=SAMPLE_TOOL_CALLS),  # index 5
            _tool_result("call_1"),  # index 6
            _user("q3"),
        ]
        # keep_recent=2 -> naive split = 8-2 = 6 (tool result)
        # walk back: 6 is tool result -> 5 is assistant w/ tool_calls
        # pull assistant into to_keep
        split = self.plugin._find_split_point(history, keep_recent=2)
        self.assertLessEqual(split, 5)
        # Verify the kept portion starts clean
        kept = history[split:]
        self.assertFalse(self.plugin._is_tool_result(kept[0]))

    # ---- mixed scenario ----

    def test_complex_multi_tool_sequence(self):
        """Realistic scenario with multiple tool call rounds."""
        tc1 = [{"id": "c1", "function": {"name": "read", "arguments": "{}"}}]
        tc2 = [
            {"id": "c2", "function": {"name": "edit", "arguments": "{}"}},
            {"id": "c3", "function": {"name": "write", "arguments": "{}"}},
        ]
        history = [
            _system(),  # 0
            _user("fix the bug"),  # 1
            _assistant("let me look", tool_calls=tc1),  # 2
            _tool_result("c1"),  # 3
            _assistant("found it", tool_calls=tc2),  # 4
            _tool_result("c2"),  # 5
            _tool_result("c3"),  # 6
            _assistant("fixed, here's what i did"),  # 7
            _user("nice, now add tests"),  # 8
            _assistant("on it"),  # 9
        ]
        # keep_recent=4 -> naive split = 10-4 = 6 (tool result c3)
        split = self.plugin._find_split_point(history, keep_recent=4)
        # Should walk back past both tool results and the assistant
        self.assertLessEqual(split, 4)
        kept = history[split:]
        self.assertFalse(self.plugin._is_tool_result(kept[0]))

    def test_all_tool_calls_returns_past_system(self):
        """Walking back to include the tool group stops at the assistant.
        Split=1 is correct: system msg at 0 is separated by _run_compaction,
        and to_summarize=history[1:1]=[] triggers the 'nothing to summarize'
        guard, so the entire conversation is preserved."""
        history = [
            _system(),
            _assistant("tools", tool_calls=SAMPLE_TOOL_CALLS),
            _tool_result("call_1"),
            _user("ok"),
        ]
        split = self.plugin._find_split_point(history, keep_recent=3)
        # split=1 means everything after system is kept
        self.assertEqual(split, 1)
        # Verify the kept portion is valid (no orphaned tool results)
        kept = history[split:]
        self.assertFalse(self.plugin._is_tool_result(kept[0]))


class TestHelperMethods(unittest.TestCase):
    """Test _is_tool_result and _has_tool_calls."""

    def test_is_tool_result_by_role(self):
        msg = _msg("tool", "output")
        self.assertTrue(ContextCompactionPlugin._is_tool_result(msg))

    def test_is_tool_result_by_metadata(self):
        msg = _msg("user", "result", tool_call_id="call_1")
        self.assertTrue(ContextCompactionPlugin._is_tool_result(msg))

    def test_not_tool_result(self):
        msg = _msg("assistant", "hello")
        self.assertFalse(ContextCompactionPlugin._is_tool_result(msg))

    def test_has_tool_calls(self):
        msg = _assistant("hi", tool_calls=SAMPLE_TOOL_CALLS)
        self.assertTrue(ContextCompactionPlugin._has_tool_calls(msg))

    def test_no_tool_calls(self):
        msg = _assistant("hi")
        self.assertFalse(ContextCompactionPlugin._has_tool_calls(msg))

    def test_user_with_tool_calls_metadata_is_false(self):
        """Only assistant messages should register as having tool_calls."""
        msg = _msg("user", "hi", tool_calls=SAMPLE_TOOL_CALLS)
        self.assertFalse(ContextCompactionPlugin._has_tool_calls(msg))


class TestFormatMessagesForSummary(unittest.TestCase):
    """Test that summary formatting handles tool messages correctly."""

    def setUp(self):
        self.plugin = _make_plugin()

    def test_skips_tool_results(self):
        msgs = [
            _user("fix bug"),
            _assistant("looking", tool_calls=SAMPLE_TOOL_CALLS),
            _tool_result("call_1"),
            _assistant("done"),
        ]
        formatted = self.plugin._format_messages_for_summary(msgs)
        self.assertNotIn("[Tool]:", formatted)
        self.assertNotIn("result output", formatted)

    def test_annotates_tool_usage(self):
        msgs = [
            _user("read the file"),
            _assistant("on it", tool_calls=SAMPLE_TOOL_CALLS),
        ]
        formatted = self.plugin._format_messages_for_summary(msgs)
        self.assertIn("[Used tools: read_file]", formatted)

    def test_plain_messages_unchanged(self):
        msgs = [_user("hello"), _assistant("hi")]
        formatted = self.plugin._format_messages_for_summary(msgs)
        self.assertIn("[User]: hello", formatted)
        self.assertIn("[Assistant]: hi", formatted)


if __name__ == "__main__":
    unittest.main()
