from __future__ import annotations

import json
from pathlib import Path

import yaml

from scripts import flake_catcher as catcher


def test_parse_failed_lines_extracts_and_deduplicates_pytest_node_ids() -> None:
    logs = """2026-11-27T00:00:00Z Run pytest
2026-11-27T00:00:01Z FAILED tests/unit/test_alpha.py::test_one - AssertionError
FAILED tests/unit/test_beta.py::TestBeta::test_two[param 1] - RuntimeError
FAILED tests/unit/test_alpha.py::test_one - AssertionError
ERROR tests/unit/test_gamma.py::test_three - collection error
"""

    assert catcher.parse_failed_lines(logs) == [
        "tests/unit/test_alpha.py::test_one",
        "tests/unit/test_beta.py::TestBeta::test_two[param 1]",
    ]


def test_issue_title_and_body_include_test_run_url_and_attempt() -> None:
    nodeid = "tests/unit/test_alpha.py::TestAlpha::test_one[param]"

    assert catcher.build_issue_title(nodeid) == f"flaky test: {nodeid}"
    body = catcher.build_issue_body(nodeid, "https://example.test/runs/7", 2)
    assert nodeid in body
    assert "https://example.test/runs/7" in body
    assert "attempt 2" in body


def test_issue_decision_creates_then_updates_once_per_test() -> None:
    nodeid = "tests/unit/test_alpha.py::test_one"
    title = catcher.build_issue_title(nodeid)
    first = catcher.build_issue_body(nodeid, "https://example.test/runs/7", 1)

    action, issue, body = catcher.decide_issue_action([], title, first)
    assert (action, issue, body) == ("create", None, first)

    existing = {"number": 12, "title": title, "state": "open", "body": body}
    second = catcher.build_issue_body(nodeid, "https://example.test/runs/8", 2)
    action, issue, updated = catcher.decide_issue_action([existing], title, second)
    assert action == "update"
    assert issue == existing
    assert "runs/7" in updated and "runs/8" in updated

    updated_issue = {**existing, "body": updated}
    duplicate = catcher.decide_issue_action([updated_issue], title, second)
    assert duplicate == ("noop", updated_issue, updated)


class FakeGh:
    def __init__(self) -> None:
        self.created: list[dict[str, str]] = []
        self.updated: list[tuple[str, dict[str, str]]] = []

    def __call__(self, args: list[str]) -> str:
        assert args[0] == "api"
        path = args[1]
        if path == "repos/owner/repo/actions/runs/7":
            return json.dumps(
                {
                    "html_url": "https://example.test/runs/7",
                    "conclusion": "success",
                    "run_attempt": 2,
                }
            )
        if path == "repos/owner/repo/actions/runs/7/attempts/1/jobs?per_page=100":
            return json.dumps({"jobs": [{"id": 90, "conclusion": "failure"}]})
        if path == "repos/owner/repo/actions/jobs/90/logs":
            return "FAILED tests/unit/test_alpha.py::test_one - AssertionError\n"
        if path == "repos/owner/repo/issues?state=all&per_page=100":
            return json.dumps([])
        if path == "repos/owner/repo/issues" and "--method" in args:
            fields = [args[index + 1].split("=", 1) for index, value in enumerate(args[:-1]) if value == "-f"]
            payload = dict(fields)
            self.created.append(payload)
            return json.dumps({"number": 12, **payload})
        raise AssertionError(f"unexpected gh call: {args}")


def test_network_function_reads_failed_attempt_logs_and_opens_one_issue() -> None:
    gh = FakeGh()

    catcher.open_or_update_flaky_tests("owner/repo", "7", 2, gh=gh)

    assert len(gh.created) == 1
    assert gh.created[0]["title"] == "flaky test: tests/unit/test_alpha.py::test_one"
    assert "https://example.test/runs/7" in gh.created[0]["body"]
    assert "attempt 1" in gh.created[0]["body"]


def test_workflow_runs_only_after_a_successful_ci_rerun() -> None:
    path = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "flake-catcher.yml"
    workflow = yaml.safe_load(path.read_text())
    triggers = workflow.get("on") or workflow[True]

    assert triggers["workflow_run"]["workflows"] == ["CI"]
    assert triggers["workflow_run"]["types"] == ["completed"]
    job = workflow["jobs"]["catch-flakes"]
    assert "conclusion == 'success'" in job["if"]
    assert "run_attempt > 1" in job["if"]
    assert job["permissions"]["actions"] == "read"
    assert job["permissions"]["issues"] == "write"


class RacingGh(FakeGh):
    """Another catcher run opened the same issue between our read and our create."""

    def __init__(self) -> None:
        super().__init__()
        self.lists = 0

    def __call__(self, args: list[str]) -> str:
        path = args[1]
        if path == "repos/owner/repo/issues?state=all&per_page=100":
            self.lists += 1
            if self.lists == 1:
                return json.dumps([])
            title = "flaky test: tests/unit/test_alpha.py::test_one"
            other = {"number": 11, "title": title, "state": "open", "body": "Test: x\n\n- other run"}
            ours = {"number": 12, "title": title, "state": "open", "body": self.created[0]["body"]}
            return json.dumps([ours, other])
        if path.startswith("repos/owner/repo/issues/") and "--method" in args:
            fields = dict(args[i + 1].split("=", 1) for i, v in enumerate(args[:-1]) if v == "-f")
            self.updated.append((path, fields))
            return json.dumps({"number": int(path.rsplit("/", 1)[1]), **fields})
        return super().__call__(args)


def test_a_duplicate_from_a_concurrent_run_folds_into_the_oldest_issue() -> None:
    gh = RacingGh()

    catcher.open_or_update_flaky_tests("owner/repo", "7", 2, gh=gh)

    updates = dict(gh.updated)
    assert "https://example.test/runs/7" in updates["repos/owner/repo/issues/11"]["body"]
    assert updates["repos/owner/repo/issues/12"]["state"] == "closed"


def test_parse_failed_lines_keeps_package_suite_tests() -> None:
    logs = "FAILED packages/kollabor-engine/tests/test_e.py::test_x - AssertionError\n"

    assert catcher.parse_failed_lines(logs) == ["packages/kollabor-engine/tests/test_e.py::test_x"]


def test_job_logs_are_read_even_when_they_hold_terminal_escape_codes() -> None:
    calls: list[list[str]] = []

    def gh(args: list[str]) -> str:
        calls.append(list(args))
        return "log text"

    assert catcher._api_text(gh, "repos/o/r/actions/jobs/1/logs") == "log text"
    assert "--allow-escape-sequences" in calls[0]

    def old_gh(args: list[str]) -> str:
        if "--allow-escape-sequences" in args:
            raise RuntimeError("unknown flag: --allow-escape-sequences")
        return "old log"

    assert catcher._api_text(old_gh, "repos/o/r/actions/jobs/1/logs") == "old log"


def test_gh_output_that_is_not_utf8_does_not_crash(tmp_path, monkeypatch) -> None:
    fake = tmp_path / "gh"
    fake.write_text("#!/bin/sh\nprintf 'FAILED tests/unit/test_a.py::t \\377\\n'\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")

    assert "FAILED tests/unit/test_a.py::t" in catcher._run_gh(["api", "x"])


def test_parse_failed_lines_keeps_parameter_ids_that_contain_the_separator() -> None:
    logs = (
        "FAILED tests/unit/test_net.py::test_link[network - timeout] - AssertionError\n"
        "FAILED tests/unit/test_net.py::test_link[network - reset] - AssertionError\n"
    )

    assert catcher.parse_failed_lines(logs) == [
        "tests/unit/test_net.py::test_link[network - timeout]",
        "tests/unit/test_net.py::test_link[network - reset]",
    ]


class TimedOutGh(FakeGh):
    def __call__(self, args: list[str]) -> str:
        if args[1] == "repos/owner/repo/actions/runs/7/attempts/1/jobs?per_page=100":
            return json.dumps({"jobs": [{"id": 90, "conclusion": "timed_out"}]})
        return super().__call__(args)


def test_a_timed_out_attempt_is_read_for_flaky_tests() -> None:
    gh = TimedOutGh()

    catcher.open_or_update_flaky_tests("owner/repo", "7", 2, gh=gh)

    assert len(gh.created) == 1
