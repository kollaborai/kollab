"""Task ledger read failures remain visible to callers (#912)."""

import json

import pytest

from plugins.hub.plugin import HubPlugin
from plugins.hub.supervisor import evaluate
from plugins.hub.task_ledger import TaskLedger
from plugins.hub.vault import AgentVault


def test_get_open_reports_a_corrupt_card_without_losing_valid_cards(tmp_path):
    ledger = TaskLedger(str(tmp_path / "tasks"))
    valid = ledger.enqueue("coordinator", "valid work")
    (tmp_path / "tasks" / "corrupt.json").write_text("{")

    with pytest.raises(OSError) as error:
        ledger.get_open()

    assert error.value.failed_count == 1
    assert [card.id for card in error.value.partial_cards] == [valid.id]
    assert "{" not in str(error.value)
    assert str(tmp_path) not in str(error.value)


def test_get_open_reports_unreadable_card_and_preserves_empty_ledger(tmp_path, monkeypatch):
    tasks = tmp_path / "tasks"
    ledger = TaskLedger(str(tasks))
    assert ledger.get_open() == []

    card_path = tasks / "unreadable.json"
    card_path.write_text(json.dumps({"id": "unreadable", "status": "open"}))
    original_read_text = type(card_path).read_text

    def fail_card_read(path, *args, **kwargs):
        if path.name == card_path.name:
            raise PermissionError("private path and contents must not escape")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(type(card_path), "read_text", fail_card_read)

    with pytest.raises(OSError) as error:
        ledger.get_open()

    assert error.value.failed_count == 1
    assert error.value.partial_cards == []
    assert "private path" not in str(error.value)


def test_get_all_reports_task_directory_enumeration_failure(tmp_path, monkeypatch):
    ledger = TaskLedger(str(tmp_path / "tasks"))

    def fail_enumeration(_path):
        raise PermissionError("private directory path")

    monkeypatch.setattr("plugins.hub.task_ledger.os.scandir", fail_enumeration)

    with pytest.raises(OSError) as error:
        ledger.get_all()

    assert error.value.failed_count == 1
    assert error.value.partial_cards == []
    assert "private directory" not in str(error.value)


def test_rebirth_and_task_list_do_not_report_empty_when_a_card_is_corrupt(tmp_path):
    ledger = TaskLedger(str(tmp_path / "tasks"))
    valid = ledger.create("coordinator", "koordinator", "known assignment")
    (tmp_path / "tasks" / "corrupt.json").write_text("{")
    vault = AgentVault.__new__(AgentVault)
    vault.identity = "koordinator"
    lines = []

    vault._append_ledger_section(lines, ledger)
    plugin = HubPlugin.__new__(HubPlugin)
    plugin._task_ledger = ledger

    assert any("unavailable" in line for line in lines)
    assert "none" not in "\n".join(lines)
    assert valid.id in "\n".join(lines)
    assert "incomplete" in plugin._tasks_list().lower()
    assert "no tasks" not in plugin._tasks_list().lower()


def test_supervisor_surfaces_incomplete_ledger_without_mutating_known_cards(tmp_path):
    ledger = TaskLedger(str(tmp_path / "tasks"))
    valid = ledger.enqueue("coordinator", "known work")
    (tmp_path / "tasks" / "corrupt.json").write_text("{")
    before = (tmp_path / "tasks" / f"{valid.id}.json").read_bytes()

    decisions, _ = evaluate(
        ledger,
        [{"identity": "koordinator", "is_coordinator": True, "state": "idle"}],
        now=1000,
    )

    after = (tmp_path / "tasks" / f"{valid.id}.json").read_bytes()
    assert [decision.kind for decision in decisions] == ["ledger_read_failed"]
    assert "recovery" in decisions[0].text.lower()
    assert after == before


def test_work_status_does_not_call_an_incomplete_ledger_empty(tmp_path):
    ledger = TaskLedger(str(tmp_path / "tasks"))
    valid = ledger.enqueue("coordinator", "known open work")
    (tmp_path / "tasks" / "corrupt.json").write_text("{")
    plugin = HubPlugin.__new__(HubPlugin)
    plugin._task_ledger = ledger
    plugin._work_queue = None

    status = plugin._format_work()

    assert "incomplete" in status.lower()
    assert "empty" not in status.lower()
    assert valid.id in status


def test_claim_does_not_treat_a_corrupt_card_as_missing_or_overwrite_it(tmp_path):
    ledger = TaskLedger(str(tmp_path / "tasks"))
    path = tmp_path / "tasks" / "corrupt.json"
    original = b"{"
    path.write_bytes(original)

    with pytest.raises(OSError, match="Operator recovery"):
        ledger.claim("corrupt", "worker")

    assert path.read_bytes() == original
