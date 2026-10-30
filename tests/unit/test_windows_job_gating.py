"""The Windows desktop job runs for every release, and a release needs its installer (#268)."""

from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parents[2] / ".github" / "workflows"


def _jobs():
    return yaml.safe_load((WORKFLOWS / "binaries.yml").read_text())["jobs"]


def test_windows_job_runs_for_every_release():
    job = _jobs()["desktop-windows"]
    assert "if" not in job
    assert "continue-on-error" not in job


def test_release_manifest_waits_on_windows_and_requires_its_installer():
    manifest = _jobs()["release-manifest"]
    assert "desktop-windows" in manifest["needs"]
    assert any(
        "verify dist/bin --windows required" in step.get("run", "")
        for step in manifest["steps"]
    )


def test_publish_requires_the_windows_installer():
    assert (
        "verify dist/bin --windows required" in (WORKFLOWS / "publish.yml").read_text()
    )
