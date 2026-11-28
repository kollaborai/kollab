"""Unreadable active cards remain visible to supervision and compaction (#1323)."""

from types import SimpleNamespace

from plugins.context_compaction_plugin import ContextCompactionPlugin
from plugins.hub.supervisor import evaluate
from plugins.hub.task_ledger import TaskLedger


def _malformed_active_card(ledger, task_id="task1323"):
    ledger._task_path(task_id).write_text("[]", encoding="utf-8")


def test_supervisor_surfaces_unreadable_active_card_instead_of_skipping_handoff(
    tmp_path,
):
    ledger = TaskLedger(tasks_dir=str(tmp_path))
    _malformed_active_card(ledger)

    decisions, _state = evaluate(ledger, [], now=1000)

    unreadable = [d for d in decisions if d.kind == "alert_handoff_unreadable"]
    assert [d.card_id for d in unreadable] == ["task1323"]
    assert "could not read" in unreadable[0].text.lower()


def test_compaction_keeps_messages_for_unreadable_active_card(tmp_path):
    ledger = TaskLedger(tasks_dir=str(tmp_path))
    _malformed_active_card(ledger)
    hub = SimpleNamespace(
        _task_ledger=ledger,
        _identity=SimpleNamespace(identity="lapis"),
    )
    plugin = ContextCompactionPlugin.__new__(ContextCompactionPlugin)
    plugin.event_bus = SimpleNamespace(
        get_service=lambda name: hub if name == "hub_plugin" else None
    )

    assert plugin._get_live_task_ids() == {"task1323"}
