from plugins.hub.task_ledger import TaskLedger


def test_expect_reply_replay_is_idempotent_and_preserves_resolution(tmp_path):
    ledger = TaskLedger(str(tmp_path))
    request = {
        "task_id": "task-stale-cron",
        "assignee": "lapis",
        "requested_by": "koordinator",
        "message_id": "msg-replayed",
        "deadline_seconds": 120,
    }

    ledger.expect_reply(**request)
    ledger.expect_reply(**request)
    assert len(ledger.pending_replies()) == 1

    assert ledger.resolve_pending_reply(
        "msg-replayed", reason="stale task directive acknowledged"
    )
    # A late replay must not create a fresh pending acknowledgement.
    ledger.expect_reply(**request)
    assert ledger.pending_replies() == []


def test_expect_reply_allows_distinct_message_ids_for_same_task(tmp_path):
    ledger = TaskLedger(str(tmp_path))
    common = {
        "task_id": "task-1",
        "assignee": "lapis",
        "requested_by": "koordinator",
        "deadline_seconds": 120,
    }

    ledger.expect_reply(message_id="msg-1", **common)
    ledger.expect_reply(message_id="msg-2", **common)

    assert {item["message_id"] for item in ledger.pending_replies()} == {
        "msg-1",
        "msg-2",
    }
