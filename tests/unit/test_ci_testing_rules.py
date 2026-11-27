from __future__ import annotations

import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _workflow(path: str):
    return yaml.safe_load((ROOT / path).read_text())


def test_quarantine_marker_is_registered_and_issuer_case_is_marked() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    markers = config["tool"]["pytest"]["ini_options"]["markers"]
    assert any(marker.split(":", 1)[0] == "quarantine" for marker in markers)

    source = (ROOT / "tests/unit/test_enrollment_client.py").read_text()
    test_block = source.split(
        "async def test_issuer_requires_explicit_decision_after_proof", 1
    )[0].splitlines()[-12:]
    decorators = "\n".join(test_block)
    assert "@pytest.mark.quarantine" in decorators
    assert "@pytest.mark.skip" not in decorators
    assert "# Quarantined as flaky (#1391)" in decorators


def test_ci_gate_requires_repeat_job_but_excludes_nonblocking_quarantine() -> None:
    workflow = _workflow(".github/workflows/ci.yml")
    jobs = workflow["jobs"]
    required = jobs["required_tests"]
    assert "repeat_changed_tests" in required["needs"]
    assert "quarantine" not in required["needs"]

    repeat = jobs["repeat_changed_tests"]
    assert "pull_request" in repeat["if"]
    assert "pull_request.base.sha" in str(repeat)
    assert jobs["quarantine"]["continue-on-error"] is True


def test_every_blocking_pytest_invocation_excludes_quarantine() -> None:
    workflow = _workflow(".github/workflows/ci.yml")
    for job_id, job in workflow["jobs"].items():
        if job_id in {"quarantine", "required_tests"}:
            continue
        for step in job.get("steps", []):
            command = step.get("run", "")
            if "python -m pytest" in command:
                assert '-m "not quarantine"' in command, (job_id, step.get("name"))


def test_base_fresh_workflow_and_current_docs_are_retired() -> None:
    assert not (ROOT / ".github/workflows/base-fresh.yml").exists()
    operations = ROOT / "docs/operations"
    references = [
        path
        for path in operations.glob("*.md")
        if "base-fresh" in path.read_text().lower()
    ]
    assert references == []
