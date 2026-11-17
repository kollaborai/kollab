"""The overlap check must run for every open/push lifecycle event."""

from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
WORKFLOW = REPO / ".github/workflows/pr-overlap.yml"


def test_workflow_uses_trusted_pull_request_target_events_and_comment_scope():
    workflow = WORKFLOW.read_text()

    assert "pull_request_target:" in workflow
    assert "types: [opened, reopened, synchronize]" in workflow
    assert "issues: write" in workflow
    assert "pull-requests: write" in workflow
