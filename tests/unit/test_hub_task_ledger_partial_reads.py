import asyncio
import json
import logging
import time
from pathlib import Path
from types import SimpleNamespace

from plugins.hub.plugin import HubPlugin
from plugins.hub.task_ledger import TaskLedger


def _context_plugin(ledger: TaskLedger) -> HubPlugin:
    plugin = HubPlugin.__new__(HubPlugin)
    plugin._identity = SimpleNamespace(identity="lapis")
    plugin._roster = []
    plugin._task_ledger = ledger
    plugin.config = None
    plugin._change_feed = None

    async def no_remote_rows():
        return []

    plugin._refresh_remote_agent_rows = no_remote_rows
    return plugin


def test_incomplete_task_ledger_preserves_due_cards_and_warns_in_context(
    tmp_path, caplog
):
    ledger = TaskLedger(tasks_dir=str(tmp_path))
    card = ledger.create(
        assigner="koordinator",
        assignee="lapis",
        directive="resume the deployment",
        cron_interval=1,
    )
    payload = card.to_dict()
    payload["updated_at"] = time.time() - 60
    ledger._task_path(card.id).write_text(json.dumps(payload))
    Path(tmp_path, "broken.json").write_text("{")

    with caplog.at_level(logging.WARNING, logger="plugins.hub.task_ledger"):
        due_cards = ledger.get_cron_due()
        context = asyncio.run(_context_plugin(ledger)._hub_live_context())

    assert [due.id for due in due_cards] == [card.id]
    assert any(
        record.levelno >= logging.WARNING and "broken" in record.getMessage()
        for record in caplog.records
    )
    assert f"task {card.id} from koordinator" in context
    assert "incomplete" in context.lower()
    assert "could not be read" in context.lower()
