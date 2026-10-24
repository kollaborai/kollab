"""The provisional Windows desktop job never runs for, or holds up, a release (#268)."""

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "binaries.yml"


def _jobs():
    return yaml.safe_load(WORKFLOW.read_text())["jobs"]


def test_windows_job_runs_only_by_hand():
    assert _jobs()["desktop-windows"]["if"] == "github.event_name == 'workflow_dispatch'"


def test_release_manifest_does_not_wait_on_windows():
    assert "desktop-windows" not in _jobs()["release-manifest"]["needs"]
