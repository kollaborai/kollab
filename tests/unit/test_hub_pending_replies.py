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
        task_id="task-a", assignee="worker", requested_by="lead",
        message_id="msg-a", deadline_seconds=60,
    )
    ledger.expect_reply(
        task_id="task-b", assignee="worker", requested_by="lead",
        message_id="msg-b", deadline_seconds=60,
    )

    assert ledger.resolve_reply(
        assignee="worker", evidence="task complete", message_id="reply-b", task_id="task-b"
    )
    pending = ledger.pending_replies()
    assert [item["task_id"] for item in pending] == ["task-a"]


def test_production_reply_resolver_passes_inbound_task_id():
    """The live hub message path must disambiguate same-assignee replies."""
    from pathlib import Path

    source = Path("plugins/hub/plugin.py").read_text()
    call = source[source.index("resolve_reply(") : source.index("):", source.index("resolve_reply("))]
    assert 'task_id=str((message.metadata or {}).get("task_id") or "").strip()' in call
