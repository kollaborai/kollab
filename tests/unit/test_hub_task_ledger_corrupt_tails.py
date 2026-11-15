import json

import pytest

from plugins.hub.task_ledger import TaskLedger


def _record_dispatch(ledger):
    return ledger.record_dispatch(
        task_id="task-1",
        assigned_by="koordinator",
        assignee="lapis",
        directive="recover safely",
        message_id="message-1",
    )


@pytest.mark.parametrize(
    "tail",
    [b"unexpected suffix", b'{"dispatches": []}', b"{\"dispatches\": ["],
)
def test_dispatch_mutation_preserves_non_whitespace_corrupt_tail(tmp_path, tail):
    ledger = TaskLedger(str(tmp_path))
    path = tmp_path / "_dispatches.json"
    original = b'{"dispatches": []}' + tail
    path.write_bytes(original)

    with pytest.raises(RuntimeError, match="unreadable"):
        _record_dispatch(ledger)

    assert path.read_bytes() == original


def test_task_mutation_preserves_non_whitespace_corrupt_tail(tmp_path):
    ledger = TaskLedger(str(tmp_path))
    card = ledger.create(
        assigner="koordinator", assignee="lapis", directive="keep this card"
    )
    path = tmp_path / f"{card.id}.json"
    original = path.read_bytes() + b"\nrecovery candidate"
    path.write_bytes(original)

    changed = ledger._mutate_locked(card.id, lambda loaded: True)

    assert changed is None
    assert path.read_bytes() == original


def test_pending_reply_mutation_preserves_non_whitespace_corrupt_tail(tmp_path):
    ledger = TaskLedger(str(tmp_path))
    ledger.expect_reply(
        task_id="task-1",
        assignee="lapis",
        requested_by="koordinator",
        message_id="message-1",
        deadline_seconds=60,
    )
    path = tmp_path / "_pending_replies.json"
    original = path.read_bytes() + b"\nrecovery candidate"
    path.write_bytes(original)

    with pytest.raises(RuntimeError, match="unreadable"):
        ledger.resolve_pending_reply("message-1", reason="task completed")

    assert path.read_bytes() == original


def test_whitespace_only_json_tail_is_a_clean_mutable_file(tmp_path):
    ledger = TaskLedger(str(tmp_path))
    path = tmp_path / "_dispatches.json"
    path.write_bytes(b'{"dispatches": []}\n \t\r\n')

    result = _record_dispatch(ledger)

    assert result["message_id"] == "message-1"
    assert json.loads(path.read_text())["dispatches"][0]["message_id"] == "message-1"


def test_read_only_task_load_can_recover_prefix_without_mutating_source(tmp_path):
    ledger = TaskLedger(str(tmp_path))
    card = ledger.create(
        assigner="koordinator", assignee="lapis", directive="read the card"
    )
    path = tmp_path / f"{card.id}.json"
    original = path.read_bytes() + b"\nrecovery candidate"
    path.write_bytes(original)

    loaded = ledger.get(card.id)

    assert loaded is not None
    assert loaded.directive == "read the card"
    assert path.read_bytes() == original


@pytest.mark.parametrize("ledger_name", ["_dispatches.json", "_pending_replies.json"])
def test_mutation_io_error_does_not_replace_ledger(tmp_path, monkeypatch, ledger_name):
    ledger = TaskLedger(str(tmp_path))
    path = tmp_path / ledger_name
    original = b'{"dispatches": []}' if "dispatches" in ledger_name else b'{"expected_replies": []}'
    path.write_bytes(original)
    read_text = type(path).read_text

    def fail_target_read(self, *args, **kwargs):
        if self == path:
            raise OSError("synthetic read failure")
        return read_text(self, *args, **kwargs)

    monkeypatch.setattr(type(path), "read_text", fail_target_read)

    with pytest.raises((RuntimeError, OSError)):
        if "dispatches" in ledger_name:
            _record_dispatch(ledger)
        else:
            ledger.expect_reply(
                task_id="task-1",
                assignee="lapis",
                requested_by="koordinator",
                message_id="message-1",
                deadline_seconds=60,
            )

    assert path.read_bytes() == original
