"""Regression coverage for bounded, TaskLedger-aware hub context retention."""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from plugins.context_compaction_plugin import (  # noqa: E402
    MAX_PRESERVED_HUB_TASK_CHARS,
    MAX_PRESERVED_HUB_TASK_MESSAGES,
    ContextCompactionPlugin,
)


@dataclass
class Message:
    role: str = "user"
    content: Any = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


def _task_message(task_id: str, detail: str) -> Message:
    return Message(
        content=f"[hub channel: koordinator -> sapphire]\nTask: {detail}",
        metadata={
            "hub_message": True,
            "hub_is_intended": True,
            "task_id": task_id,
        },
    )


def _plugin() -> ContextCompactionPlugin:
    plugin = ContextCompactionPlugin.__new__(ContextCompactionPlugin)
    plugin.event_bus = None
    return plugin


def test_only_latest_bounded_messages_for_live_tasks_are_preserved() -> None:
    archive = [
        _task_message(f"closed-{index}", f"archived task {index}")
        for index in range(257)
    ]
    active_earlier = _task_message("active-1", "old active update")
    open_earlier = _task_message("open-1", "old queued update")
    active_latest = _task_message("active-1", "latest active update")
    open_latest = _task_message("open-1", "latest queued update")
    messages = archive + [
        active_earlier,
        open_earlier,
        active_latest,
        open_latest,
    ]

    preserved, summarizable = _plugin()._extract_preservable_messages(
        messages, live_task_ids={"active-1", "open-1"}
    )

    assert preserved == [active_latest, open_latest]
    assert len(preserved) <= MAX_PRESERVED_HUB_TASK_MESSAGES
    assert active_earlier in summarizable
    assert open_earlier in summarizable
    assert all(message in summarizable for message in archive)


def test_preservation_has_a_hard_cap_even_if_many_tasks_are_live() -> None:
    messages = [
        _task_message(f"live-{index}", f"current task {index}")
        for index in range(MAX_PRESERVED_HUB_TASK_MESSAGES * 3)
    ]
    live_ids = {message.metadata["task_id"] for message in messages}

    preserved, summarizable = _plugin()._extract_preservable_messages(
        messages, live_task_ids=live_ids
    )

    assert len(preserved) == MAX_PRESERVED_HUB_TASK_MESSAGES
    assert preserved == messages[-MAX_PRESERVED_HUB_TASK_MESSAGES:]
    assert summarizable == messages[:-MAX_PRESERVED_HUB_TASK_MESSAGES]



def test_oversized_live_task_is_bounded_and_left_for_summary() -> None:
    message = _task_message("large-task", "x" * 20_000)

    preserved, summarizable = _plugin()._extract_preservable_messages(
        [message], live_task_ids={"large-task"}
    )

    assert len(preserved) == 1
    assert len(preserved[0].content) <= MAX_PRESERVED_HUB_TASK_CHARS
    assert summarizable == [message]


def test_live_task_ids_include_assigned_paused_and_targeted_open_cards() -> None:
    active = SimpleNamespace(id="active", assignee="sapphire", status="active")
    paused = SimpleNamespace(id="paused", assignee="sapphire", status="paused")
    qa_review = SimpleNamespace(
        id="qa-review", assignee="sapphire", status="qa_review"
    )
    queued_for_sapphire = SimpleNamespace(
        id="open-sapphire", assignee="", status="open", directive="sapphire: do work"
    )
    queued_for_other = SimpleNamespace(
        id="open-other", assignee="", status="open", directive="zircon: do work"
    )
    completed = SimpleNamespace(id="done", assignee="sapphire", status="done")

    class Ledger:
        def get_active_for(self, identity):
            assert identity == "sapphire"
            return [active, qa_review]

        def get_all(self, status=None):
            return [paused, completed]

        def get_open(self):
            return [queued_for_sapphire, queued_for_other]

    class HubPlugin:
        _task_ledger = Ledger()
        _identity = SimpleNamespace(identity="sapphire")

    class EventBus:
        @staticmethod
        def get_service(name):
            return HubPlugin() if name == "hub_plugin" else None

    plugin = _plugin()
    plugin.event_bus = EventBus()

    assert plugin._get_live_task_ids() == {"active", "paused", "qa-review", "open-sapphire"}


def test_coordination_extraction_does_not_restore_archived_task_messages() -> None:
    archived = [
        _task_message(f"closed-{index}", f"stale assignment body {index}")
        for index in range(257)
    ]
    archived_without_ids = [
        Message(
            content="[hub channel: koordinator -> sapphire] Please complete archived task "
            + str(index)
            + " and report back when done",
            metadata={"hub_message": True, "hub_is_intended": True},
        )
        for index in range(257)
    ]

    extracted = ContextCompactionPlugin._extract_coordination_lines(
        archived + archived_without_ids
    )

    assert extracted == []


def test_context_overflow_forces_one_compaction_past_the_shrink_floor() -> None:
    plugin = _plugin()
    plugin._pending_compaction = None
    plugin._compaction_in_progress = False
    plugin._disabled_for_session = False
    plugin._shrink_floor = 450_000
    plugin._compaction_just_applied = True
    plugin._get_conversation_history = lambda: [Message(content="old"), Message(content="new")]
    plugin._keep_recent_count = lambda: 1
    plugin._find_split_point = lambda history, keep_recent: 2
    starts = []
    statuses = []
    plugin._start_compaction = lambda reason="threshold": starts.append(reason) or True
    plugin._set_compaction_status = lambda status, message, action="": statuses.append(status)

    asyncio.run(
        plugin._on_llm_request_error(
            {"context_window_exceeded": True, "error_code": "context_window_exceeded"},
            None,
        )
    )

    assert starts == ["context_window_exceeded"]
    assert plugin._shrink_floor is None
    assert plugin._compaction_just_applied is False
    assert statuses == ["compacting"]


def test_non_context_request_error_does_not_force_compaction() -> None:
    plugin = _plugin()
    plugin._pending_compaction = None
    plugin._compaction_in_progress = False
    plugin._disabled_for_session = False
    starts = []
    plugin._start_compaction = lambda reason="threshold": starts.append(reason) or True

    asyncio.run(plugin._on_llm_request_error({"context_window_exceeded": False}, None))

    assert starts == []


def test_compaction_status_publishes_a_persistent_semantic_state() -> None:
    plugin = _plugin()
    renderer = object()
    plugin.renderer = renderer

    with patch("plugins.context_compaction_plugin.publish_semantic") as publish:
        plugin._set_compaction_status(
            "paused",
            "Automatic compaction paused because the prompt remains above threshold.",
            action="Run /compact now.",
        )

    assert plugin._compaction_status == "paused"
    assert "above threshold" in plugin._compaction_status_message
    publish.assert_called_once_with(
        renderer,
        "context_compaction_status",
        status="paused",
        message="Automatic compaction paused because the prompt remains above threshold.",
        action="Run /compact now.",
    )


def test_failed_compaction_action_is_visible_in_compact_status() -> None:
    plugin = _plugin()
    plugin.config = SimpleNamespace(get=lambda key, default=None: default)
    plugin._fetch_display_history = lambda: _empty_history()
    plugin._fetch_display_tokens = lambda: _empty_tokens()
    plugin._profile_manager = None
    plugin._get_token_threshold = lambda: 200_000
    plugin._compaction_round = 1
    plugin._compaction_in_progress = False
    plugin._disabled_for_session = False
    plugin._compaction_status = "failed"
    plugin._compaction_status_message = "Compaction failed before shrinking context."
    plugin._compaction_status_action = "Run /compact now or start a smaller conversation."

    result = asyncio.run(
        plugin._handle_compact_command(SimpleNamespace(args=["status"]))
    )

    assert "state:          failed" in result
    assert "Run /compact now or start a smaller conversation." in result


def test_failed_compaction_status_displays_tui_next_step() -> None:
    plugin = _plugin()
    displayed = []
    coordinator = SimpleNamespace(display_message_sequence=displayed.extend)
    plugin.renderer = SimpleNamespace(
        pipe_mode=False,
        message_coordinator=coordinator,
    )

    with patch("plugins.context_compaction_plugin.publish_semantic") as publish:
        plugin._set_compaction_status(
            "failed",
            "The context-overflow compaction could not be started.",
            action="Run /compact now or reduce conversation history.",
        )

    assert displayed, "TUI should get a visible recovery message"
    assert plugin._compaction_status_action == (
        "Run /compact now or reduce conversation history."
    )
    role, content, metadata = displayed[0]
    assert role == "system"
    assert "The context-overflow compaction could not be started." in content
    assert "\nNext step: Run /compact now or reduce conversation history." in content
    assert metadata["display_type"] == "warning"
    publish.assert_called_once_with(
        plugin.renderer,
        "context_compaction_status",
        status="failed",
        message="The context-overflow compaction could not be started.",
        action="Run /compact now or reduce conversation history.",
    )


async def _empty_history():
    return []


async def _empty_tokens():
    return 0
