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
