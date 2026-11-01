"""Unit tests for the fail-closed host-process scan policy."""

import ast
import subprocess
from pathlib import Path

import psutil
import pytest

from tests.process_scan_guard import (
    HOST_PROCESS_SCAN_TESTS,
    guarded_process_enumeration,
    host_process_scan_environment_errors,
    host_process_scans_allowed,
    is_process_table_enumeration_command,
)

CI_ENVIRONMENT = {
    "GITHUB_ACTIONS": "true",
    "GITHUB_WORKFLOW": "CI",
    "GITHUB_JOB": "tests",
    "GITHUB_REPOSITORY": "kollaborai/kollab",
    "GITHUB_EVENT_NAME": "pull_request",
    "GITHUB_WORKFLOW_REF": ("kollaborai/kollab/.github/workflows/ci.yml@refs/pull/570/merge"),
    "RUNNER_OS": "Linux",
    "RUNNER_ENVIRONMENT": "github-hosted",
}


def test_scan_manifest_names_existing_live_process_tests():
    tests_root = Path(__file__).parents[1]
    for module_path, expected_names in HOST_PROCESS_SCAN_TESTS.items():
        module_path = tests_root / module_path
        tree = ast.parse(module_path.read_text(encoding="utf-8"))
        declared_names = {
            node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        assert expected_names <= declared_names


def test_host_process_scans_require_explicit_option_and_sanctioned_ci_metadata():
    assert host_process_scans_allowed(True, CI_ENVIRONMENT)
    assert not host_process_scans_allowed(False, CI_ENVIRONMENT)


def test_runner_environment_alone_cannot_authorize_host_process_scans():
    assert not host_process_scans_allowed(True, {"RUNNER_ENVIRONMENT": "github-hosted"})


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("GITHUB_JOB", "another-job"),
        ("GITHUB_WORKFLOW", "Other workflow"),
        ("GITHUB_REPOSITORY", "someone/fork"),
        ("RUNNER_OS", "macOS"),
        ("RUNNER_ENVIRONMENT", "self-hosted"),
        ("GITHUB_EVENT_NAME", "schedule"),
        ("GITHUB_WORKFLOW_REF", "someone/fork/.github/workflows/ci.yml@refs/heads/main"),
    ],
)
def test_mismatched_runner_or_job_metadata_fails_closed(key, value):
    environ = dict(CI_ENVIRONMENT)
    environ[key] = value
    assert not host_process_scans_allowed(True, environ)
    assert key in host_process_scan_environment_errors(environ)


def test_process_table_detector_blocks_global_ps_but_allows_pid_targeting():
    assert is_process_table_enumeration_command(["ps", "-A", "-o", "pid,ppid"])
    assert is_process_table_enumeration_command(["/bin/ps", "-U", "501", "-o", "pid"])

    assert is_process_table_enumeration_command(["/bin/ps", "-axo", "pid,ppid"])
    assert is_process_table_enumeration_command(["/bin/ps", "-E", "-axww", "-o", "pid=,command="])
    assert is_process_table_enumeration_command("/bin/sh -c 'ps -A -o pid'")
    assert is_process_table_enumeration_command(["/bin/bash", "-lc", "ps -A"])
    assert is_process_table_enumeration_command(["ps"])
    assert is_process_table_enumeration_command(["ps", "-o", "pid"])
    assert not is_process_table_enumeration_command(["ps", "-p", "4242", "-o", "pid"])
    assert not is_process_table_enumeration_command(["/usr/bin/python3", "-c", "print('not a process scan')"])


def test_unmarked_process_enumeration_fails_before_original_call():
    calls = []
    guarded = guarded_process_enumeration("psutil.process_iter", lambda: calls.append("enumerated"), allowed=False)

    with pytest.raises(RuntimeError, match="blocked for unmarked test"):
        guarded()

    assert calls == []


def test_authorized_process_enumeration_wrapper_preserves_original_call():
    def original(value):
        return value + 1

    guarded = guarded_process_enumeration("psutil.process_iter", original, allowed=True)
    assert guarded is original
    assert guarded(4) == 5


def test_global_autouse_guard_blocks_process_iter():
    with pytest.raises(RuntimeError, match="blocked for unmarked test"):
        psutil.process_iter()


def test_global_autouse_guard_blocks_broad_ps_before_launch():
    with pytest.raises(RuntimeError, match="blocked for unmarked test"):
        subprocess.Popen(["/bin/ps", "-A", "-o", "pid="])
