import asyncio
import json
from collections import OrderedDict
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.hub import task_ledger as task_ledger_module
from plugins.hub.plugin import HubPlugin
from plugins.hub.task_ledger import TaskLedger


def test_task_assignment_creates_expected_reply(tmp_path):
    ledger = TaskLedger(str(tmp_path))

    ledger.expect_reply(
        task_id="review-agent-hud",
        assignee="lapis",
        requested_by="koordinator",
        message_id="msg-1",
        deadline_seconds=120,
    )

    pending = ledger.pending_replies()

    assert len(pending) == 1
    assert pending[0]["task_id"] == "review-agent-hud"
    assert pending[0]["assignee"] == "lapis"


@pytest.mark.parametrize(
    "created_at",
    ["bad", float("nan"), float("inf"), float("-inf"), True],
    ids=["string", "nan", "positive-infinity", "negative-infinity", "boolean"],
)
def test_pending_replies_prunes_invalid_timestamps(tmp_path, monkeypatch, created_at):
    now = 100.0
    monkeypatch.setattr(
        task_ledger_module, "time", SimpleNamespace(time=lambda: now)
    )
    ledger = TaskLedger(str(tmp_path))
    path = tmp_path / "_pending_replies.json"
    path.write_text(
        json.dumps(
            {
                "expected_replies": [
                    {
                        "task_id": "bad-timestamp",
                        "message_id": "bad",
                        "status": "pending",
                        "created_at": created_at,
                    },
                    {
                        "task_id": "valid-neighbor",
                        "message_id": "valid",
                        "status": "pending",
                        "created_at": now,
                    },
                ]
            },
            allow_nan=True,
        ),
        encoding="utf-8",
    )

    pending = ledger.pending_replies()

    assert [item["message_id"] for item in pending] == ["valid"]
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert [
        item["message_id"] for item in persisted["expected_replies"]
    ] == ["valid"]


def test_pending_reply_reader_keeps_only_allowlisted_fields(tmp_path, monkeypatch):
    now = 100.0
    monkeypatch.setattr(
        task_ledger_module, "time", SimpleNamespace(time=lambda: now)
    )
    ledger = TaskLedger(str(tmp_path))
    (tmp_path / "_pending_replies.json").write_text(
        json.dumps(
            [
                {
                    "task_id": "legacy-list-entry",
                    "message_id": "msg-1",
                    "status": "pending",
                    "created_at": now,
                    "unexpected": "ignored",
                }
            ]
        ),
        encoding="utf-8",
    )

    pending = ledger.pending_replies()

    assert pending == [
        {
            "task_id": "legacy-list-entry",
            "message_id": "msg-1",
            "status": "pending",
            "created_at": now,
        }
    ]


def test_task_loader_recovers_valid_prefix_after_old_corruption(tmp_path):
    ledger = TaskLedger(str(tmp_path))
    card = ledger.create(
        assigner="koordinator",
        assignee="lapis",
        directive="recover this card",
    )
    path = tmp_path / f"{card.id}.json"
    path.write_text(path.read_text() + "\\nextra writer suffix")

    recovered = ledger.get(card.id)

    assert recovered is not None
    assert recovered.id == card.id
    assert recovered.directive == "recover this card"


def test_concurrent_pending_reply_writers_keep_both_entries(tmp_path):
    first = TaskLedger(str(tmp_path))
    second = TaskLedger(str(tmp_path))

    first.expect_reply(
        task_id="task-1",
        assignee="lapis",
        requested_by="koordinator",
        message_id="msg-1",
        deadline_seconds=120,
    )
    second.expect_reply(
        task_id="task-2",
        assignee="sapphire",
        requested_by="koordinator",
        message_id="msg-2",
        deadline_seconds=120,
    )

    assert {item["task_id"] for item in first.pending_replies()} == {
        "task-1",
        "task-2",
    }


def test_completion_report_resolves_expected_reply(tmp_path):
    ledger = TaskLedger(str(tmp_path))
    ledger.expect_reply(
        task_id="review-agent-hud",
        assignee="sapphire",
        requested_by="koordinator",
        message_id="msg-1",
        deadline_seconds=120,
    )

    resolved = ledger.resolve_reply(
        assignee="sapphire",
        evidence="VERDICT: ship-ready, no blockers",
        message_id="msg-2",
    )

    assert resolved is True
    assert ledger.pending_replies() == []


def test_legacy_message_id_resolution_closes_one_expected_reply(tmp_path):
    ledger = TaskLedger(str(tmp_path))
    ledger.expect_reply(
        task_id="legacy-message-id",
        assignee="sapphire",
        requested_by="koordinator",
        message_id="legacy-message-id",
        deadline_seconds=120,
    )

    resolved = ledger.resolve_pending_reply(
        "legacy-message-id",
        reason="retired with the stopped harness swarm",
    )

    assert resolved is True
    assert ledger.pending_replies() == []


def test_ack_does_not_resolve_expected_reply(tmp_path):
    ledger = TaskLedger(str(tmp_path))
    ledger.expect_reply(
        task_id="review-agent-hud",
        assignee="sapphire",
        requested_by="koordinator",
        message_id="msg-1",
        deadline_seconds=120,
    )

    resolved = ledger.resolve_reply(
        assignee="sapphire",
        evidence="standing by",
        message_id="msg-2",
    )

    assert resolved is False
    assert len(ledger.pending_replies()) == 1


def test_terminalize_obsolete_audits_and_resolves_task_reply(tmp_path):
    ledger = TaskLedger(str(tmp_path))
    card = ledger.create(
        assigner="koordinator",
        assignee="sapphire",
        directive="review the stale harness task",
    )
    ledger.expect_reply(
        task_id=card.id,
        assignee="sapphire",
        requested_by="koordinator",
        message_id="msg-1",
        deadline_seconds=120,
    )

    terminal = ledger.terminalize(
        card.id,
        status="obsolete",
        reason="superseded by the consolidated harness task",
        actor="koordinator",
        message_id="msg-2",
    )

    assert terminal.status == "obsolete"
    assert terminal.cron_active is False
    assert terminal.terminal_reason == "superseded by the consolidated harness task"
    assert terminal.terminal_actor == "koordinator"
    assert terminal.terminal_message_id == "msg-2"
    assert terminal.checkpoints[-1]["data"]["status"] == "obsolete"
    assert ledger.pending_replies() == []

    duplicate = ledger.terminalize(
        card.id,
        status="obsolete",
        reason="a duplicate stale directive",
        actor="sapphire",
    )
    assert duplicate.status == "obsolete"
    assert duplicate.terminal_reason == terminal.terminal_reason
    assert len(duplicate.checkpoints) == len(terminal.checkpoints)


def test_hub_status_includes_cockpit_counts(tmp_path):
    ledger = TaskLedger(str(tmp_path))
    ledger.expect_reply(
        task_id="task-1",
        assignee="lapis",
        requested_by="koordinator",
        message_id="msg-1",
        deadline_seconds=120,
    )

    plugin = HubPlugin.__new__(HubPlugin)
    plugin._identity = None
    plugin._task_ledger = ledger
    plugin._work_queue = None

    status = plugin._format_status()

    assert "pending replies: 1" in status
    assert "delivery trace:" in status


def test_resolve_reply_can_target_specific_task(tmp_path):
    ledger = TaskLedger(str(tmp_path))
    ledger.expect_reply(
        task_id="task-a",
        assignee="worker",
        requested_by="lead",
        message_id="msg-a",
        deadline_seconds=60,
    )
    ledger.expect_reply(
        task_id="task-b",
        assignee="worker",
        requested_by="lead",
        message_id="msg-b",
        deadline_seconds=60,
    )

    assert ledger.resolve_reply(
        assignee="worker",
        evidence="task complete",
        message_id="reply-b",
        task_id="task-b",
    )
    pending = ledger.pending_replies()
    assert [item["task_id"] for item in pending] == ["task-a"]


@pytest.mark.asyncio
async def test_inbound_message_resolves_matching_task_only(tmp_path):
    from plugins.hub.models import HubMessage, MessageScope
    from plugins.hub.plugin import HubPlugin, HubWakeDecision

    ledger = TaskLedger(str(tmp_path))
    for task_id in ("task-a", "task-b"):
        ledger.expect_reply(
            task_id=task_id,
            assignee="worker",
            requested_by="lead",
            message_id=f"msg-{task_id}",
            deadline_seconds=60,
        )

    plugin = HubPlugin.__new__(HubPlugin)
    plugin._task_ledger = ledger
    plugin._handle_stale_task_cron = AsyncMock(return_value=False)
    plugin._decide_hub_wake = lambda *_args, **_kwargs: HubWakeDecision(
        "wake", False, "test"
    )
    plugin._seen_messages = OrderedDict()
    plugin._presence = MagicMock()
    plugin._identity = MagicMock(identity="lead", agent_id="lead-id")
    plugin._vault = None

    llm = SimpleNamespace(
        conversation_history=[],
        is_processing=False,
        conversation_logger=None,
        current_parent_uuid=None,
    )

    class FakeEventBus:
        def get_service(self, name):
            return llm if name == "llm_service" else None

        async def emit_with_hooks(self, *args, **kwargs):
            return None

    plugin.event_bus = FakeEventBus()
    plugin._history_lock = asyncio.Lock()
    plugin._seen_content_hashes = {}
    plugin._pending_hub_wake_ids = OrderedDict()
    plugin._display_hub_message = MagicMock()
    plugin._bridge = None
    plugin._change_feed = None
    plugin._route_message = AsyncMock(return_value=[])
    plugin._hub_buffer_retry_queued = False

    await plugin._on_message_received(
        HubMessage(
            action="message",
            from_agent="worker-id",
            from_identity="worker",
            to="*",
            content="task complete",
            scope=MessageScope.BROADCAST.value,
            metadata={"task_id": "task-b"},
        )
    )

    assert [item["task_id"] for item in ledger.pending_replies()] == ["task-a"]


def test_preserve_active_terminalize_resolves_pending_replies(tmp_path):
    ledger = TaskLedger(str(tmp_path))
    card = ledger.create(
        assigner="koordinator",
        assignee="lapis",
        directive="work linked to a stale upstream reference",
        status="open",
    )
    ledger.expect_reply(
        task_id=card.id,
        assignee="lapis",
        requested_by="koordinator",
        message_id="msg-preserve-active-terminalize",
        deadline_seconds=120,
    )

    terminal = ledger.terminalize(
        card.id,
        status="cancelled",
        reason="the linked queue slot became stale",
        preserve_active=True,
    )

    assert terminal.status == "cancelled"
    assert ledger.pending_replies() == []
