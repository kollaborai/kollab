"""The review gate must not be bypassed by automatic merge arming."""

import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CODEOWNERS_BODY = "* @maarco @kollaborai\n/.github/CODEOWNERS @maarco @kollaborai\n"
CODEOWNERS_B64 = base64.encodebytes(CODEOWNERS_BODY.encode()).decode("ascii")


def _workflow(name):
    return yaml.load((ROOT / ".github" / "workflows" / name).read_text(), Loader=yaml.BaseLoader)


AUTO_MERGE_SHA = "a" * 40
REPO_API = "repos/example/project"
FAKE_GH_FOR_GATE = """#!{python}
import json, os, subprocess, sys
args = sys.argv[1:]
state = json.load(open(os.environ["FAKE_GH_STATE"]))
target = next(a for a in args[1:] if a == "graphql" or a.startswith("repos/"))
pages = state["graphql"] if target == "graphql" else [state[target.split("?")[0]]]
if "--paginate" not in args:
    pages = pages[:1]
jq_filter = args[args.index("--jq") + 1] if "--jq" in args else "."
for page in pages:
    sys.stdout.write(subprocess.run(
        ["jq", "-rc", jq_filter], input=json.dumps(page),
        capture_output=True, text=True, check=True,
    ).stdout)
"""
needs_gate_tools = pytest.mark.skipif(
    not shutil.which("bash") or not shutil.which("jq"),
    reason="the merge bot's gate step uses bash and jq",
)


def _threads(resolved_flags):
    nodes = [{"isResolved": flag} for flag in resolved_flags]
    return {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": nodes}}}}}


def _gate_state():
    commit = f"{REPO_API}/commits/{AUTO_MERGE_SHA}"
    return {
        REPO_API: {"default_branch": "main"},
        f"{REPO_API}/rules/branches/main": [
            {
                "type": "required_status_checks",
                "parameters": {"required_status_checks": [{"context": "ci/tests", "integration_id": 15368}]},
            }
        ],
        f"{REPO_API}/pulls/7": {
            "state": "open",
            "draft": False,
            "base": {"ref": "main"},
            "head": {"sha": AUTO_MERGE_SHA, "repo": {"full_name": "example/project"}},
            "user": {"login": "author"},
            "labels": [],
            "mergeable_state": "clean",
        },
        f"{REPO_API}/pulls/7/files": [{"filename": "src/app.py"}],
        f"{commit}/check-runs": {
            "check_runs": [
                {
                    "id": 1,
                    "name": "ci/tests",
                    "conclusion": "success",
                    "started_at": "2026-11-04T00:00:00Z",
                    "app": {"id": 15368},
                }
            ]
        },
        f"{commit}/statuses": [
            {"context": "review/independent", "state": "success", "creator": {"login": "reviewer[bot]"}}
        ],
        f"{REPO_API}/pulls/7/reviews": [
            {
                "id": 99,
                "state": "APPROVED",
                "commit_id": AUTO_MERGE_SHA,
                "user": {"login": "owner"},
                "submitted_at": "2026-11-04T00:00:00Z",
            }
        ],
        "graphql": [_threads([True])],
    }


def _run_gate(tmp_path, state, env_overrides=None):
    """Run the bot's real evaluate step against a fake gh; returns (stdout, merges output)."""
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    gh = fakebin / "gh"
    gh.write_text(FAKE_GH_FOR_GATE.replace("{python}", sys.executable))
    gh.chmod(0o755)
    state_file = tmp_path / "state.json"
    state_file.write_text(json.dumps(state))
    output = tmp_path / "github_output"
    env = {
        "PATH": f"{fakebin}{os.pathsep}{os.environ['PATH']}",
        "FAKE_GH_STATE": str(state_file),
        "GITHUB_OUTPUT": str(output),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        "REPO": "example/project",
        "EV_PR": "7",
        "EV_RUN_PRS": "null",
        "EV_SHA": "",
        "REVIEW_CONTEXT": "review/independent",
        "REVIEWER_LOGINS": "reviewer[bot]",
        "APPROVER_LOGINS": "",
        "HOLD_LABELS": "merge/hold",
        "OPT_IN_LABEL": "",
        "MANUAL_PATHS": ".github/",
        **(env_overrides or {}),
    }
    step = _workflow("auto-merge.yml")["jobs"]["evaluate"]["steps"][0]
    proc = subprocess.run(["bash", "-c", step["run"]], env=env, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout, output.read_text() if output.exists() else ""


def test_merge_bot_runs_no_pr_code_and_merges_only_when_turned_on():
    workflow = _workflow("auto-merge.yml")
    # Each trigger runs the default-branch copy, so a PR cannot rewrite its own gate.
    assert set(workflow["on"]) == {"pull_request_target", "workflow_run", "status", "workflow_dispatch"}
    assert workflow["permissions"] == {}
    jobs = workflow["jobs"]
    assert set(jobs["evaluate"]["permissions"].values()) == {"read"}
    assert jobs["merge"]["permissions"] == {}
    assert jobs["merge"]["environment"] == "pr-gates"
    assert "vars.PR_GATES_AUTO_MERGE == 'on'" in jobs["merge"]["if"]
    # A dispatched dry run may evaluate with the variable unset, but never reaches the merge job.
    assert " ".join(jobs["evaluate"]["if"].split()).startswith(
        "(vars.PR_GATES_AUTO_MERGE == 'on' || vars.PR_GATES_AUTO_MERGE == 'dry-run' || "
        "(github.event_name == 'workflow_dispatch' && inputs.dry_run)) && "
    )
    assert "!(github.event_name == 'workflow_dispatch' && inputs.dry_run)" in jobs["merge"]["if"]
    for job in jobs.values():
        for step in job["steps"]:
            assert "uses" not in step
            assert "--admin" not in step["run"]
    assert "--match-head-commit" in jobs["merge"]["steps"][0]["run"]

    relay = _workflow("auto-merge-relay.yml")
    assert set(relay["on"]) == {"pull_request_review"}
    assert relay["permissions"] == {}
    assert all("uses" not in step for job in relay["jobs"].values() for step in job["steps"])


@needs_gate_tools
def test_merge_bot_lists_a_pr_when_every_gate_holds(tmp_path):
    stdout, merges = _run_gate(tmp_path, _gate_state())
    assert "ELIGIBLE" in stdout
    assert f"merges=7:{AUTO_MERGE_SHA}" in merges


def _set(path, **fields):
    return lambda state: state[path].update(fields)


def _set_review(**fields):
    return lambda state: state[f"{REPO_API}/pulls/7/reviews"][0].update(fields)


def _set_value(path, value):
    return lambda state: state.__setitem__(path, value)


_STATUSES = f"{REPO_API}/commits/{AUTO_MERGE_SHA}/statuses"
_CHECK_RUNS = f"{REPO_API}/commits/{AUTO_MERGE_SHA}/check-runs"


@needs_gate_tools
@pytest.mark.parametrize(
    "change,env,expected",
    [
        (_set(f"{REPO_API}/pulls/7", draft=True), None, "skip: draft"),
        (
            _set(f"{REPO_API}/pulls/7", head={"sha": AUTO_MERGE_SHA, "repo": {"full_name": "fork/project"}}),
            None,
            "skip: fork head",
        ),
        (_set(f"{REPO_API}/pulls/7", labels=[{"name": "merge/hold"}]), None, "hold label"),
        (
            _set_value(f"{REPO_API}/pulls/7/files", [{"filename": ".github/workflows/ci.yml"}]),
            None,
            "manual-merge path",
        ),
        (_set_value(f"{REPO_API}/rules/branches/main", []), None, "No required checks"),
        (
            _set_value(_CHECK_RUNS, {"check_runs": [{"id": 1, "name": "ci/tests", "conclusion": "failure",
                                                     "started_at": "2026-11-04T00:00:00Z", "app": {"id": 15368}}]}),
            None,
            "required checks not green",
        ),
        (_set_value(_STATUSES, []), None, "not success"),
        (None, {"REVIEWER_LOGINS": ""}, "fail closed"),
        (None, {"REVIEWER_LOGINS": "someone-else"}, "not an allowed reviewer"),
        (
            _set_value(_STATUSES, [{"context": "review/independent", "state": "success",
                                    "creator": {"login": "author"}}]),
            {"REVIEWER_LOGINS": "author"},
            "posted by the PR author",
        ),
        (_set_review(commit_id="b" * 40), None, "approval on"),
        (_set_review(user={"login": "author"}), None, "approval on"),
        (_set_review(state="CHANGES_REQUESTED"), None, "changes_requested"),
        (_set_value("graphql", [_threads([True] * 100), _threads([False])]), None, "1 unresolved review threads"),
        (_set(f"{REPO_API}/pulls/7", mergeable_state="behind"), None, "mergeable_state=behind"),
    ],
    ids=[
        "draft",
        "fork-head",
        "hold-label",
        "manual-path",
        "no-required-checks",
        "required-check-red",
        "no-review-status",
        "no-reviewer-configured",
        "reviewer-not-allowed",
        "review-by-author",
        "approval-on-old-head",
        "approval-by-author",
        "changes-requested",
        "unresolved-thread-on-page-2",
        "behind-base",
    ],
)
def test_merge_bot_fails_closed(tmp_path, change, env, expected):
    state = _gate_state()
    if change:
        change(state)
    stdout, merges = _run_gate(tmp_path, state, env)
    assert expected in stdout
    assert "ELIGIBLE" not in stdout
    assert f"7:{AUTO_MERGE_SHA}" not in merges


@pytest.mark.skipif(
    not shutil.which("bash") or not shutil.which("jq"),
    reason="branch-protection setup uses bash and jq",
)
@pytest.mark.parametrize("existing_ruleset", [True, False])
def test_branch_protection_setup_does_not_change_auto_merge_setting(tmp_path, existing_ruleset):
    assert CODEOWNERS_B64.endswith("\n") and "\n" in CODEOWNERS_B64[:-1]
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    log = tmp_path / "calls.jsonl"
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
        "elif args[0] == 'api' and '/contents/.github/CODEOWNERS?ref=main' in args[1]:\n"
        "    print(json.dumps({'encoding': 'base64', 'content': os.environ['FAKE_CODEOWNERS']}))\n"
        "elif args[0] == 'api' and '/collaborators/maarco/permission' in args[1]:\n"
        "    print('write')\n"
        "elif args[0] == 'api' and '/collaborators/kollaborai/permission' in args[1]:\n"
        "    print('admin')\n"
        "elif args[0] == 'api' and args[1].endswith('/rulesets') and '--method' in args:\n"
        "    pathlib.Path(os.environ['FAKE_RULESET']).write_text(sys.stdin.read())\n"
        "    print(json.dumps({'id': 123}))\n"
        "elif args[0] == 'api' and args[1].endswith('/rulesets'):\n"
        "    print('123' if os.environ.get('FAKE_EXISTING_RULESET', 'true') == 'true' else '')\n"
        "elif args[0] == 'api' and args[1].endswith('/rulesets/123'):\n"
        "    p = pathlib.Path(os.environ['FAKE_RULESET'])\n"
        "    if '--method' in args: p.write_text(sys.stdin.read())\n"
        "    else: print(p.read_text())\n"
        "elif args[0] == 'api' and args[1] == 'repos/example/project':\n"
        "    pass\n"
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
        FAKE_RULESET=str(tmp_path / "applied-ruleset.json"),
        FAKE_CODEOWNERS=CODEOWNERS_B64,
        FAKE_EXISTING_RULESET=str(existing_ruleset).lower(),
    )

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
    ruleset_calls = [call for call in calls if call[0] == "api" and call[1].endswith("/rulesets/123")]
    mutation_calls = [call for call in calls if call[0] == "api" and "--method" in call and "/rulesets" in call[1]]
    assert len(ruleset_calls) == (2 if existing_ruleset else 1)
    assert len(mutation_calls) == 1
    expected_method = "PUT" if existing_ruleset else "POST"
    assert mutation_calls[0][mutation_calls[0].index("--method") + 1] == expected_method
    readback = ruleset_calls[-1]
    assert "--method" not in readback
    assert calls.index(readback) < calls.index(patches[0])
    applied_ruleset = json.loads(Path(env["FAKE_RULESET"]).read_text())
    assert applied_ruleset["bypass_actors"] == []
    pull_request_rule = next(rule for rule in applied_ruleset["rules"] if rule["type"] == "pull_request")
    assert pull_request_rule["parameters"]["required_approving_review_count"] == 1
    assert pull_request_rule["parameters"]["require_code_owner_review"] is True
    assert pull_request_rule["parameters"]["dismiss_stale_reviews_on_push"] is True
    assert pull_request_rule["parameters"]["required_review_thread_resolution"] is True
    assert pull_request_rule["parameters"]["allowed_merge_methods"] == ["merge"]
    assert {rule["type"] for rule in applied_ruleset["rules"]} == {
        "deletion", "non_fast_forward", "pull_request", "required_status_checks"
    }
    checks = next(rule for rule in applied_ruleset["rules"] if rule["type"] == "required_status_checks")
    assert checks["parameters"]["strict_required_status_checks_policy"] is True
    assert checks["parameters"]["required_status_checks"] == [
        {"context": "ci/tests", "integration_id": 15368},
        {"context": "ci/standards-check", "integration_id": 15368},
        {"context": "ci/security-scan", "integration_id": 15368},
    ]
    assert any("/collaborators/maarco/permission" in arg for call in calls for arg in call)
    assert any("/collaborators/kollaborai/permission" in arg for call in calls for arg in call)


@pytest.mark.skipif(
    not shutil.which("bash") or not shutil.which("jq"),
    reason="branch-protection setup uses bash and jq",
)
@pytest.mark.parametrize("readback_case", ["nonempty_bypass", "hidden_bypass", "protection_mismatch"])
def test_branch_protection_apply_fails_when_ruleset_readback_does_not_match(tmp_path, readback_case):
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    log = tmp_path / "calls.jsonl"
    saved = tmp_path / "ruleset.json"
    gh = fakebin / "gh"
    gh.write_text(
        f'''#!{sys.executable}
import json, os, pathlib, sys
args = sys.argv[1:]
with open(os.environ['FAKE_GH_LOG'], 'a') as f: f.write(json.dumps(args) + '\\n')
if args[:2] == ['auth', 'status']:
    pass
elif args[:2] == ['repo', 'view']:
    print('example/project')
elif args[0] == 'api' and '/contents/.github/CODEOWNERS?ref=main' in args[1]:
    print(json.dumps({{'encoding': 'base64', 'content': os.environ['FAKE_CODEOWNERS']}}))
elif args[0] == 'api' and '/collaborators/maarco/permission' in args[1]:
    print('write')
elif args[0] == 'api' and '/collaborators/kollaborai/permission' in args[1]:
    print('admin')
elif args[0] == 'api' and args[1].endswith('/rulesets'):
    print('123')
elif args[0] == 'api' and args[1].endswith('/rulesets/123'):
    p = pathlib.Path(os.environ['FAKE_RULESET'])
    if '--method' in args:
        p.write_text(sys.stdin.read())
    else:
        data = json.loads(p.read_text())
        case = os.environ['FAKE_READBACK_CASE']
        if case == 'nonempty_bypass':
            data['bypass_actors'] = [{{'actor_id': 9, 'actor_type': 'User', 'bypass_mode': 'always'}}]
        elif case == 'hidden_bypass':
            data.pop('bypass_actors', None)
        elif case == 'protection_mismatch':
            for rule in data['rules']:
                if rule['type'] == 'pull_request':
                    rule['parameters']['required_approving_review_count'] = 2
        print(json.dumps(data))
elif args[0] == 'api' and args[1] == 'repos/example/project':
    pass
else:
    sys.exit(2)
'''
    )
    gh.chmod(0o755)
    env = dict(os.environ)
    for key in ("GH_TOKEN", "GITHUB_TOKEN", "GH_ENTERPRISE_TOKEN", "GITHUB_ENTERPRISE_TOKEN"):
        env.pop(key, None)
    env.update(
        PATH=f"{fakebin}{os.pathsep}{os.environ.get('PATH', os.defpath)}",
        FAKE_GH_LOG=str(log),
        FAKE_RULESET=str(saved),
        FAKE_CODEOWNERS=CODEOWNERS_B64,
        FAKE_READBACK_CASE=readback_case,
    )

    result = subprocess.run(
        ["bash", str(ROOT / ".github" / "scripts" / "setup-branch-protection.sh")],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=5,
    )
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert result.returncode == 1
    assert any(call[0] == "api" and call[1].endswith("/rulesets/123") and "--method" in call for call in calls)
    assert any(call[0] == "api" and call[1].endswith("/rulesets/123") and "--method" not in call for call in calls)
    assert not any(call[:2] == ["api", "repos/example/project"] for call in calls)
    if readback_case == "hidden_bypass":
        assert "ruleset write access" in result.stdout
    else:
        assert "Applied ruleset does not match" in result.stdout


@pytest.mark.parametrize("main_codeowners", ["", "* @someone\n"])
def test_branch_protection_apply_refuses_until_expected_codeowners_is_on_main(tmp_path, main_codeowners):
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    log = tmp_path / "calls.jsonl"
    gh = fakebin / "gh"
    gh.write_text(
        f"#!{sys.executable}\n"
        "import base64, json, os, sys\n"
        "args = sys.argv[1:]\n"
        "with open(os.environ['FAKE_GH_LOG'], 'a') as f: f.write(json.dumps(args) + '\\n')\n"
        "if args[:2] == ['auth', 'status']:\n"
        "    pass\n"
        "elif args[:2] == ['repo', 'view']:\n"
        "    print('example/project')\n"
        "elif args[0] == 'api' and '/contents/.github/CODEOWNERS?ref=main' in args[1]:\n"
        "    body = os.environ['FAKE_MAIN_CODEOWNERS'].encode()\n"
        "    print(json.dumps({'encoding': 'base64', 'content': base64.encodebytes(body).decode()}))\n"
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
        FAKE_MAIN_CODEOWNERS=main_codeowners,
    )

    result = subprocess.run(
        ["bash", str(ROOT / ".github" / "scripts" / "setup-branch-protection.sh")],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=5,
    )
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert result.returncode == 1
    assert "CODEOWNERS" in result.stdout and "on main" in result.stdout
    assert not any("--method" in call or "-F" in call for call in calls)
    assert not any("rulesets" in arg for call in calls for arg in call)


def test_codeowners_cover_every_path_and_protect_themselves():
    rules = (ROOT / ".github" / "CODEOWNERS").read_text().splitlines()
    assert rules == [
        "* @maarco @kollaborai",
        "/.github/CODEOWNERS @maarco @kollaborai",
    ]


@pytest.mark.skipif(
    not shutil.which("bash") or not shutil.which("jq"),
    reason="branch-protection setup uses bash and jq",
)
def test_branch_protection_apply_requires_write_access_for_both_codeowners(tmp_path):
    fakebin = tmp_path / "bin"
    fakebin.mkdir()
    log = tmp_path / "calls.jsonl"
    gh = fakebin / "gh"
    gh.write_text(
        f"#!{sys.executable}\n"
        "import base64, json, os, sys\n"
        "args = sys.argv[1:]\n"
        "with open(os.environ['FAKE_GH_LOG'], 'a') as f: f.write(json.dumps(args) + '\\n')\n"
        "if args[:2] == ['auth', 'status']:\n"
        "    pass\n"
        "elif args[:2] == ['repo', 'view']:\n"
        "    print('example/project')\n"
        "elif args[0] == 'api' and '/contents/.github/CODEOWNERS?ref=main' in args[1]:\n"
        "    body = os.environ['FAKE_MAIN_CODEOWNERS'].encode()\n"
        "    print(json.dumps({'encoding': 'base64', 'content': base64.encodebytes(body).decode()}))\n"
        "elif args[0] == 'api' and '/collaborators/maarco/permission' in args[1]:\n"
        "    print('read')\n"
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
        FAKE_MAIN_CODEOWNERS=CODEOWNERS_BODY,
    )

    result = subprocess.run(
        ["bash", str(ROOT / ".github" / "scripts" / "setup-branch-protection.sh")],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        timeout=5,
    )
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert result.returncode == 1
    assert "@maarco needs repository write access" in result.stdout
    assert not any("--method" in call or "-F" in call for call in calls)
    assert not any("rulesets" in arg for call in calls for arg in call)


@pytest.mark.skipif(
    not shutil.which("bash") or not shutil.which("jq"),
    reason="branch-protection setup uses bash and jq",
)
@pytest.mark.parametrize("bypass", ["empty", "hidden", "null", "unexpected"])
@pytest.mark.parametrize("approval_count", [0, 1, 2])
@pytest.mark.parametrize("code_owner_review", [False, True])
def test_branch_protection_check_requires_one_code_owner_approval_and_visible_empty_bypass(
    tmp_path, bypass, approval_count, code_owner_review
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
        "elif args[0] == 'api' and '/contents/.github/CODEOWNERS?ref=main' in args[1]:\n"
        "    print(json.dumps({'encoding': 'base64', 'content': os.environ['FAKE_CODEOWNERS']}))\n"
        "elif args[0] == 'api' and '/collaborators/maarco/permission' in args[1]:\n"
        "    print('write')\n"
        "elif args[0] == 'api' and '/collaborators/kollaborai/permission' in args[1]:\n"
        "    print('admin')\n"
        "elif args[0] == 'api' and args[1].endswith('/rulesets'):\n"
        "    print('123')\n"
        "elif args[0] == 'api' and args[1].endswith('/rulesets/123'):\n"
        "    p = pathlib.Path(os.environ['FAKE_RULESET'])\n"
        "    if '--method' in args:\n"
        "        p.write_text(sys.stdin.read())\n"
        "    else:\n"
        "        data = json.loads(p.read_text())\n"
        "        marker = pathlib.Path(os.environ['FAKE_VERIFY_MARKER'])\n"
        "        if marker.exists():\n"
        "            case = os.environ['FAKE_BYPASS']\n"
        "            for rule in data['rules']:\n"
        "                if rule['type'] == 'pull_request':\n"
        "                    rule['parameters']['required_approving_review_count'] = int(os.environ["
        "'FAKE_APPROVAL_COUNT'])\n"
        "                    rule['parameters']['require_code_owner_review'] = os.environ["
        "'FAKE_CODEOWNER_REVIEW'] == 'true'\n"
        "            if case == 'empty': data['bypass_actors'] = []\n"
        "            elif case == 'hidden': data.pop('bypass_actors', None)\n"
        "            elif case == 'null': data['bypass_actors'] = None\n"
        "            elif case == 'unexpected': data['bypass_actors'] = ["
        "{'actor_id': 5, 'actor_type': 'RepositoryRole', 'bypass_mode': 'always'}]\n"
        "        else:\n"
        "            marker.touch()\n"
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
        FAKE_VERIFY_MARKER=str(tmp_path / "post-apply-readback"),
        FAKE_BYPASS=bypass,
        FAKE_APPROVAL_COUNT=str(approval_count),
        FAKE_CODEOWNER_REVIEW=str(code_owner_review).lower(),
        FAKE_CODEOWNERS=CODEOWNERS_B64,
    )
    script = str(ROOT / ".github" / "scripts" / "setup-branch-protection.sh")
    subprocess.run(["bash", script], cwd=ROOT, env=env, input="", text=True, capture_output=True, check=True, timeout=5)
    before_check = len(log.read_text().splitlines())
    result = subprocess.run(["bash", script, "--check"], cwd=ROOT, env=env, text=True, capture_output=True, timeout=5)
    calls = [json.loads(line) for line in log.read_text().splitlines()[before_check:]]
    assert not any("--method" in call for call in calls)
    matches_policy = bypass == "empty" and approval_count == 1 and code_owner_review
    assert (result.returncode == 0) is matches_policy
    if bypass in ("hidden", "null"):
        assert "bypass_actors" in result.stdout
        assert "ruleset write access" in result.stdout
    elif not matches_policy:
        assert "Drift on" in result.stdout
    else:
        assert "one code-owner approval" in result.stdout


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


def _runs_under_bash(workflow, job, step):
    """GitHub's default shell is Bash, except PowerShell on Windows runners."""
    defaults = (job.get("defaults") or workflow.get("defaults") or {}).get("run") or {}
    shell = step.get("shell") or defaults.get("shell")
    if shell:
        return shell.split()[0] == "bash"
    return "windows" not in str(job.get("runs-on", "")).lower() and "== 'windows'" not in step.get("if", "").lower()


def test_run_step_shell_follows_github_defaults():
    linux, windows = {"runs-on": "ubuntu-latest"}, {"runs-on": "windows-latest"}
    assert _runs_under_bash({}, linux, {})
    assert not _runs_under_bash({}, windows, {})
    assert _runs_under_bash({}, windows, {"shell": "bash"})
    assert not _runs_under_bash({}, linux, {"shell": "pwsh"})
    assert not _runs_under_bash({}, linux, {"if": "runner.os == 'Windows'"})
    assert not _runs_under_bash({"defaults": {"run": {"shell": "pwsh"}}}, linux, {})


def test_every_bash_run_step_in_a_workflow_parses():
    # " #" starts a YAML comment in a plain scalar, which once cut the relay's echo in half.
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        workflow = _workflow(path.name)
        for job_name, job in workflow["jobs"].items():
            for step in job.get("steps", []):
                if "run" in step and _runs_under_bash(workflow, job, step):
                    parsed = subprocess.run(["bash", "-n"], input=step["run"], capture_output=True, text=True)
                    assert parsed.returncode == 0, f"{path.name} {job_name}: {parsed.stderr}"
