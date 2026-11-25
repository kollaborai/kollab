"""Keep the base-fresh workflow on trusted code with limited authority."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "base-fresh.yml"
SCRIPT = ROOT / "scripts" / "pr_blast_radius.py"


def test_base_fresh_workflow_contract():
    doc = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)

    events = doc["on"]
    assert set(events) == {"pull_request_target", "push", "workflow_dispatch"}
    assert set(events["pull_request_target"]["types"]) == {
        "opened",
        "synchronize",
        "reopened",
        "ready_for_review",
    }
    assert events["pull_request_target"]["branches"] == ["main"]
    assert events["push"]["branches"] == ["main"]
    dispatch_inputs = events["workflow_dispatch"]["inputs"]
    assert set(dispatch_inputs) == {"pr"}
    assert dispatch_inputs["pr"]["required"] == "false"
    assert dispatch_inputs["pr"]["type"] == "string"

    assert doc["permissions"] == {
        "contents": "read",
        "pull-requests": "read",
        "statuses": "write",
    }
    assert doc["concurrency"] == {
        "group": "base-fresh-${{ github.event.pull_request.number || 'main' }}",
        "cancel-in-progress": "true",
    }

    jobs = doc["jobs"]
    assert len(jobs) == 1
    steps = next(iter(jobs.values()))["steps"]
    checkouts = [
        step for step in steps if step.get("uses", "").startswith("actions/checkout@")
    ]
    assert len(checkouts) == 1
    assert checkouts[0]["with"] == {
        "ref": "${{ github.event.repository.default_branch }}",
        "persist-credentials": "false",
    }

    python_steps = [
        step for step in steps if step.get("uses", "").startswith("actions/setup-python@")
    ]
    assert len(python_steps) == 1
    assert python_steps[0]["with"]["python-version"] == "3.14"

    script_steps = [step for step in steps if "pr_blast_radius.py" in step.get("run", "")]
    assert len(script_steps) == 3
    assert SCRIPT.is_file()
    pr_event_step = next(step for step in script_steps if step["if"] == "github.event_name == 'pull_request_target'")
    dispatch_step = next(step for step in script_steps if "inputs.pr != ''" in step["if"])
    all_open_step = next(step for step in script_steps if "--all-open" in step["run"])
    assert pr_event_step["if"] == "github.event_name == 'pull_request_target'"
    assert dispatch_step["if"] == (
        "github.event_name == 'workflow_dispatch' && inputs.pr != ''"
    )
    assert all_open_step["if"] == (
        "github.event_name == 'push' || "
        "(github.event_name == 'workflow_dispatch' && !inputs.pr)"
    )
    assert "--pr" in pr_event_step["run"] and "--post" in pr_event_step["run"]
    assert "--pr" in dispatch_step["run"] and "--post" in dispatch_step["run"]
    assert "--all-open" in all_open_step["run"] and "--post" in all_open_step["run"]
    for step in script_steps:
        assert "${{" not in step["run"]
        assert step["env"]["GH_TOKEN"] == "${{ github.token }}"
        assert step["env"]["REPO"] == "${{ github.repository }}"
    assert pr_event_step["env"]["PR_NUMBER"] == "${{ github.event.pull_request.number }}"
    assert dispatch_step["env"]["PR_NUMBER"] == "${{ inputs.pr }}"
