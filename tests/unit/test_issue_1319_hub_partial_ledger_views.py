from types import SimpleNamespace
from unittest.mock import MagicMock

from plugins.hub.plugin import HubPlugin
from plugins.hub.supervisor import SupervisorState
from plugins.hub.task_ledger import TaskLedger


def _plugin(tmp_path):
    plugin = object.__new__(HubPlugin)
    plugin._identity = SimpleNamespace(is_coordinator=True)
    plugin._task_ledger = TaskLedger(tasks_dir=str(tmp_path / "tasks"))
    plugin._supervisor_task = MagicMock()
    plugin._supervisor_state = SupervisorState()
    plugin._supervisor_alerts = []
    plugin.config = {}
    return plugin


def _add_malformed_sibling(ledger):
    ledger.enqueue(assigner="koordinator", directive="readable work")
    ledger._task_path("malformed").write_text("{not json", encoding="utf-8")


def test_status_marks_counts_partial_when_sibling_card_is_unreadable(tmp_path):
    plugin = _plugin(tmp_path)
    _add_malformed_sibling(plugin._task_ledger)

    lines = plugin._supervisor_status_lines()

    assert lines[0] == "supervisor: 1 open, 0 stalled, 0 alert(s) (partial; 1 unreadable card)"


def test_work_marks_count_partial_when_sibling_card_is_unreadable(tmp_path):
    plugin = _plugin(tmp_path)
    _add_malformed_sibling(plugin._task_ledger)

    text, complete = plugin._format_work_report()

    assert "open ledger cards: 1 (partial; 1 unreadable card)" in text
    assert complete is False


def test_status_and_work_remain_complete_when_cards_are_readable(tmp_path):
    plugin = _plugin(tmp_path)
    plugin._task_ledger.enqueue(assigner="koordinator", directive="readable work")

    assert "partial" not in plugin._supervisor_status_lines()[0]
    text, complete = plugin._format_work_report()
    assert "open ledger cards: 1" in text
    assert "partial" not in text
    assert complete is True
