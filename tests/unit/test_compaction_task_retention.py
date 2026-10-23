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
    MAX_EMERGENCY_SUMMARY_MESSAGE_CHARS,
    MAX_PRESERVED_HUB_TASK_CHARS,
    MAX_PRESERVED_HUB_TASK_MESSAGES,
    SUMMARY_INJECTION_PREFIX,
    SUMMARY_INJECTION_SUFFIX,
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
    qa_review = SimpleNamespace(id="qa-review", assignee="sapphire", status="qa_review")
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

    assert plugin._get_live_task_ids() == {
        "active",
        "paused",
        "qa-review",
        "open-sapphire",
    }


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
    plugin._get_conversation_history = lambda: [
        Message(content="old"),
        Message(content="new"),
    ]
    plugin._keep_recent_count = lambda: 1
    plugin._find_split_point = lambda history, keep_recent: 2
    starts = []
    statuses = []
    plugin._start_compaction = lambda reason="threshold": starts.append(reason) or True
    plugin._set_compaction_status = lambda status, message, action="": statuses.append(
        status
    )

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
    plugin._compaction_status_action = (
        "Run /compact now or start a smaller conversation."
    )

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


def test_persistent_status_is_reannounced_on_next_turn() -> None:
    plugin = _plugin()
    plugin.renderer = object()
    plugin._pending_compaction = None

    with patch("plugins.context_compaction_plugin.publish_semantic") as publish:
        # idle stays silent
        asyncio.run(plugin._apply_pending_compaction({}, None))
        publish.assert_not_called()

        plugin._compaction_status = "failed"
        plugin._compaction_status_message = (
            "The summarizer returned too little content."
        )
        plugin._compaction_status_action = "Run /compact now."
        asyncio.run(plugin._apply_pending_compaction({}, None))

    publish.assert_called_once_with(
        plugin.renderer,
        "context_compaction_status",
        status="failed",
        message="The summarizer returned too little content.",
        action="Run /compact now.",
    )


def test_150_live_tasks_stay_bounded_across_repeated_compaction_rounds() -> None:
    """The 24 x 4K live-task budget must stay below the 272K prompt cap."""
    plugin = _plugin()
    system = Message(role="system", content="system instructions")
    history = [system, Message(role="user", content="initial request")]
    summary = "summary of earlier work " * 300
    rounds = 3

    for round_number in range(rounds):
        round_tasks = [
            _task_message(
                f"round-{round_number}-task-{task_number}",
                f"task detail {task_number} " + ("x" * 5_000),
            )
            for task_number in range(180)
        ]
        history.extend(round_tasks)
        live_ids = {
            message.metadata["task_id"]
            for message in history
            if message.metadata.get("task_id")
        }

        preserved, _summarizable = plugin._extract_preservable_messages(
            history, live_task_ids=live_ids
        )
        assert len(preserved) == MAX_PRESERVED_HUB_TASK_MESSAGES
        assert all(
            len(message.content) <= MAX_PRESERVED_HUB_TASK_CHARS
            for message in preserved
        )
        assert [message.metadata["task_id"] for message in preserved] == [
            f"round-{round_number}-task-{task_number}"
            for task_number in range(180 - MAX_PRESERVED_HUB_TASK_MESSAGES, 180)
        ]

        history = plugin._build_compacted_history(
            system_msg=system,
            summary_text=summary,
            to_keep=[],
            preserved_tasks=preserved,
            compaction_round=round_number + 1,
            pre_message_count=len(history),
        )
        assert (
            sum(bool(message.metadata.get("task_id")) for message in history)
            <= MAX_PRESERVED_HUB_TASK_MESSAGES
        )
        assert (
            sum(
                bool(message.metadata.get("compaction_task_reminder"))
                for message in history
            )
            == 1
        )
        assert plugin._estimate_history_tokens(history) < 272_000


def test_run_compaction_preserves_bounded_active_taskledger_messages() -> None:
    """Exercise live TaskLedger retention through the real compaction path."""
    from unittest.mock import AsyncMock

    task_ids = [f"live-task-{index}" for index in range(30)]
    cards = [
        SimpleNamespace(id=task_id, status="active", assignee="peridot")
        for task_id in task_ids
    ]

    class Ledger:
        def get_active_for(self, identity):
            assert identity == "peridot"
            return cards

        def get_all(self, status=None):
            return []

        def get_open(self):
            return []

    hub_plugin = SimpleNamespace(
        _task_ledger=Ledger(),
        _identity=SimpleNamespace(identity="peridot"),
    )
    event_bus = SimpleNamespace(
        get_service=lambda name: hub_plugin if name == "hub_plugin" else None
    )
    config_values = {
        "plugins.context_compaction.keep_recent": 2,
        "plugins.context_compaction.max_summary_tokens": 2000,
        "plugins.context_compaction.log_compaction_events": False,
    }
    config = SimpleNamespace(
        get=lambda key, default=None: config_values.get(key, default)
    )
    plugin = ContextCompactionPlugin(
        "context_compaction", event_bus, SimpleNamespace(), config
    )
    history = [Message(role="system", content="Synthetic system prompt")]
    history.extend(
        _task_message(task_id, "assignment body " + "x" * 6_000) for task_id in task_ids
    )
    history.extend(
        [
            Message(role="user", content="Recent user turn"),
            Message(role="assistant", content="Recent assistant turn"),
        ]
    )
    plugin._llm_service = SimpleNamespace(conversation_history=history)
    plugin._call_summarization_llm = AsyncMock(
        return_value="Synthetic summary of the remaining task history. " * 5
    )
    plugin._set_compaction_status = lambda *_args, **_kwargs: None
    plugin._log_compaction_event = AsyncMock()

    with patch("plugins.context_compaction_plugin.logger.info") as info:
        asyncio.run(plugin._run_compaction())

    compacted = plugin._pending_compaction
    assert compacted is not None
    preserved = [
        message
        for message in compacted
        if message.metadata.get("context_compaction_task_excerpt")
    ]
    assert len(preserved) == MAX_PRESERVED_HUB_TASK_MESSAGES
    assert [message.metadata["task_id"] for message in preserved] == task_ids[-24:]
    assert all(
        len(message.content) <= MAX_PRESERVED_HUB_TASK_CHARS for message in preserved
    )
    assert plugin._estimate_history_tokens(compacted) < 272_000
    assert plugin._call_summarization_llm.await_count == 1
    assert plugin._log_compaction_event.await_args.kwargs["tasks_preserved"] == 24
    assert any(
        call.args[0].startswith("Task extraction: preserved ")
        and call.args[1] == MAX_PRESERVED_HUB_TASK_MESSAGES
        for call in info.call_args_list
    )


def test_emergency_summary_prioritizes_request_and_filters_stale_tasks() -> None:
    plugin = _plugin()
    filler_tasks = [
        _task_message(f"live-{index:02}", f"active task {index} " + ("x" * 6_000))
        for index in range(22)
    ]
    superseded = _task_message("live-22", "SUPERSEDED_TASK_CANARY " + ("s" * 6_000))
    current_version = _task_message(
        "live-22", "CURRENT_TASK_VERSION_CANARY " + ("c" * 6_000)
    )
    closed = _task_message("closed-task", "CLOSED_TASK_CANARY " + ("d" * 6_000))
    old_summary = Message(
        role="user",
        content="OLD_COMPACTION_SUMMARY_CANARY",
        metadata={"context_compaction": True},
    )
    old_reminder = Message(
        role="user",
        content="OLD_TASK_REMINDER_CANARY",
        metadata={"compaction_task_reminder": True},
    )
    request_marker = "LATEST_USER_REQUEST_CANARY"
    latest_request = Message(
        role="user",
        content=request_marker + " q" * 1_500 + " REQUEST_END_CANARY",
    )
    calls = [
        {"id": f"call-{index}", "function": {"name": "read", "arguments": "{}"}}
        for index in range(5)
    ]
    assistant = Message(role="assistant", content="", metadata={"tool_calls": calls})
    tool_results = [
        Message(role="tool", content="result", metadata={"tool_call_id": call["id"]})
        for call in calls
    ]
    history = [
        Message(role="system", content="system prompt"),
        *filler_tasks,
        superseded,
        current_version,
        closed,
        old_summary,
        old_reminder,
        latest_request,
        assistant,
        *tool_results,
    ]

    split_point = plugin._find_split_point(history, keep_recent=4)
    assert history[split_point:] == [assistant, *tool_results]
    recovery_text = plugin._build_emergency_summary(history[1:split_point])

    assert request_marker in recovery_text
    assert "REQUEST_END_CANARY" in recovery_text
    assert "SUPERSEDED_TASK_CANARY" not in recovery_text
    assert "CLOSED_TASK_CANARY" not in recovery_text
    assert "OLD_COMPACTION_SUMMARY_CANARY" not in recovery_text
    assert "OLD_TASK_REMINDER_CANARY" not in recovery_text

    live_ids = {f"live-{index:02}" for index in range(22)} | {"live-22"}
    preserved, _ = plugin._extract_preservable_messages(
        history, live_task_ids=live_ids
    )
    current_excerpt = next(
        message for message in preserved if message.metadata["task_id"] == "live-22"
    )
    assert "CURRENT_TASK_VERSION_CANARY" in current_excerpt.content
    assert "SUPERSEDED_TASK_CANARY" not in current_excerpt.content

    compacted = plugin._build_compacted_history(
        system_msg=history[0],
        summary_text=recovery_text,
        to_keep=history[split_point:],
        preserved_tasks=preserved,
        compaction_round=1,
        pre_message_count=len(history),
    )
    summary_message = next(
        message for message in compacted if message.metadata.get("context_compaction")
    )
    assert request_marker in summary_message.content
    assert "CLOSED_TASK_CANARY" not in summary_message.content
    assert len(summary_message.content) <= MAX_EMERGENCY_SUMMARY_MESSAGE_CHARS
    tool_call_message = next(
        message for message in compacted if message.metadata.get("tool_calls")
    )
    assert len(tool_call_message.metadata["tool_calls"]) == 5
    assert sum(message.role == "tool" for message in compacted) == 5


def test_emergency_summary_bound_includes_injection_wrapper() -> None:
    plugin = _plugin()
    messages = [
        Message(role="user", content=f"turn-{index} " + ("x" * 2_400))
        for index in range(12)
    ]

    recovery_text = plugin._build_emergency_summary(messages)
    injected_message = (
        SUMMARY_INJECTION_PREFIX + recovery_text + SUMMARY_INJECTION_SUFFIX
    )

    assert "turn-11" in recovery_text
    assert len(injected_message) <= MAX_EMERGENCY_SUMMARY_MESSAGE_CHARS
