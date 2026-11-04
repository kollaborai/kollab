"""The Windows desktop job runs for every release, and a release needs its installer (#268)."""

from pathlib import Path

import pytest
import yaml

from scripts import release_publish
from scripts.desktop_release import manifest, write_checksum

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


def test_publish_requires_the_windows_installer(tmp_path, monkeypatch):
    for name in manifest("0.19.0"):
        if not name.endswith(".sha256"):
            asset = tmp_path / name
            asset.write_bytes(b"asset")
            write_checksum(asset)
    monkeypatch.setattr(
        release_publish, "request_json",
        lambda *args, **kwargs: pytest.fail("network reached without Windows installer"),
    )
    with pytest.raises(ValueError, match="manifest mismatch"):
        release_publish.publish_github(tmp_path, "v0.19.0", "a" * 40, "b" * 40)
