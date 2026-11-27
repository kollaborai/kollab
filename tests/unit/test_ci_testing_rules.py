from __future__ import annotations

import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _workflow(path: str):
    return yaml.safe_load((ROOT / path).read_text())


def _pytest_invocations(command: str) -> list[str]:
    normalized = re.sub(r"\\\r?\n\s*", " ", command)
    invocations = []
    for match in re.finditer(r"\bpython\s+-m\s+pytest\b", normalized):
        remainder = normalized[match.end() :]
        boundary = re.search(r";|(?<!\|)\|(?!\|)|\n", remainder)
        end = match.end() + boundary.start() if boundary else len(normalized)
        invocations.append(normalized[match.start() : end])
    return invocations


def _pull_request_workflows():
    for path in sorted((ROOT / ".github/workflows").glob("*.yml")):
        workflow = yaml.safe_load(path.read_text()) or {}
        triggers = workflow.get("on", workflow.get(True, {})) or {}
        if isinstance(triggers, dict) and "pull_request" in triggers:
            yield path, workflow


def _step(workflow, job_id: str, name: str):
    return next(step for step in workflow["jobs"][job_id]["steps"] if step.get("name") == name)


def test_quarantine_marker_is_registered_and_issuer_case_is_marked() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())
    markers = config["tool"]["pytest"]["ini_options"]["markers"]
    assert any(marker.split(":", 1)[0] == "quarantine" for marker in markers)

    source = (ROOT / "tests/unit/test_enrollment_client.py").read_text()
    test_block = source.split("async def test_issuer_requires_explicit_decision_after_proof", 1)[0].splitlines()[-12:]
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


def test_flake_catcher_runs_only_for_successful_retries_from_this_repository() -> None:
    job = _workflow(".github/workflows/flake-catcher.yml")["jobs"]["catch-flakes"]
    condition = job["if"]

    assert "github.event.workflow_run.head_repository.full_name == github.repository" in condition
    assert "github.event.workflow_run.conclusion == 'success'" in condition
    assert "github.event.workflow_run.run_attempt > 1" in condition


def test_every_blocking_pytest_invocation_excludes_quarantine() -> None:
    workflow = _workflow(".github/workflows/ci.yml")
    for job_id, job in workflow["jobs"].items():
        if job_id in {"quarantine", "required_tests"}:
            continue
        for step in job.get("steps", []):
            command = step.get("run", "")
            if "python -m pytest" in command:
                assert '-m "not quarantine"' in command, (job_id, step.get("name"))


def test_every_pull_request_workflow_excludes_quarantine_from_pytest_runs() -> None:
    violations = []
    windows_workflows = {"windows-smoke.yml", "windows-locks.yml"}
    for path, workflow in _pull_request_workflows():
        for job_id, job in workflow.get("jobs", {}).items():
            if job_id == "quarantine":
                continue
            for step in job.get("steps", []):
                command = step.get("run", "")
                for invocation in _pytest_invocations(command):
                    if "--collect-only" not in invocation and '-m "not quarantine"' not in invocation:
                        violations.append(f"{path.name}: {job_id}/{step.get('name')}: {invocation}")
                    if path.name in windows_workflows:
                        if step.get("shell") == "bash":
                            violations.append(f"{path.name}: {job_id}/{step.get('name')}: Bash overrides Windows shell")
                        lines = [line.strip() for line in command.splitlines()]
                        pytest_lines = [index for index, line in enumerate(lines) if "python -m pytest" in line]
                        if len(pytest_lines) != len(_pytest_invocations(command)) or any(
                            index + 1 >= len(lines) or lines[index + 1] != "if ($LASTEXITCODE -eq 5) { exit 0 }"
                            for index in pytest_lines
                        ):
                            violations.append(f"{path.name}: {job_id}/{step.get('name')}: exit-5 handler missing")

    assert not violations, "\n".join(violations)


def test_ci_shard_and_heavy_lane_collect_before_filtering_quarantine() -> None:
    workflow = _workflow(".github/workflows/ci.yml")
    lanes = (
        (
            "tests",
            "Run unit test shard",
            'if [ -z "$selected_slow_tests" ]; then',
            "Slow-file shard $SHARD discovered no test items",
        ),
        (
            "heavy_tests",
            "Run this lane's network membership tests",
            'if [ "${#targets[@]}" -eq 0 ]; then',
            "Heavy lane $HEAVY_LANE discovered no test items",
        ),
    )
    for job_id, step_name, empty_guard, guard in lanes:
        command = _step(workflow, job_id, step_name)["run"]
        invocations = _pytest_invocations(command)
        collection = [invocation for invocation in invocations if "--collect-only" in invocation]
        execution = [invocation for invocation in invocations if "--collect-only" not in invocation]

        assert len(collection) == 1, (job_id, invocations)
        assert '-m "not quarantine"' not in collection[0], (job_id, collection[0])
        assert len(execution) == 1, (job_id, invocations)
        assert '-m "not quarantine"' in execution[0], (job_id, execution[0])
        assert empty_guard in command
        assert guard in command
        assert '|| [ "$?" -eq 5 ]' in command


def test_every_blocking_quarantine_filtered_pytest_step_accepts_exit_five() -> None:
    jobs = _workflow(".github/workflows/ci.yml")["jobs"]
    filtered_steps = []
    for job_id, job in jobs.items():
        if job_id == "quarantine":
            continue
        for step in job.get("steps", []):
            command = step.get("run", "")
            if '-m "not quarantine"' in command and _pytest_invocations(command):
                filtered_steps.append((job_id, step.get("name"), command))

    assert filtered_steps
    for job_id, name, command in filtered_steps:
        assert '|| [ "$?" -eq 5 ]' in command, f"{job_id}/{name} does not accept pytest exit 5"


def _pytest_test_files(command: str) -> set[str]:
    files = set()
    for invocation in _pytest_invocations(command):
        files.update(re.findall(r"(?:tests|packages/[\w-]+/tests)/[\w./-]+\.py", invocation))
    return files


def test_windows_quarantine_steps_cover_the_same_files_as_windows_pytest_steps() -> None:
    cases = (
        ("windows-smoke.yml", "import-smoke"),
        ("windows-locks.yml", "lock-tests"),
    )
    for filename, job_id in cases:
        workflow = _workflow(f".github/workflows/{filename}")
        job = workflow["jobs"][job_id]
        steps = job["steps"]
        quarantine = [step for step in steps if step.get("name") == "Quarantined Windows tests (non-blocking)"]

        assert len(quarantine) == 1, filename
        quarantine_step = quarantine[0]
        assert quarantine_step["continue-on-error"] is True
        command = quarantine_step.get("run", "")
        assert "-m quarantine" in command
        assert "-q -rs -p no:cacheprovider --noconftest" in command
        assert "if ($LASTEXITCODE -eq 5) { exit 0 }" in command

        normal_files = set()
        for step in steps:
            if step is not quarantine_step:
                normal_files |= _pytest_test_files(step.get("run", ""))
        assert _pytest_test_files(command) == normal_files, filename


def test_python_repeat_rule_is_scoped_to_required_ci_test_suites() -> None:
    sentence = (
        "Run each new or changed Python test file in the suites the required ci/tests gate runs "
        "(tests/unit/, the engine, rpc and voice package suites) 20 times in its PR; one non-quarantined "
        "failure blocks the PR. Tests that run only in non-required workflows (desktop signing, "
        "Windows smoke) are outside this gate."
    )
    for name in ("CLAUDE.md", "AGENTS.md", "SOFTWARE_DEVELOPMENT_LIFECYCLE.md"):
        assert sentence in (ROOT / name).read_text(), name


def test_base_fresh_workflow_and_current_docs_are_retired() -> None:
    assert not (ROOT / ".github/workflows/base-fresh.yml").exists()
    operations = ROOT / "docs/operations"
    references = [path for path in operations.glob("*.md") if "base-fresh" in path.read_text().lower()]
    assert references == []


def _test_roots(command: str) -> set[str]:
    return set(re.findall(r"(?<![\w/.-])(packages/[\w-]+/tests|tests)(?=[/\s]|$)", command))


def test_every_suite_kept_out_of_blocking_runs_runs_in_the_quarantine_job() -> None:
    jobs = _workflow(".github/workflows/ci.yml")["jobs"]
    excluded: set[str] = set()
    for job_id, job in jobs.items():
        if job_id in {"quarantine", "required_tests"}:
            continue
        for step in job.get("steps", []):
            command = step.get("run", "")
            if '-m "not quarantine"' in command:
                excluded |= _test_roots(command)
    quarantined: set[str] = set()
    for step in jobs["quarantine"]["steps"]:
        command = step.get("run", "")
        if "-m quarantine" in command:
            quarantined |= _test_roots(command)

    assert excluded >= {"tests", "packages/kollabor-engine/tests", "packages/kollabor-voice/tests"}
    assert excluded - quarantined == set()


def test_every_blocking_suite_is_covered_by_the_repeat_job() -> None:
    from scripts.repeat_changed_tests import DEFAULT_SUITES

    jobs = _workflow(".github/workflows/ci.yml")["jobs"]
    blocking: set[str] = set()
    for job_id, job in jobs.items():
        if job_id in {"quarantine", "required_tests", "repeat_changed_tests"}:
            continue
        for step in job.get("steps", []):
            if '-m "not quarantine"' in (command := step.get("run", "")):
                blocking |= _test_roots(command)
    repeated = set(DEFAULT_SUITES)
    for step in jobs["repeat_changed_tests"]["steps"]:
        command = step.get("run", "")
        if "repeat_changed_tests.py" in command and "--suites" in command:
            repeated |= _test_roots(command.split("--suites", 1)[1])

    assert blocking - repeated == set()


def test_a_failing_quarantine_suite_does_not_skip_the_next_ones() -> None:
    steps = _workflow(".github/workflows/ci.yml")["jobs"]["quarantine"]["steps"]
    runs = [step for step in steps if "-m quarantine" in step.get("run", "")]

    assert len(runs) >= 3
    for step in runs[1:]:
        assert "!cancelled()" in str(step.get("if", "")), step["name"]
