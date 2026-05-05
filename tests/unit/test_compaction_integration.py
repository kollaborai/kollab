"""Integration test for context compaction with tool call boundaries.

Runs the full _run_compaction -> _apply_pending_compaction pipeline
with a realistic conversation containing tool calls. Mocks only the
LLM summarization call. Validates the resulting history is OpenAI-valid.

Run directly for visual output:  python tests/unit/test_compaction_integration.py
Run via pytest for CI:           pytest tests/unit/test_compaction_integration.py -v
"""

import asyncio
import unittest
from datetime import datetime
from typing import List
from unittest.mock import AsyncMock, MagicMock, patch

from kollabor_events.data_models import ConversationMessage
from plugins.context_compaction_plugin import ContextCompactionPlugin

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

TC_READ = [
    {
        "id": "call_r1",
        "function": {"name": "read_file", "arguments": '{"path":"src/main.py"}'},
    }
]
TC_EDIT = [
    {
        "id": "call_e1",
        "function": {"name": "edit_file", "arguments": '{"path":"src/main.py"}'},
    }
]
TC_MULTI = [
    {"id": "call_m1", "function": {"name": "grep", "arguments": '{"pattern":"TODO"}'}},
    {"id": "call_m2", "function": {"name": "glob", "arguments": '{"pattern":"*.py"}'}},
]


def _msg(role, content, **meta):
    return ConversationMessage(
        role=role, content=content, metadata=meta, timestamp=datetime.now()
    )


def _build_realistic_history() -> List[ConversationMessage]:
    """Build a 20-message conversation with multiple tool call rounds."""
    return [
        _msg("system", "You are a helpful coding assistant."),  # 0
        _msg("user", "can you look at the main file?"),  # 1
        ConversationMessage(
            role="assistant",
            content="let me read it",  # 2
            metadata={"tool_calls": TC_READ},
            timestamp=datetime.now(),
        ),
        _msg("tool", "def main():\n    print('hello')", tool_call_id="call_r1"),  # 3
        _msg("assistant", "it's a simple hello world script"),  # 4
        _msg("user", "add error handling"),  # 5
        ConversationMessage(
            role="assistant",
            content="on it",  # 6
            metadata={"tool_calls": TC_EDIT},
            timestamp=datetime.now(),
        ),
        _msg("tool", "file edited successfully", tool_call_id="call_e1"),  # 7
        _msg("assistant", "done, added try/except around the main call"),  # 8
        _msg("user", "find all TODOs in the project"),  # 9
        ConversationMessage(
            role="assistant",
            content="searching",  # 10
            metadata={"tool_calls": TC_MULTI},
            timestamp=datetime.now(),
        ),
        _msg(
            "tool", "src/main.py:5: # TODO: add logging", tool_call_id="call_m1"
        ),  # 11
        _msg(
            "tool",
            "src/main.py\nsrc/utils.py\ntests/test_main.py",
            tool_call_id="call_m2",
        ),  # 12
        _msg("assistant", "found 1 TODO in main.py, and 3 python files total"),  # 13
        _msg("user", "fix that TODO"),  # 14
        ConversationMessage(
            role="assistant",
            content="fixing",  # 15
            metadata={"tool_calls": TC_EDIT},
            timestamp=datetime.now(),
        ),
        _msg("tool", "added logging module", tool_call_id="call_e1"),  # 16
        _msg("assistant", "added logging to main.py, the TODO is resolved"),  # 17
        _msg("user", "nice, now write tests"),  # 18
        _msg("assistant", "i'll create test_main.py with unittest"),  # 19
    ]


def _validate_openai_message_order(history: List[ConversationMessage]) -> List[str]:
    """Validate the message sequence is valid for OpenAI API.

    Returns list of violations found (empty = valid).
    """
    violations = []

    for i, msg in enumerate(history):
        # Rule 1: tool role must follow an assistant with matching tool_calls
        if msg.role == "tool" or msg.metadata.get("tool_call_id"):
            tool_call_id = msg.metadata.get("tool_call_id")

            # Walk backward to find the owning assistant
            found_owner = False
            for j in range(i - 1, -1, -1):
                prev = history[j]
                if prev.role == "assistant" and prev.metadata.get("tool_calls"):
                    tc_ids = [tc.get("id") for tc in prev.metadata["tool_calls"]]
                    if tool_call_id in tc_ids:
                        found_owner = True
                        break
                # If we hit a user message, the chain is broken
                if prev.role == "user":
                    break

            if not found_owner:
                violations.append(
                    f"  [idx {i}] tool result (call_id={tool_call_id}) "
                    f"has no preceding assistant with matching tool_calls"
                )

        # Rule 2: assistant with tool_calls must be followed by tool results
        if msg.role == "assistant" and msg.metadata.get("tool_calls"):
            tc_ids = {tc.get("id") for tc in msg.metadata["tool_calls"]}
            found_ids = set()
            for j in range(i + 1, len(history)):
                nxt = history[j]
                if nxt.role == "tool" or nxt.metadata.get("tool_call_id"):
                    tid = nxt.metadata.get("tool_call_id")
                    if tid in tc_ids:
                        found_ids.add(tid)
                else:
                    break  # non-tool message ends the group

            missing = tc_ids - found_ids
            if missing:
                violations.append(
                    f"  [idx {i}] assistant with tool_calls missing "
                    f"results for: {missing}"
                )

    return violations


def _role_tag(msg):
    """Short label for printing."""
    if msg.role == "system":
        return "SYS"
    if msg.role == "user":
        return "USR"
    if msg.role == "tool" or msg.metadata.get("tool_call_id"):
        return "TOL"
    if msg.role == "assistant" and msg.metadata.get("tool_calls"):
        names = [tc["function"]["name"] for tc in msg.metadata["tool_calls"]]
        return f"AST+{'+'.join(names)}"
    return "AST"


# ---------------------------------------------------------------------------
# plugin factory with mocked LLM
# ---------------------------------------------------------------------------


def _make_plugin_with_history(history: List[ConversationMessage], keep_recent=6):
    """Create plugin wired to a live history list with mocked config."""
    config = MagicMock()
    config_values = {
        "plugins.context_compaction.enabled": True,
        "plugins.context_compaction.keep_recent": keep_recent,
        "plugins.context_compaction.max_summary_tokens": 2000,
        "plugins.context_compaction.log_compaction_events": False,
        "plugins.context_compaction.count_mode": "interactions",
        "plugins.context_compaction.trigger_threshold": 16,
        "plugins.context_compaction.re_trigger_threshold": 12,
    }
    config.get = lambda key, default=None: config_values.get(key, default)

    bus = MagicMock()
    renderer = MagicMock()
    plugin = ContextCompactionPlugin("test", bus, renderer, config)

    # Wire up a fake llm_service with the history
    llm_service = MagicMock()
    llm_service.conversation_history = history
    plugin._llm_service = llm_service

    # Mock session
    conv_logger = MagicMock()
    conv_logger.session_id = "test-session-001"
    plugin._conversation_logger = conv_logger

    return plugin


FAKE_SUMMARY = (
    "The user asked to inspect main.py, which is a simple hello world script. "
    "Error handling was added with try/except. A project-wide TODO search found "
    "one TODO in main.py for adding logging. The TODO was resolved by adding the "
    "logging module. The user then requested writing tests."
)


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------


class TestCompactionIntegration(unittest.IsolatedAsyncioTestCase):
    """Full pipeline: _run_compaction stages, _apply_pending_compaction swaps."""

    async def test_full_compaction_preserves_tool_boundaries(self):
        """Run compaction on realistic history, verify result is OpenAI-valid."""
        history = _build_realistic_history()
        original_len = len(history)
        plugin = _make_plugin_with_history(history, keep_recent=6)

        # Mock the LLM summarization call
        with patch.object(
            plugin, "_call_summarization_llm", new_callable=AsyncMock
        ) as mock_llm:
            mock_llm.return_value = FAKE_SUMMARY

            # Phase 1: run compaction (stages result)
            await plugin._run_compaction()

            self.assertIsNotNone(
                plugin._pending_compaction, "compaction should have staged a result"
            )
            self.assertEqual(plugin._compaction_round, 1)

            # Phase 2: apply on next pre-request
            await plugin._apply_pending_compaction({}, MagicMock())

            self.assertIsNone(
                plugin._pending_compaction, "pending should be cleared after apply"
            )

        # Validate
        compacted_len = len(history)
        self.assertLess(
            compacted_len,
            original_len,
            f"history should be shorter: {compacted_len} >= {original_len}",
        )

        violations = _validate_openai_message_order(history)
        self.assertEqual(
            violations, [], "OpenAI message order violations:\n" + "\n".join(violations)
        )

        # First message should be system, second should be the summary
        self.assertEqual(history[0].role, "system")
        self.assertEqual(history[1].role, "user")
        self.assertIn("Previous Context Summary", history[1].content)

        # No tool result should appear without its owning assistant
        for i, msg in enumerate(history):
            if plugin._is_tool_result(msg):
                # There must be an assistant with tool_calls before it
                found = False
                for j in range(i - 1, -1, -1):
                    if plugin._has_tool_calls(history[j]):
                        found = True
                        break
                    if history[j].role == "user":
                        break
                self.assertTrue(
                    found, f"orphaned tool result at index {i}: {msg.metadata}"
                )

    async def test_compaction_with_tool_calls_at_boundary(self):
        """Specifically test when keep_recent split lands on a tool group."""
        history = _build_realistic_history()
        # keep_recent=8 puts naive split at index 12 (a tool result from TC_MULTI)
        plugin = _make_plugin_with_history(history, keep_recent=8)

        with patch.object(
            plugin, "_call_summarization_llm", new_callable=AsyncMock
        ) as mock_llm:
            mock_llm.return_value = FAKE_SUMMARY
            await plugin._run_compaction()
            await plugin._apply_pending_compaction({}, MagicMock())

        violations = _validate_openai_message_order(history)
        self.assertEqual(
            violations, [], "violations at keep_recent=8:\n" + "\n".join(violations)
        )

    async def test_compaction_keeps_4_recent(self):
        """Aggressive compaction with keep_recent=4."""
        history = _build_realistic_history()
        plugin = _make_plugin_with_history(history, keep_recent=4)

        with patch.object(
            plugin, "_call_summarization_llm", new_callable=AsyncMock
        ) as mock_llm:
            mock_llm.return_value = FAKE_SUMMARY
            await plugin._run_compaction()
            await plugin._apply_pending_compaction({}, MagicMock())

        violations = _validate_openai_message_order(history)
        self.assertEqual(
            violations, [], "violations at keep_recent=4:\n" + "\n".join(violations)
        )


# ---------------------------------------------------------------------------
# visual runner
# ---------------------------------------------------------------------------


async def _visual_run():
    """Run compaction and print before/after for visual inspection."""
    history = _build_realistic_history()
    original = list(history)  # snapshot

    print("=" * 70)
    print("  BEFORE compaction")
    print("=" * 70)
    for i, msg in enumerate(history):
        tag = _role_tag(msg)
        content = msg.content[:60].replace("\n", " ")
        print(f"  [{i:2d}] {tag:<20s} {content}")

    plugin = _make_plugin_with_history(history, keep_recent=6)

    with patch.object(
        plugin, "_call_summarization_llm", new_callable=AsyncMock
    ) as mock_llm:
        mock_llm.return_value = FAKE_SUMMARY
        await plugin._run_compaction()
        await plugin._apply_pending_compaction({}, MagicMock())

    print()
    print("=" * 70)
    print("  AFTER compaction")
    print("=" * 70)
    for i, msg in enumerate(history):
        tag = _role_tag(msg)
        content = msg.content[:60].replace("\n", " ")
        print(f"  [{i:2d}] {tag:<20s} {content}")

    # Show what was removed
    print()
    print("-" * 70)
    print(f"  before: {len(original)} messages")
    print(f"  after:  {len(history)} messages")
    print(f"  removed: {len(original) - len(history)} messages")
    print()

    # Bucket summary
    removed_roles = {"user": 0, "assistant": 0, "tool": 0, "assistant+tools": 0}
    kept_ids = {id(m) for m in history}
    for msg in original:
        if id(msg) not in kept_ids:
            if msg.role == "assistant" and msg.metadata.get("tool_calls"):
                removed_roles["assistant+tools"] += 1
            elif msg.role == "tool" or msg.metadata.get("tool_call_id"):
                removed_roles["tool"] += 1
            else:
                removed_roles[msg.role] = removed_roles.get(msg.role, 0) + 1

    print("  removed by type:")
    for role, count in removed_roles.items():
        if count > 0:
            bar = "*" * count
            print(f"    {role:<20s} {count:>2d}  {bar}")

    # Kept messages
    kept_roles = {
        "system": 0,
        "user": 0,
        "assistant": 0,
        "tool": 0,
        "assistant+tools": 0,
        "summary": 0,
    }
    for msg in history:
        if "Previous Context Summary" in msg.content:
            kept_roles["summary"] += 1
        elif msg.role == "assistant" and msg.metadata.get("tool_calls"):
            kept_roles["assistant+tools"] += 1
        elif msg.role == "tool" or msg.metadata.get("tool_call_id"):
            kept_roles["tool"] += 1
        else:
            kept_roles[msg.role] = kept_roles.get(msg.role, 0) + 1

    print()
    print("  kept by type:")
    for role, count in kept_roles.items():
        if count > 0:
            bar = "=" * count
            print(f"    {role:<20s} {count:>2d}  {bar}")

    # Validate
    print()
    violations = _validate_openai_message_order(history)
    if violations:
        print("  !! VIOLATIONS FOUND:")
        for v in violations:
            print(f"    {v}")
    else:
        print("  openai message order: VALID")

    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(_visual_run())
