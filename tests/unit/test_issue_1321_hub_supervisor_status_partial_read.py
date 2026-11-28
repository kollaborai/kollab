from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from plugins.hub.plugin import HubPlugin
from plugins.hub.supervisor import SupervisorState
from plugins.hub.task_ledger import TaskLedger


def test_supervisor_status_warns_when_task_ledger_is_partial(tmp_path):
    ledger = TaskLedger(tasks_dir=str(tmp_path / "tasks"))
    ledger.enqueue(assigner="peridot", directive="resume deployment")
    Path(tmp_path / "tasks" / "broken.json").write_text("{")

    plugin = HubPlugin.__new__(HubPlugin)
    plugin._identity = SimpleNamespace(is_coordinator=True)
    plugin._task_ledger = ledger
    plugin._supervisor_task = MagicMock()
    plugin._supervisor_state = SupervisorState()
    plugin._supervisor_alerts = []
    plugin.config = None

    lines = plugin._supervisor_status_lines()

    assert lines[0] == "supervisor: 1 open, 0 stalled, 0 alert(s)"
    assert any("incomplete" in line.lower() for line in lines)
    assert any("1 task card could not be read" in line for line in lines)
