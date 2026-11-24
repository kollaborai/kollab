import json

from scripts.main_red_issue import open_main_red_issue

RUN_SHA = "a" * 40


class FakeGh:
    def __init__(self, pull_requests=None):
        self.pull_requests = pull_requests or []
        self.issues = []
        self.issue_posts = []
        self.comment_posts = []

    def __call__(self, args):
        assert args[0] == "api", args
        path = args[1]
        if path == "repos/owner/repo/actions/runs/7":
            return json.dumps(
                {
                    "head_sha": RUN_SHA,
                    "html_url": "https://example.test/runs/7",
                }
            )
        if path == "repos/owner/repo/actions/runs/7/jobs?per_page=100":
            return json.dumps(
                {
                    "jobs": [
                        {"name": "Unit suite", "conclusion": "failure"},
                        {"name": "Docs", "conclusion": "success"},
                    ]
                }
            )
        if path == f"repos/owner/repo/commits/{RUN_SHA}/pulls?per_page=100":
            return json.dumps(self.pull_requests)
        if path == "repos/owner/repo/issues?state=open&per_page=100":
            return json.dumps(self.issues)
        if path == "repos/owner/repo/issues" and "--method" in args:
            fields = [
                args[index + 1]
                for index, value in enumerate(args[:-1])
                if value == "-f"
            ]
            payload = {}
            labels = []
            for field in fields:
                key, value = field.split("=", 1)
                if key == "labels[]":
                    labels.append(value)
                else:
                    payload[key] = value
            payload["labels"] = labels
            created = {
                "number": 91,
                "html_url": "https://example.test/issues/91",
                **payload,
            }
            self.issue_posts.append(created)
            self.issues.append(created)
            return json.dumps(created)
        if path == "repos/owner/repo/issues/42/comments" and "--method" in args:
            body = next(
                args[index + 1].split("=", 1)[1]
                for index, value in enumerate(args[:-1])
                if value == "-f" and args[index + 1].startswith("body=")
            )
            self.comment_posts.append(body)
            return json.dumps({"id": 1, "body": body})
        raise AssertionError(f"unexpected gh call: {args}")


def test_opens_one_issue_and_comments_on_the_related_pr_once():
    pull = {
        "number": 42,
        "html_url": "https://example.test/pull/42",
    }
    gh = FakeGh([pull])

    open_main_red_issue("owner/repo", "7", gh=gh)
    open_main_red_issue("owner/repo", "7", gh=gh)

    assert len(gh.issue_posts) == 1
    issue = gh.issue_posts[0]
    assert issue["title"] == f"main CI red at {RUN_SHA[:7]} after #42"
    assert set(issue["labels"]) == {"bug", "priority/p0", "area/ci"}
    assert f"<!-- main-red:{RUN_SHA} -->" in issue["body"]
    assert "https://example.test/runs/7" in issue["body"]
    assert "Unit suite" in issue["body"]
    assert "https://example.test/pull/42" in issue["body"]
    assert f"git revert -m 1 {RUN_SHA}" in issue["body"]
    assert len(gh.comment_posts) == 1
    assert "https://example.test/issues/91" in gh.comment_posts[0]


def test_opens_issue_without_a_pr_link_when_sha_has_no_pr():
    gh = FakeGh()

    open_main_red_issue("owner/repo", "7", gh=gh)

    assert len(gh.issue_posts) == 1
    issue = gh.issue_posts[0]
    assert issue["title"] == f"main CI red at {RUN_SHA[:7]}"
    assert "Pull request:" not in issue["body"]
    assert not gh.comment_posts


def test_workflow_runs_only_for_failed_main_ci_and_checks_out_the_default_branch():
    from pathlib import Path

    import yaml

    path = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "main-red.yml"
    wf = yaml.safe_load(path.read_text())
    triggers = wf.get("on") or wf[True]
    assert triggers["workflow_run"]["workflows"] == ["CI"]
    job = wf["jobs"]["open-issue"]
    for part in ("conclusion == 'failure'", "head_branch == 'main'", "'push'", "'workflow_dispatch'"):
        assert part in job["if"]
    assert job["permissions"] == {
        "contents": "read",
        "issues": "write",
        "pull-requests": "read",
        "actions": "read",
    }
    checkout = next(s for s in job["steps"] if s.get("uses", "").startswith("actions/checkout@"))
    assert checkout["with"]["ref"] == "${{ github.event.repository.default_branch }}"
    assert checkout["with"]["persist-credentials"] is False
    for step in job["steps"]:
        assert "${{" not in step.get("run", "")
