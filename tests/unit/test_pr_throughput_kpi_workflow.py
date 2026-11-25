import importlib.util
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".github" / "scripts" / "pr_throughput_kpi.py"
CONFIG = ROOT / ".github" / "pr-throughput-kpi.json"
WORKFLOW = ROOT / ".github" / "workflows" / "pr-throughput-kpi.yml"


def _load_script():
    spec = importlib.util.spec_from_file_location("pr_throughput_kpi", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_workflow_is_weekly_manual_and_read_only():
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    triggers = workflow["on"]

    assert triggers["schedule"] == [{"cron": "17 14 * * 1"}]
    assert "workflow_dispatch" in triggers
    assert workflow["permissions"] == {
        "contents": "read",
        "pull-requests": "read",
        "issues": "read",
        "actions": "read",
    }


def test_config_loads_through_kpi_script():
    config = _load_script().load_config(CONFIG)

    assert config.enabled is True
    assert config.repo == "kollaborai/kollab"
    assert config.target_per_hour == 12
    assert config.windows == (1, 24, 168)
