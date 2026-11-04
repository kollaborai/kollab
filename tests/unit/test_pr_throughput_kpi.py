"""Tests for the PR throughput KPI report (issue #729).

All GitHub access goes through an injected fake runner. No test touches the
network or the real `gh` binary; a runner that is not expected to be called
fails the test if it is.
"""

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / ".github" / "scripts" / "pr_throughput_kpi.py"


def _load():
    spec = importlib.util.spec_from_file_location("pr_throughput_kpi", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolves its module through sys.modules at class creation.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


kpi = _load()
NOW = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _pr(number, *, created=None, closed=None, merged=None, updated=None, **extra):
    created = created or NOW - timedelta(days=1)
    updated = updated or closed or created
    pr = {
        "number": number,
        "state": "closed" if closed else "open",
        "created_at": _iso(created),
        "updated_at": _iso(updated),
        "closed_at": _iso(closed) if closed else None,
        "merged_at": _iso(merged) if merged else None,
        "user": {"login": "agent-a"},
        "labels": [],
    }
    pr.update(extra)
    return pr


class FakeRunner:
    """Routes `gh api <path>` calls to canned JSON by path substring."""

    def __init__(self, routes=None, fail=None):
        self.routes = routes or {}
        self.fail = fail or set()
        self.calls = []

    def __call__(self, args):
        assert args[0] == "api", args
        path = args[1]
        self.calls.append(path)
        for needle in self.fail:
            if needle in path:
                raise kpi.GhError(f"gh api {path} failed: boom")
        for needle, payload in self.routes.items():
            if needle in path:
                return json.dumps(payload)
        raise AssertionError(f"unexpected gh call: {path}")


def _no_network():
    def runner(args):
        raise AssertionError(f"runner must not be called: {args}")

    return runner


# ---------------------------------------------------------------- config


def test_config_defaults_are_off_with_target_three():
    cfg = kpi.parse_config({})
    assert cfg.enabled is False
    assert cfg.target_per_hour == 3
    assert cfg.windows == (1, 8, 24)
    assert cfg.stale_days == 5


def test_config_reads_kpi_section_and_rejects_bad_values():
    cfg = kpi.parse_config(
        {
            "kpi": {
                "enabled": True,
                "repo": "o/r",
                "target_per_hour": 2.5,
                "windows": [2, 6],
            }
        }
    )
    assert cfg.enabled and cfg.repo == "o/r"
    assert cfg.target_per_hour == 2.5
    assert cfg.windows == (2, 6)
    with pytest.raises(kpi.ConfigError):
        kpi.parse_config({"kpi": {"enabled": True}})  # repo is required
    with pytest.raises(kpi.ConfigError):
        kpi.parse_config({"kpi": {"target_per_hour": 0}})
    with pytest.raises(kpi.ConfigError):
        kpi.parse_config({"kpi": {"windows": [0]}})


# ------------------------------------------------------- rolling rate


def test_rolling_rate_counts_closures_inside_each_window():
    prs = [
        _pr(1, closed=NOW - timedelta(minutes=10), merged=NOW - timedelta(minutes=10)),
        _pr(2, closed=NOW - timedelta(minutes=40)),
        _pr(3, closed=NOW - timedelta(hours=5), merged=NOW - timedelta(hours=5)),
        _pr(4, closed=NOW - timedelta(hours=30)),
    ]
    one = kpi.window_stats(prs, NOW, 1, 3.0, covered_since=None)
    eight = kpi.window_stats(prs, NOW, 8, 3.0, covered_since=None)
    day = kpi.window_stats(prs, NOW, 24, 3.0, covered_since=None)
    assert one["closed"] == 2 and one["rate_per_hour"] == 2.0
    assert eight["closed"] == 3 and eight["rate_per_hour"] == pytest.approx(3 / 8)
    assert day["closed"] == 3  # PR 4 closed 30h ago is outside 24h
    assert one["merged"] == 1 and one["closed_unmerged"] == 1


def test_window_boundary_is_open_at_start_and_closed_at_now():
    at_start = _pr(1, closed=NOW - timedelta(hours=1))
    at_now = _pr(2, closed=NOW)
    stats = kpi.window_stats([at_start, at_now], NOW, 1, 3.0, covered_since=None)
    assert stats["closed"] == 1


def test_meets_or_below_target_is_explicit():
    three = [_pr(i, closed=NOW - timedelta(minutes=i + 1)) for i in range(3)]
    met = kpi.window_stats(three, NOW, 1, 3.0, covered_since=None)
    assert met["status"] == "meets" and met["pct_of_target"] == pytest.approx(100.0)
    low = kpi.window_stats(three[:2], NOW, 1, 3.0, covered_since=None)
    assert low["status"] == "below"
    assert low["pct_of_target"] == pytest.approx(200 / 3)


def test_truncated_fetch_reports_missing_not_zero():
    # Only PRs updated after `covered_since` were fetched. A 1h window
    # starting after that point is fully covered; a 24h window is not.
    covered = NOW - timedelta(hours=2)
    prs = [_pr(1, closed=NOW - timedelta(minutes=5))]
    fresh = kpi.window_stats(prs, NOW, 1, 3.0, covered_since=covered)
    old = kpi.window_stats(prs, NOW, 24, 3.0, covered_since=covered)
    assert fresh["closed"] == 1
    assert old["closed"] is None and old["rate_per_hour"] is None
    assert old["status"] == "missing"


# ------------------------------------------------------ supporting metrics


def test_open_summary_counts_age_stale_conflicts_and_unknowns():
    open_prs = [
        _pr(
            10,
            created=NOW - timedelta(days=6),
            updated=NOW - timedelta(days=6),
            mergeable_state="dirty",
        ),
        _pr(
            11,
            created=NOW - timedelta(days=2),
            updated=NOW - timedelta(hours=1),
            mergeable_state="behind",
        ),
        _pr(
            12,
            created=NOW - timedelta(days=1),
            updated=NOW - timedelta(hours=2),
            mergeable_state="unknown",
        ),
    ]
    summary = kpi.open_stats(open_prs, NOW, stale_days=5)
    assert summary["open"] == 3
    assert summary["oldest_age_days"] == pytest.approx(6.0)
    assert summary["stale"] == 1
    assert summary["conflicted"] == 1
    assert summary["behind"] == 1
    assert summary["merge_state_unknown"] == 1


def test_open_summary_marks_unknown_merge_state_missing_when_not_fetched():
    open_prs = [_pr(20, created=NOW - timedelta(days=1))]  # no detail fetched
    summary = kpi.open_stats(open_prs, NOW, stale_days=5)
    assert summary["conflicted"] is None
    assert summary["behind"] is None


def test_time_to_first_review_ignores_pending_and_missing_is_none():
    pr = _pr(30, created=NOW - timedelta(hours=10), closed=NOW - timedelta(hours=1))
    reviews = [
        {"state": "PENDING", "submitted_at": None},
        {"state": "COMMENTED", "submitted_at": _iso(NOW - timedelta(hours=7))},
        {"state": "APPROVED", "submitted_at": _iso(NOW - timedelta(hours=8))},
    ]
    assert kpi.time_to_first_review_hours(pr, reviews) == pytest.approx(2.0)
    assert kpi.time_to_first_review_hours(pr, []) is None


def test_review_before_pr_creation_is_missing_not_zero():
    pr = _pr(31, created=NOW - timedelta(hours=1))
    reviews = [{"state": "COMMENTED", "submitted_at": _iso(NOW - timedelta(hours=5))}]
    assert kpi.time_to_first_review_hours(pr, reviews) is None


def test_review_rounds_count_change_requests_and_zero_is_real():
    reviews = [
        {"state": "CHANGES_REQUESTED", "submitted_at": _iso(NOW)},
        {"state": "COMMENTED", "submitted_at": _iso(NOW)},
        {"state": "CHANGES_REQUESTED", "submitted_at": _iso(NOW)},
    ]
    assert kpi.review_rounds(reviews) == 2
    assert kpi.review_rounds([]) == 0


def test_first_push_ci_verdict_pass_fail_or_missing():
    def run(conclusion, status="completed"):
        return {"status": status, "conclusion": conclusion}

    assert kpi.first_push_ci([run("success"), run("skipped")]) == "pass"
    assert kpi.first_push_ci([run("success"), run("failure")]) == "fail"
    assert kpi.first_push_ci([run("success"), run(None, "in_progress")]) is None
    assert kpi.first_push_ci([]) is None


def test_throughput_by_author_and_lane():
    closed = [
        _pr(1, closed=NOW - timedelta(minutes=5), labels=[{"name": "lane/relay"}]),
        _pr(2, closed=NOW - timedelta(minutes=6), user={"login": "agent-b"}),
        _pr(3, closed=NOW - timedelta(hours=20), labels=[{"name": "lane/relay"}]),
    ]
    by_author = kpi.throughput_by(closed, NOW, 24, kpi.author_key)
    assert by_author == {"agent-a": 2, "agent-b": 1}
    by_lane = kpi.throughput_by(closed, NOW, 1, kpi.lane_key(["lane/"]))
    assert by_lane == {"lane/relay": 1, "unassigned": 1}


# --------------------------------------------------------- GitHub access


def test_fetch_pulls_stops_at_updated_boundary_without_extra_pages():
    cutoff = NOW - timedelta(hours=24)
    page1 = [
        _pr(1, closed=NOW - timedelta(hours=1)),
        _pr(2, closed=NOW - timedelta(hours=30), updated=NOW - timedelta(hours=30)),
    ]
    runner = FakeRunner(
        {"state=closed&sort=updated&direction=desc&per_page=100&page=1": page1}
    )
    api = kpi.GhApi(runner)
    prs, covered = kpi.fetch_pulls(
        api, "o/r", "closed", max_pages=10, stop_before=cutoff
    )
    assert [p["number"] for p in prs] == [1, 2]
    assert covered is None  # boundary reached: everything in range is covered
    assert len(runner.calls) == 1


def test_fetch_pulls_truncates_at_max_pages_and_reports_coverage():
    def full_page(start_minute):
        return [
            _pr(
                n, closed=NOW - timedelta(minutes=n), updated=NOW - timedelta(minutes=n)
            )
            for n in range(start_minute, start_minute + kpi.PER_PAGE)
        ]

    page1, page2 = full_page(1), full_page(101)
    runner = FakeRunner({"&page=1": page1, "&page=2": page2})
    api = kpi.GhApi(runner)
    prs, covered = kpi.fetch_pulls(
        api, "o/r", "closed", max_pages=2, stop_before=NOW - timedelta(hours=24)
    )
    assert len(prs) == 2 * kpi.PER_PAGE
    assert len(runner.calls) == 2
    assert covered == NOW - timedelta(minutes=200)  # oldest updated_at fetched


def test_enrich_closed_paginates_reviews_and_check_runs():
    pr = _pr(
        5,
        created=NOW - timedelta(hours=3),
        closed=NOW - timedelta(minutes=30),
        merged=NOW - timedelta(minutes=30),
    )
    review_page_1 = [
        {
            "id": f"review-{index}",
            "state": "COMMENTED",
            "submitted_at": _iso(NOW - timedelta(hours=2)),
        }
        for index in range(kpi.PER_PAGE)
    ]
    review_page_2 = [
        {
            "id": "review-last",
            "state": "CHANGES_REQUESTED",
            "submitted_at": _iso(NOW - timedelta(hours=1)),
        }
    ]
    check_page_1 = [
        {"id": f"check-{index}", "status": "completed", "conclusion": "success"}
        for index in range(kpi.PER_PAGE)
    ]
    check_page_2 = [
        {"id": "check-last", "status": "completed", "conclusion": "failure"}
    ]
    routes = {
        "pulls/5/reviews?per_page=100&page=1": review_page_1,
        "pulls/5/reviews?per_page=100&page=2": review_page_2,
        "pulls/5/commits": [{"sha": "abc", "commit": {}}],
        "commits/abc/check-runs?per_page=100&page=1": {
            "total_count": kpi.PER_PAGE + 1,
            "check_runs": check_page_1,
        },
        "commits/abc/check-runs?per_page=100&page=2": {
            "total_count": kpi.PER_PAGE + 1,
            "check_runs": check_page_2,
        },
    }
    runner = FakeRunner(routes)
    errors = []

    result = kpi._enrich_closed(kpi.GhApi(runner), "o/r", [pr], errors)

    assert errors == []
    assert result["time_to_first_review_hours_median"] == pytest.approx(1.0)
    assert result["review_rounds_per_pr"] == pytest.approx(1.0)
    assert result["first_push_pass_rate"] == 0.0
    assert result["first_push_checked"] == 1
    assert any("pulls/5/reviews" in path and "page=2" in path for path in runner.calls)
    assert any("check-runs" in path and "page=2" in path for path in runner.calls)
    # One page of commits means one request; longer lists page like the others.
    assert sum("pulls/5/commits" in path for path in runner.calls) == 1


@pytest.mark.parametrize("target", ["reviews", "check-runs"])
def test_enrich_closed_degrades_only_the_pr_with_pagination_error(target):
    def runner(args):
        path = args[1]
        if "/reviews?" in path:
            pr_number = path.split("/pulls/")[1].split("/")[0]
            if target == "reviews" and pr_number == "5":
                return json.dumps({"not": "a review list"})
            return json.dumps(
                [
                    {
                        "id": f"review-{pr_number}",
                        "state": "CHANGES_REQUESTED",
                        "submitted_at": _iso(NOW - timedelta(hours=2)),
                    }
                ]
            )
        if "/commits?" in path:
            pr_number = path.split("/pulls/")[1].split("/")[0]
            return json.dumps([{"sha": f"sha-{pr_number}", "commit": {}}])
        if "/check-runs?" in path:
            sha = path.split("/commits/")[1].split("/")[0]
            if target == "check-runs" and sha == "sha-5":
                return json.dumps({"check_runs": []})
            return json.dumps(
                {
                    "total_count": 1,
                    "check_runs": [
                        {"id": sha, "status": "completed", "conclusion": "success"}
                    ],
                }
            )
        raise AssertionError(f"unexpected gh call: {path}")

    errors = []
    result = kpi._enrich_closed(
        kpi.GhApi(runner),
        "o/r",
        [
            _pr(5, created=NOW - timedelta(hours=3)),
            _pr(6, created=NOW - timedelta(hours=3)),
        ],
        errors,
    )

    assert result["time_to_first_review_hours_median"] == pytest.approx(1.0)
    assert result["review_rounds_per_pr"] == pytest.approx(1.0)
    assert result["first_push_pass_rate"] == 1.0
    assert result["first_push_checked"] == (2 if target == "reviews" else 1)
    assert len(errors) == 1
    assert errors[0].startswith("PR #5")
    assert "failing closed" in errors[0]


@pytest.mark.parametrize("target", ["reviews", "check-runs"])
def test_enrich_closed_fails_closed_on_repeated_page(target):
    repeated_reviews = [
        {
            "id": f"review-{index}",
            "state": "COMMENTED",
            "submitted_at": _iso(NOW - timedelta(hours=1)),
        }
        for index in range(kpi.PER_PAGE)
    ]
    repeated_checks = [
        {"id": f"check-{index}", "status": "completed", "conclusion": "success"}
        for index in range(kpi.PER_PAGE)
    ]
    runner_calls = []

    def runner(args):
        path = args[1]
        runner_calls.append(path)
        if "/pulls/5/reviews?" in path:
            return json.dumps(repeated_reviews if target == "reviews" else [])
        if "/pulls/5/commits?" in path:
            return json.dumps([{"sha": "abc", "commit": {}}])
        if "/check-runs?" in path:
            if target == "reviews":
                return json.dumps({"total_count": 0, "check_runs": []})
            return json.dumps(
                {"total_count": 2 * kpi.PER_PAGE, "check_runs": repeated_checks}
            )
        raise AssertionError(f"unexpected gh call: {path}")

    errors = []
    result = kpi._enrich_closed(kpi.GhApi(runner), "o/r", [_pr(5)], errors)

    repeated_endpoint = "pulls/5/reviews" if target == "reviews" else "check-runs"
    assert sum(repeated_endpoint in path for path in runner_calls) == 2
    assert any(repeated_endpoint in error and "identical" in error for error in errors)
    if target == "reviews":
        assert result["review_rounds_per_pr"] is None
    else:
        assert result["first_push_pass_rate"] is None
        assert result["first_push_checked"] == 0


@pytest.mark.parametrize("target", ["reviews", "check-runs"])
def test_enrich_closed_fails_closed_at_page_cap(target, monkeypatch):
    monkeypatch.setattr(kpi, "PAGINATION_MAX_PAGES", 2, raising=False)
    runner_calls = []

    def runner(args):
        path = args[1]
        runner_calls.append(path)
        page = int(path.rsplit("page=", 1)[1]) if "page=" in path else 1
        if "/pulls/5/reviews?" in path:
            if target != "reviews":
                return json.dumps([])
            return json.dumps(
                [
                    {
                        "id": f"review-{page}-{index}",
                        "state": "COMMENTED",
                        "submitted_at": _iso(NOW - timedelta(hours=1)),
                    }
                    for index in range(kpi.PER_PAGE)
                ]
            )
        if "/pulls/5/commits?" in path:
            return json.dumps([{"sha": "abc", "commit": {}}])
        if "/check-runs?" in path:
            if target == "reviews":
                return json.dumps({"total_count": 0, "check_runs": []})
            return json.dumps(
                {
                    "total_count": 10 * kpi.PER_PAGE,
                    "check_runs": [
                        {
                            "id": f"check-{page}-{index}",
                            "status": "completed",
                            "conclusion": "success",
                        }
                        for index in range(kpi.PER_PAGE)
                    ],
                }
            )
        raise AssertionError(f"unexpected gh call: {path}")

    errors = []
    result = kpi._enrich_closed(kpi.GhApi(runner), "o/r", [_pr(5)], errors)

    capped_endpoint = "pulls/5/reviews" if target == "reviews" else "check-runs"
    assert sum(capped_endpoint in path for path in runner_calls) == 2
    assert any(
        capped_endpoint in error and "failing closed" in error for error in errors
    )
    if target == "reviews":
        assert result["review_rounds_per_pr"] is None
    else:
        assert result["first_push_pass_rate"] is None
        assert result["first_push_checked"] == 0


@pytest.mark.parametrize("target", ["reviews", "check-runs"])
def test_enrich_closed_fails_closed_on_malformed_page(target):
    def runner(args):
        path = args[1]
        if "/pulls/5/reviews?" in path:
            return json.dumps({"not": "a review list"} if target == "reviews" else [])
        if "/pulls/5/commits?" in path:
            return json.dumps([{"sha": "abc", "commit": {}}])
        if "/check-runs?" in path:
            if target == "reviews":
                return json.dumps({"total_count": 0, "check_runs": []})
            return json.dumps({"check_runs": []})
        raise AssertionError(f"unexpected gh call: {path}")

    errors = []
    result = kpi._enrich_closed(kpi.GhApi(runner), "o/r", [_pr(5)], errors)

    endpoint = "pulls/5/reviews" if target == "reviews" else "check-runs"
    assert any(endpoint in error and "failing closed" in error for error in errors)
    if target == "reviews":
        assert result["review_rounds_per_pr"] is None
    else:
        assert result["first_push_pass_rate"] is None
        assert result["first_push_checked"] == 0


@pytest.mark.parametrize("target", ["reviews", "check-runs"])
def test_enrich_closed_discards_partial_metric_when_later_page_fails(target):
    full_reviews = [
        {
            "id": f"review-{index}",
            "state": "CHANGES_REQUESTED",
            "submitted_at": _iso(NOW - timedelta(hours=1)),
        }
        for index in range(kpi.PER_PAGE)
    ]
    full_checks = [
        {"id": f"check-{index}", "status": "completed", "conclusion": "success"}
        for index in range(kpi.PER_PAGE)
    ]
    errors = []

    def runner(args):
        path = args[1]
        if "/pulls/5/reviews?" in path:
            if target != "reviews":
                return json.dumps([])
            if "&page=1" in path or "&page=" not in path:
                return json.dumps(full_reviews)
            raise kpi.GhError("reviews page 2 failed")
        if "/pulls/5/commits?" in path:
            return json.dumps([{"sha": "abc", "commit": {}}])
        if "/check-runs?" in path:
            if "&page=1" in path or "&page=" not in path:
                return json.dumps(
                    {"total_count": kpi.PER_PAGE + 1, "check_runs": full_checks}
                )
            raise kpi.GhError("check-runs page 2 failed")
        raise AssertionError(f"unexpected gh call: {path}")

    result = kpi._enrich_closed(kpi.GhApi(runner), "o/r", [_pr(5)], errors)

    if target == "reviews":
        assert result["review_rounds_per_pr"] is None
        assert any("reviews page 2 failed" in error for error in errors)
    else:
        assert result["first_push_pass_rate"] is None
        assert result["first_push_checked"] == 0
        assert any("check-runs page 2 failed" in error for error in errors)


def test_enrich_closed_judges_the_earliest_commit_ci_ran_on():
    closed = NOW - timedelta(minutes=30)
    pr = _pr(5, created=NOW - timedelta(hours=3), closed=closed, merged=closed)
    runner = FakeRunner(
        {
            "pulls/5/reviews": [],
            "pulls/5/commits": [
                {"sha": "early", "commit": {}},
                {"sha": "pushed", "commit": {}},
                {"sha": "later", "commit": {}},
            ],
            "commits/early/check-runs": {"total_count": 0, "check_runs": []},
            "commits/pushed/check-runs": {
                "total_count": 1,
                "check_runs": [{"status": "completed", "conclusion": "failure"}],
            },
            "commits/later/check-runs": {
                "total_count": 1,
                "check_runs": [{"status": "completed", "conclusion": "success"}],
            },
        }
    )
    errors = []

    result = kpi._enrich_closed(kpi.GhApi(runner), "o/r", [pr], errors)

    assert errors == []
    assert result["first_push_pass_rate"] == 0.0
    assert result["first_push_checked"] == 1


def test_enrich_closed_finds_the_first_pushed_head_past_page_one():
    closed = NOW - timedelta(minutes=30)
    pr = _pr(5, created=NOW - timedelta(hours=3), closed=closed, merged=closed)
    page_1 = [{"sha": f"early{n}", "commit": {}} for n in range(kpi.PER_PAGE)]
    runner = FakeRunner(
        {
            "pulls/5/reviews": [],
            "pulls/5/commits?per_page=100&page=1": page_1,
            "pulls/5/commits?per_page=100&page=2": [{"sha": "pushed", "commit": {}}],
            "commits/pushed/check-runs": {
                "total_count": 1,
                "check_runs": [{"status": "completed", "conclusion": "failure"}],
            },
            "/check-runs": {"total_count": 0, "check_runs": []},
        }
    )
    errors = []

    result = kpi._enrich_closed(kpi.GhApi(runner), "o/r", [pr], errors)

    assert errors == []
    assert result["first_push_pass_rate"] == 0.0
    assert result["first_push_checked"] == 1


def test_collect_says_when_max_pages_cut_off_the_lists():
    cfg = kpi.parse_config({"kpi": {"enabled": True, "repo": "o/r", "max_pages": 1}})
    recent = NOW - timedelta(minutes=30)
    created = NOW - timedelta(hours=3)
    closed = [
        _pr(n, created=created, closed=recent, merged=recent, updated=recent)
        for n in range(1, kpi.PER_PAGE + 1)
    ]
    still_open = [
        _pr(n, created=created, updated=recent)
        for n in range(1000, 1000 + kpi.PER_PAGE)
    ]
    runner = FakeRunner(
        {
            "pulls?state=closed": closed,
            "pulls?state=open": still_open,
            "/reviews": [],
            "/commits": [],
            "/pulls/": {"mergeable_state": "clean"},
        }
    )

    report = kpi.collect(kpi.GhApi(runner), cfg, NOW)

    assert report["status"] == "partial"
    assert any("open PR list" in error for error in report["errors"])
    assert any("closed PR list" in error for error in report["errors"])


def test_gh_failure_yields_unavailable_report_with_missing_metrics():
    cfg = kpi.parse_config({"kpi": {"enabled": True, "repo": "o/r"}})
    runner = FakeRunner(fail={"/pulls?"})
    report = kpi.collect(kpi.GhApi(runner), cfg, NOW)
    assert report["status"] == "unavailable"
    assert report["windows"][0]["rate_per_hour"] is None
    assert report["open"]["open"] is None
    assert any("boom" in e for e in report["errors"])
    md = kpi.render_markdown(report)
    assert "missing" in md and "0.00" not in md


# ------------------------------------------------------ report and output


def test_collect_builds_full_report_from_mocked_api():
    cfg = kpi.parse_config({"kpi": {"enabled": True, "repo": "o/r"}})
    closed = [
        _pr(
            5,
            created=NOW - timedelta(hours=3),
            closed=NOW - timedelta(minutes=30),
            merged=NOW - timedelta(minutes=30),
        ),
    ]
    opened = [_pr(6, created=NOW - timedelta(days=6), updated=NOW - timedelta(days=6))]
    runner = FakeRunner(
        {
            "state=closed": closed,
            "state=open": opened,
            "pulls/5/reviews": [
                {
                    "state": "CHANGES_REQUESTED",
                    "submitted_at": _iso(NOW - timedelta(hours=2)),
                }
            ],
            "pulls/5/commits": [{"sha": "abc", "commit": {}}],
            "commits/abc/check-runs": {
                "total_count": 1,
                "check_runs": [{"status": "completed", "conclusion": "success"}],
            },
            "pulls/6": {"mergeable_state": "dirty"},
        }
    )
    report = kpi.collect(kpi.GhApi(runner), cfg, NOW)
    assert report["status"] == "ok"
    assert report["windows"][0]["closed"] == 1
    assert report["windows"][0]["merged"] == 1
    assert report["review"]["time_to_first_review_hours_median"] == pytest.approx(1.0)
    assert report["review"]["review_rounds_per_pr"] == pytest.approx(1.0)
    assert report["ci"]["first_push_pass_rate"] == pytest.approx(1.0)
    assert report["open"]["stale"] == 1
    assert report["open"]["conflicted"] == 1


def test_disabled_config_makes_no_gh_calls(tmp_path, capsys):
    cfg_path = tmp_path / "kpi.json"
    cfg_path.write_text(json.dumps({"kpi": {"enabled": False}}))
    rc = kpi.main(["--config", str(cfg_path)], runner=_no_network())
    assert rc == 0
    assert "disabled" in capsys.readouterr().out


def test_main_writes_json_markdown_and_step_summary(tmp_path, monkeypatch):
    cfg_path = tmp_path / "kpi.json"
    cfg_path.write_text(json.dumps({"kpi": {"enabled": True, "repo": "o/r"}}))
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    runner = FakeRunner({"state=closed": [], "state=open": []})
    json_out = tmp_path / "kpi.json.out"
    md_out = tmp_path / "dashboard.md"
    rc = kpi.main(
        [
            "--config",
            str(cfg_path),
            "--json-out",
            str(json_out),
            "--markdown-out",
            str(md_out),
            "--now",
            _iso(NOW),
        ],
        runner=runner,
    )
    assert rc == 0
    report = json.loads(json_out.read_text())
    assert report["status"] == "ok"
    assert report["windows"][0]["closed"] == 0  # real zero, not missing
    assert "PR throughput" in md_out.read_text()
    assert "PR throughput" in summary.read_text()


def test_render_markdown_states_target_and_all_windows():
    cfg = kpi.parse_config({"kpi": {"enabled": True, "repo": "o/r"}})
    report = kpi.collect(
        kpi.GhApi(FakeRunner({"state=closed": [], "state=open": []})), cfg, NOW
    )
    md = kpi.render_markdown(report)
    assert "target 3.0/h" in md
    for hours in (1, 8, 24):
        assert f"| {hours}h |" in md


def test_markdown_shows_unknown_merge_states():
    """GitHub computes mergeable_state lazily. A count of 0 next to unknowns is not a real 0."""
    report = {
        "target_per_hour": 3.0,
        "windows": [],
        "open": {
            "open": 2,
            "oldest_age_days": 1.0,
            "stale_days_threshold": 5,
            "stale": 0,
            "conflicted": 0,
            "behind": 0,
            "merge_state_unknown": 2,
        },
    }
    assert "Merge state unknown: 2" in kpi.render_markdown(report)
