"""The review gate must not be bypassed by automatic merge arming."""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


def _workflow(name):
    return yaml.load((ROOT / ".github" / "workflows" / name).read_text(), Loader=yaml.BaseLoader)


@pytest.mark.parametrize(
    "event,action",
    [
        ("pull_request", "opened"),
        ("pull_request", "reopened"),
        ("pull_request", "ready_for_review"),
        ("pull_request", "synchronize"),
        ("pull_request_target", "opened"),
        ("push", None),
        ("workflow_run", "completed"),
    ],
)
def test_auto_arming_cannot_start_from_repository_events(event, action):
    triggers = _workflow("auto-merge.yml")["on"]
    assert set(triggers) == {"workflow_dispatch"}
    assert event not in triggers, (event, action)


@pytest.mark.skipif(not shutil.which("bash"), reason="GitHub's Linux runner uses bash")
def test_manual_notice_has_no_write_authority_or_github_calls(tmp_path):
    workflow = _workflow("auto-merge.yml")
    assert workflow["permissions"] == {}
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    marker = tmp_path / "gh-called"
    gh = fakebin / "gh"
    gh.write_text(f"#!{sys.executable}\nfrom pathlib import Path\nPath({str(marker)!r}).touch()\n")
    gh.chmod(0o755)
    env = {"PATH": f"{fakebin}{os.pathsep}{os.defpath}"}

    for job in workflow["jobs"].values():
        assert job.get("permissions", {}) == {}
        for step in job["steps"]:
            assert "uses" not in step
            subprocess.run(["bash", "-e", "-c", step["run"]], env=env, check=True, timeout=5)

    assert not marker.exists()


@pytest.mark.skipif(not shutil.which("bash"), reason="branch-protection setup uses bash")
def test_branch_protection_setup_does_not_change_auto_merge_setting(tmp_path):
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    log = tmp_path / "calls.jsonl"
    gh = fakebin / "gh"
    gh.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "args = sys.argv[1:]\n"
        "with open(os.environ['FAKE_GH_LOG'], 'a') as f: f.write(json.dumps(args) + '\\n')\n"
        "if args[:2] == ['auth', 'status']:\n"
        "    pass\n"
        "elif args[:2] == ['repo', 'view']:\n"
        "    print('example/project')\n"
        "elif args[0] == 'api' and args[1].endswith('/rulesets'):\n"
        "    print('123')\n"
        "elif args[0] == 'api' and args[1].endswith('/rulesets/123'):\n"
        "    json.loads(sys.stdin.read())\n"
        "elif args[0] == 'api' and args[1] == 'repos/example/project':\n"
        "    pass\n"
        "else:\n"
        "    sys.exit(2)\n"
    )
    gh.chmod(0o755)
    env = dict(os.environ)
    for key in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"):
        env.pop(key, None)
    env.update(PATH=f"{fakebin}{os.pathsep}{os.environ.get('PATH', os.defpath)}", FAKE_GH_LOG=str(log))

    subprocess.run(
        ["bash", str(ROOT / ".github" / "scripts" / "setup-branch-protection.sh")],
        cwd=ROOT,
        env=env,
        input="",
        text=True,
        capture_output=True,
        timeout=5,
        check=True,
    )
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    patches = [call for call in calls if call[:2] == ["api", "repos/example/project"]]
    assert len(patches) == 1
    fields = [patches[0][i + 1] for i, arg in enumerate(patches[0][:-1]) if arg == "-F"]
    assert fields == ["allow_merge_commit=true"]


@pytest.mark.skipif(
    not shutil.which("bash") or not shutil.which("jq"),
    reason="branch-protection setup uses bash and jq",
)
@pytest.mark.parametrize("bypass", ["empty", "hidden", "null", "unexpected"])
@pytest.mark.parametrize("approval_count", [0, 1, 2])
def test_branch_protection_check_requires_one_approval_and_visible_empty_bypass(
    tmp_path, bypass, approval_count
):
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    log = tmp_path / "calls.jsonl"
    saved = tmp_path / "ruleset.json"
    gh = fakebin / "gh"
    gh.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "args = sys.argv[1:]\n"
        "with open(os.environ['FAKE_GH_LOG'], 'a') as f: f.write(json.dumps(args) + '\\n')\n"
        "if args[:2] == ['auth', 'status']:\n"
        "    pass\n"
        "elif args[:2] == ['repo', 'view']:\n"
        "    print('example/project')\n"
        "elif args[0] == 'api' and args[1].endswith('/rulesets'):\n"
        "    print('123')\n"
        "elif args[0] == 'api' and args[1].endswith('/rulesets/123'):\n"
        "    p = pathlib.Path(os.environ['FAKE_RULESET'])\n"
        "    if '--method' in args:\n"
        "        p.write_text(sys.stdin.read())\n"
        "    else:\n"
        "        data = json.loads(p.read_text())\n"
        "        case = os.environ['FAKE_BYPASS']\n"
        "        for rule in data['rules']:\n"
        "            if rule['type'] == 'pull_request':\n"
        "                rule['parameters']['required_approving_review_count'] = int(os.environ["
        "'FAKE_APPROVAL_COUNT'])\n"
        "        if case == 'empty': data['bypass_actors'] = []\n"
        "        elif case == 'hidden': data.pop('bypass_actors', None)\n"
        "        elif case == 'null': data['bypass_actors'] = None\n"
        "        elif case == 'unexpected': data['bypass_actors'] = ["
        "{'actor_id': 5, 'actor_type': 'RepositoryRole', 'bypass_mode': 'always'}]\n"
        "        print(json.dumps(data))\n"
        "elif args[0] == 'api' and args[1] == 'repos/example/project':\n"
        "    if '--jq' in args: print('true')\n"
        "else:\n"
        "    sys.exit(2)\n"
    )
    gh.chmod(0o755)
    env = dict(os.environ)
    for key in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"):
        env.pop(key, None)
    env.update(
        PATH=f"{fakebin}{os.pathsep}{os.environ.get('PATH', os.defpath)}",
        FAKE_GH_LOG=str(log),
        FAKE_RULESET=str(saved),
        FAKE_BYPASS=bypass,
        FAKE_APPROVAL_COUNT=str(approval_count),
    )
    script = str(ROOT / ".github" / "scripts" / "setup-branch-protection.sh")
    subprocess.run(["bash", script], cwd=ROOT, env=env, input="", text=True, capture_output=True, check=True, timeout=5)
    before_check = len(log.read_text().splitlines())
    result = subprocess.run(["bash", script, "--check"], cwd=ROOT, env=env, text=True, capture_output=True, timeout=5)
    calls = [json.loads(line) for line in log.read_text().splitlines()[before_check:]]
    assert not any("--method" in call for call in calls)
    matches_policy = bypass == "empty" and approval_count == 1
    assert (result.returncode == 0) is matches_policy
    if bypass in ("hidden", "null"):
        assert "Cannot verify bypass actors" in result.stdout
    elif not matches_policy:
        assert "Drift on" in result.stdout
    else:
        assert "one approval" in result.stdout


def test_watcher_event_envelopes_include_workflow_run_association_fallback_and_review():
    watcher = _workflow("ci-after-auto-merge.yml")
    assert watcher["on"]["workflow_run"]["types"] == ["completed"]
    assert watcher["on"]["pull_request_review"]["types"] == ["submitted"]
    condition = watcher["jobs"]["dispatch-main-ci"]["if"]
    assert "github.event.workflow_run.event == 'pull_request'" in condition
    assert "github.event.review.state == 'approved'" in condition
    env = watcher["jobs"]["dispatch-main-ci"]["steps"][0]["env"]
    assert "workflow_run.pull_requests[0].number" in env["EVENT_PR_NUMBER"]
    assert "pull_request.number" in env["EVENT_PR_NUMBER"]
    assert "workflow_run.head_sha" in env["SOURCE_SHA"]
    assert "pull_request.head.sha" in env["SOURCE_SHA"]
    script = watcher["jobs"]["dispatch-main-ci"]["steps"][0]["run"]
    assert 'actions/runs/$WORKFLOW_RUN_ID' in script
    assert 'commits/$SOURCE_SHA/pulls' in script
    assert "select(.base.ref == \"main\")" in script


@pytest.mark.skipif(
    not shutil.which("bash") or not shutil.which("jq"),
    reason="GitHub's Linux runner provides bash and jq",
)
@pytest.mark.parametrize(
    "scenario",
    [
        "unarmed", "legacy-armed", "stale-head", "review-required", "human-merged",
        "workflow-run-direct", "workflow-run-by-run", "workflow-run-by-commit",
        "workflow-run-missing", "workflow-run-stale", "review-event",
    ],
)
def test_post_merge_watcher_never_arms_and_still_dispatches_for_legacy_merges(tmp_path, scenario):
    watcher = _workflow("ci-after-auto-merge.yml")
    script = watcher["jobs"]["dispatch-main-ci"]["steps"][0]["run"]
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    log = tmp_path / "calls.jsonl"
    counter = tmp_path / "count"
    gh = fakebin / "gh"
    gh.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, subprocess, sys\n"
        "args = sys.argv[1:]\n"
        "with open(os.environ['FAKE_GH_LOG'], 'a') as f: f.write(json.dumps(args) + '\\n')\n"
        "def emit(data):\n"
        "    if '--jq' in args:\n"
        "        query = args[args.index('--jq') + 1]\n"
        "        result = subprocess.run(['jq', '-r', query], input=json.dumps(data),\n"
        "            text=True, capture_output=True, check=True)\n"
        "        print(result.stdout, end='')\n"
        "    else:\n"
        "        print(json.dumps(data))\n"
        "if args[0] == 'api' and '/actions/workflows/ci.yml/runs?' in args[1]:\n"
        "    emit({'workflow_runs': [{'conclusion': 'success'}]})\n"
        "elif args[0] == 'api' and args[1].endswith('/actions/runs/77'):\n"
        "    matches = [{'number': 99, 'base': {'ref': 'dev'}},\n"
        "        {'number': 42, 'base': {'ref': 'main'}}]\n"
        "    prs = matches if os.environ['FAKE_SCENARIO'] == 'workflow-run-by-run' else []\n"
        "    emit({'pull_requests': prs})\n"
        "elif args[0] == 'api' and '/commits/' in args[1] and args[1].endswith('/pulls'):\n"
        "    matches = [{'number': 99, 'base': {'ref': 'dev'}},\n"
        "        {'number': 42, 'base': {'ref': 'main'}}]\n"
        "    prs = matches if os.environ['FAKE_SCENARIO'] == 'workflow-run-by-commit' else []\n"
        "    emit(prs)\n"
        "elif args[0] == 'api' and args[1].endswith('/pulls/42'):\n"
        "    p = pathlib.Path(os.environ['FAKE_COUNTER'])\n"
        "    n = int(p.read_text()) + 1 if p.exists() else 1\n"
        "    p.write_text(str(n))\n"
        "    case = os.environ['FAKE_SCENARIO']\n"
        "    armed = case in ('legacy-armed', 'review-required', 'workflow-run-direct', "
        "'workflow-run-by-run', 'workflow-run-by-commit', 'review-event')\n"
        "    merged = case == 'human-merged' or (armed and case != 'review-required' and n > 1)\n"
        "    sha = 'c' * 40 if case in ('stale-head', 'workflow-run-stale') else os.environ['SOURCE_SHA']\n"
        "    actor = 'human' if case == 'human-merged' else 'github-actions[bot]'\n"
        "    print(json.dumps({'base': {'ref': 'main'}, 'head': {'sha': sha},\n"
        "        'state': 'closed' if merged else 'open', 'merged_at': '2026-01-01T00:00:00Z' if merged else None,\n"
        "        'merge_commit_sha': 'b' * 40 if merged else None, 'merged_by': {'login': actor},\n"
        "        'auto_merge': {'enabled': True} if armed else None}))\n"
        "elif args[:2] == ['pr', 'view']:\n"
        "    print('CHANGES_REQUESTED' if os.environ['FAKE_SCENARIO'] == 'review-required' else 'APPROVED')\n"
        "elif args[:2] == ['workflow', 'run']:\n"
        "    pass\n"
        "else:\n"
        "    sys.exit(2)\n"
    )
    gh.chmod(0o755)
    sleep = fakebin / "sleep"
    sleep.write_text("#!/bin/sh\nexit 0\n")
    sleep.chmod(0o755)
    env = dict(os.environ)
    for key in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"):
        env.pop(key, None)
    event_name = "workflow_run" if scenario.startswith("workflow-run") else (
        "pull_request_review" if scenario == "review-event" else "pull_request"
    )
    pr_number = "" if scenario in ("workflow-run-by-run", "workflow-run-by-commit", "workflow-run-missing") else "42"
    env.update(
        PATH=f"{fakebin}{os.pathsep}{os.environ.get('PATH', os.defpath)}",
        GH_TOKEN="fixture-only",
        REPOSITORY="example/project",
        EVENT_NAME=event_name,
        EVENT_PR_NUMBER=pr_number,
        WORKFLOW_RUN_ID="77" if scenario.startswith("workflow-run") else "",
        SOURCE_SHA="a" * 40,
        FAKE_GH_LOG=str(log),
        FAKE_COUNTER=str(counter),
        FAKE_SCENARIO=scenario,
    )

    result = subprocess.run(
        ["bash", "-e", "-o", "pipefail", "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert not any(call[:2] == ["pr", "merge"] for call in calls)
    dispatches = [call for call in calls if call[:2] == ["workflow", "run"]]
    if scenario in (
        "legacy-armed", "workflow-run-direct", "workflow-run-by-run", "workflow-run-by-commit", "review-event"
    ):
        assert len(dispatches) == 1
        assert int(counter.read_text()) == 2
        assert "Dispatched CI on main" in result.stdout
    else:
        assert not dispatches
        if scenario == "workflow-run-missing":
            assert not counter.exists()
        else:
            assert int(counter.read_text()) == 1
    if scenario in ("workflow-run-by-run", "workflow-run-by-commit", "workflow-run-missing"):
        assert any(call[0] == "api" and call[1].endswith("/actions/runs/77") for call in calls)
    if scenario in ("workflow-run-by-commit", "workflow-run-missing"):
        assert any(call[0] == "api" and "/commits/" in call[1] and call[1].endswith("/pulls") for call in calls)
