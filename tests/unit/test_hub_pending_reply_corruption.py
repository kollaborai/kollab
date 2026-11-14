import json
from pathlib import Path

import pytest

from plugins.hub.task_ledger import TaskLedger


@pytest.mark.parametrize(
    "payload",
    [
        "{not json",
        '{"expected_replies":{"unknown":"shape"}}',
        '{"expected_replies":[]} trailing writer data',
        '{"expected_replies":[{"task_id":"valid"},"unknown entry"]}',
    ],
)
@pytest.mark.parametrize(
    "operation",
    ["read", "expect", "resolve", "resolve_evidence", "expire", "terminalize"],
)
def test_unreadable_pending_reply_envelope_is_never_treated_as_empty(
    tmp_path, payload, operation
):
    ledger = TaskLedger(str(tmp_path))
    path = tmp_path / "_pending_replies.json"
    path.write_text(payload)

    with pytest.raises(ValueError, match="pending reply ledger.*repair"):
        if operation == "read":
            ledger._load_pending_replies()
        elif operation == "expect":
            ledger.expect_reply(
                task_id="new",
                assignee="lapis",
                requested_by="koordinator",
                message_id="new-message",
                deadline_seconds=120,
            )
        elif operation == "resolve":
            ledger.resolve_pending_reply("old-message", reason="resolved")
        elif operation == "resolve_evidence":
            ledger.resolve_reply(
                assignee="lapis", evidence="task complete", message_id="reply"
            )
        elif operation == "expire":
            ledger.pending_replies()
        else:
            ledger._resolve_replies_for_task(
                "old-task", terminal_status="obsolete", reason="retired"
            )

    assert path.read_text() == payload


def test_pending_reply_read_io_failure_is_reported_without_content(tmp_path, monkeypatch):
    ledger = TaskLedger(str(tmp_path))
    path = tmp_path / "_pending_replies.json"
    path.write_text('{"expected_replies":[]}')
    original_read_text = Path.read_text

    def fail_pending_read(file_path, *args, **kwargs):
        if file_path == path:
            raise OSError("private payload must not appear")
        return original_read_text(file_path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", fail_pending_read)

    with pytest.raises(ValueError, match="pending reply ledger.*repair") as error:
        ledger.pending_replies()

    assert "private payload" not in str(error.value)


def test_pending_reply_expiry_still_prunes_valid_entries(tmp_path, monkeypatch):
    ledger = TaskLedger(str(tmp_path))
    ledger.expect_reply(
        task_id="expired",
        assignee="lapis",
        requested_by="koordinator",
        message_id="expired-message",
        deadline_seconds=120,
    )
    path = ledger._pending_replies_path()
    envelope = json.loads(path.read_text())
    envelope["expected_replies"][0]["created_at"] = 0
    path.write_text(json.dumps(envelope))
    monkeypatch.setattr("plugins.hub.task_ledger.time.time", lambda: 200_000)

    assert ledger.pending_replies() == []
    assert json.loads(path.read_text()) == {"expected_replies": []}
