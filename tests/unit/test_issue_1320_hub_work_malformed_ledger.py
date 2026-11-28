"""Regression coverage for partial TaskLedger reads in /hub work (#1320)."""

import asyncio

from plugins.hub.plugin import HubPlugin
from plugins.hub.task_ledger import TaskLedger


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _plugin(tmp_path):
    plugin = object.__new__(HubPlugin)
    plugin._work_queue = None
    plugin._task_ledger = TaskLedger(tasks_dir=str(tmp_path / "tasks"))
    return plugin


def _add_malformed_card(ledger):
    ledger._task_path("malformed").write_text("{")


def test_hub_work_marks_readable_cards_partial_when_another_card_is_malformed(
    tmp_path,
):
    plugin = _plugin(tmp_path)
    plugin._task_ledger.enqueue(assigner="peridot", directive="readable task")
    _add_malformed_card(plugin._task_ledger)

    result = _run(plugin._handle_hub_work_tool({"id": "work-1320-mixed"}))

    assert not result.success
    assert "open ledger cards: 1" in result.output
    assert "readable task" in result.output
    assert "partial" in result.output.lower()
    assert "do not assume empty" in result.output.lower()


def test_hub_work_does_not_report_empty_when_only_card_is_malformed(tmp_path):
    plugin = _plugin(tmp_path)
    _add_malformed_card(plugin._task_ledger)

    result = _run(plugin._handle_hub_work_tool({"id": "work-1320-malformed"}))

    assert not result.success
    assert "work queue: empty" not in result.output.lower()
    assert "do not assume empty" in result.output.lower()
