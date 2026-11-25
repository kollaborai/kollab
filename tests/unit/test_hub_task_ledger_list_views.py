from pathlib import Path
from types import SimpleNamespace

from plugins.hub.plugin import HubPlugin
from plugins.hub.task_ledger import TaskLedger


def test_task_list_commands_warn_when_ledger_is_partial(tmp_path):
    ledger = TaskLedger(tasks_dir=str(tmp_path))
    card = ledger.create(
        assigner="koordinator",
        assignee="lapis",
        directive="resume the deployment",
    )
    Path(tmp_path, "broken.json").write_text("{")

    plugin = HubPlugin.__new__(HubPlugin)
    plugin._identity = SimpleNamespace(identity="lapis")
    plugin._task_ledger = ledger

    mine = plugin._tasks_mine()
    all_tasks = plugin._tasks_list()

    assert card.id in mine
    assert card.id in all_tasks
    assert "incomplete" in mine.lower()
    assert "incomplete" in all_tasks.lower()
