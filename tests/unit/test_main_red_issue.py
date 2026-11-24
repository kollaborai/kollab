import json

from scripts.main_red_issue import open_main_red_issue

RUN_SHA = "a" * 40


class FakeGh:
    def __init__(self, pull_requests=None):
        self.pull_requests = pull_requests or []
        self.issues = []
        self.closed_issues = []
        self.issue_posts = []
        self.comment_posts = []
        self.fail_comment_once = False

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
        if path == "repos/owner/repo/issues?state=all&per_page=100":
            return json.dumps(self.issues + self.closed_issues)
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
        if path == "repos/owner/repo/issues/42/comments?per_page=100":
            return json.dumps([{"body": body} for body in self.comment_posts])
        if path == "repos/owner/repo/issues/42/comments" and "--method" in args:
            if self.fail_comment_once:
                self.fail_comment_once = False
                raise RuntimeError("comment api failed once")
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


def test_closed_issue_still_deduplicates_the_same_commit():
    gh = FakeGh()

    open_main_red_issue("owner/repo", "7", gh=gh)
    gh.closed_issues.extend(gh.issues)
    gh.issues.clear()
    open_main_red_issue("owner/repo", "7", gh=gh)

    assert len(gh.issue_posts) == 1
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


def test_workflow_serializes_runs_for_one_commit():
    from pathlib import Path

    import yaml

    path = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "main-red.yml"
    wf = yaml.safe_load(path.read_text())
    group = wf["jobs"]["open-issue"]["concurrency"]
    assert group["group"] == "main-red-${{ github.event.workflow_run.head_sha }}"
    assert group["cancel-in-progress"] is False


def test_overlapping_runs_for_one_commit_leave_one_open_issue():
    import threading

    barrier = threading.Barrier(2)
    lock = threading.Lock()

    class RacingGh(FakeGh):
        def __init__(self):
            super().__init__()
            self.next_number = 100
            self.first_reads = 0

        def __call__(self, args):
            path = args[1]
            if path == "repos/owner/repo/issues?state=all&per_page=100":
                with lock:
                    self.first_reads += 1
                    gate = self.first_reads <= 2
                snapshot = super().__call__(args)
                if gate:
                    barrier.wait(timeout=5)
                return snapshot
            if path == "repos/owner/repo/issues" and "--method" in args:
                with lock:
                    self.next_number += 1
                    number = self.next_number
                result = json.loads(super().__call__(args))
                result["number"] = number
                result["html_url"] = f"https://example.test/issues/{number}"
                with lock:
                    self.issues[-1].update(result)
                return json.dumps(result)
            if path.startswith("repos/owner/repo/issues/") and "--method" in args and "PATCH" in args:
                number = int(path.rsplit("/", 1)[1])
                with lock:
                    for issue in self.issues:
                        if issue["number"] == number:
                            issue["state"] = "closed"
                return json.dumps({"number": number, "state": "closed"})
            return super().__call__(args)

    gh = RacingGh()
    threads = [
        threading.Thread(target=open_main_red_issue, args=("owner/repo", "7"), kwargs={"gh": gh})
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    open_issues = [issue for issue in gh.issues if issue.get("state") != "closed"]
    assert len(open_issues) == 1
    assert open_issues[0]["number"] == min(issue["number"] for issue in gh.issues)


def test_retry_after_a_failed_comment_still_posts_the_pr_comment_once():
    gh = FakeGh([{"number": 42, "html_url": "https://example.test/pull/42"}])
    gh.fail_comment_once = True

    try:
        open_main_red_issue("owner/repo", "7", gh=gh)
    except RuntimeError:
        pass
    assert len(gh.issue_posts) == 1
    assert gh.comment_posts == []

    open_main_red_issue("owner/repo", "7", gh=gh)
    open_main_red_issue("owner/repo", "7", gh=gh)

    assert len(gh.issue_posts) == 1
    assert len(gh.comment_posts) == 1
