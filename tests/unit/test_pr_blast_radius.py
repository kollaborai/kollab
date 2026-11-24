from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "scripts" / "pr_blast_radius.py"
spec = importlib.util.spec_from_file_location("pr_blast_radius", SCRIPT)
pr_blast_radius = importlib.util.module_from_spec(spec)
sys.modules["pr_blast_radius"] = pr_blast_radius
spec.loader.exec_module(pr_blast_radius)


def write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_build_import_graph_resolves_python_and_frontend_imports(tmp_path: Path) -> None:
    write(tmp_path, "kollab/__init__.py", "")
    write(tmp_path, "kollab/core.py", "")
    write(tmp_path, "kollab/helpers.py", "")
    write(tmp_path, "kollab/nested/__init__.py", "")
    write(tmp_path, "kollab/nested/runner.py", """\
import kollab.core
from kollab import helpers
from . import sibling
from .sibling import deeper
""")
    write(tmp_path, "kollab/nested/sibling.py", "")
    write(tmp_path, "kollab/nested/sibling/deeper.py", "")
    write(tmp_path, "packages/kollabor-webui/frontend/src/app.tsx", "")
    write(tmp_path, "packages/kollabor-webui/frontend/src/components/view.tsx", "")
    write(tmp_path, "packages/kollabor-webui/frontend/src/components/raw.js", "")
    write(tmp_path, "packages/kollabor-webui/frontend/src/components/index.ts", "")
    write(
        tmp_path,
        "packages/kollabor-webui/frontend/src/entry.ts",
        "import raw from './components/raw.js';\n"
        "import view from './components/view';\n"
        "void import('./components');\n",
    )
    write(
        tmp_path,
        "packages/kollabor-webui/frontend/tests/app.test.ts",
        "import { view } from '../src/components/view';\n",
    )
    graph = pr_blast_radius.build_import_graph(tmp_path)

    assert {
        "kollab/core.py",
        "kollab/helpers.py",
        "kollab/nested/sibling.py",
        "kollab/nested/sibling/deeper.py",
    } <= graph["kollab/nested/runner.py"]
    assert graph["packages/kollabor-webui/frontend/tests/app.test.ts"] == {
        "packages/kollabor-webui/frontend/src/components/view.tsx"
    }
    assert graph["packages/kollabor-webui/frontend/src/entry.ts"] == {
        "packages/kollabor-webui/frontend/src/components/raw.js",
        "packages/kollabor-webui/frontend/src/components/view.tsx",
        "packages/kollabor-webui/frontend/src/components/index.ts",
    }


def test_reverse_closure_and_tests_of_follow_two_hops(tmp_path: Path) -> None:
    write(tmp_path, "packages/sample/src/sample/__init__.py", "")
    write(tmp_path, "packages/sample/src/sample/core.py", "")
    write(tmp_path, "packages/sample/src/sample/middle.py", "from . import core\n")
    write(tmp_path, "tests/unit/test_sample.py", "from sample import middle\n")
    graph = pr_blast_radius.build_import_graph(tmp_path)

    core = "packages/sample/src/sample/core.py"
    middle = "packages/sample/src/sample/middle.py"
    closure = pr_blast_radius.reverse_closure(graph, {core})
    assert closure == {
        core,
        middle,
        "tests/unit/test_sample.py",
    }
    assert pr_blast_radius.tests_of(graph, {core}) == {"tests/unit/test_sample.py"}


@pytest.mark.parametrize(
    ("pr_files", "main_files", "complete", "rule"),
    [
        ({"src/feature.py"}, {"uv.lock"}, True, "rule 1"),
        ({"src/feature.py"}, {"src/feature.py"}, True, "rule 2"),
        ({"tests/unit/test_feature.py"}, {"lib/shared.py"}, True, "rule 3"),
        (
            {"packages/kollabor-webui/frontend/src/view.tsx"},
            {"packages/kollabor-webui/frontend/src/app.tsx"},
            True,
            "rule 4",
        ),
        ({"src/feature.py"}, {"src/base.py"}, False, "rule 5"),
    ],
)
def test_each_stale_rule(
    pr_files: set[str], main_files: set[str], complete: bool, rule: str
) -> None:
    graph = {
        "tests/unit/test_feature.py": {"lib/shared.py"},
        "tests/unit/test_shared.py": {"lib/shared.py"},
    }

    stale, reasons = pr_blast_radius.decide(pr_files, main_files, graph, complete=complete)

    assert stale is True
    assert any(reason.startswith(rule) for reason in reasons)


@pytest.mark.parametrize(
    "main_path",
    [
        "pyproject.toml",
        "packages/sample/pyproject.toml",
        "uv.lock",
        "web/package.json",
        "web/package-lock.json",
        ".github/workflows/checks.yml",
        ".github/actions/setup/action.yml",
        "tests/conftest.py",
        "packages/sample/tests/conftest.py",
        "tests/tmux/lib/runner.py",
        ".python-version",
    ],
)
def test_each_global_path_is_stale(main_path: str) -> None:
    stale, reasons = pr_blast_radius.decide({"src/feature.py"}, {main_path}, {})

    assert stale is True
    assert reasons[0].startswith("rule 1:")


def test_disjoint_files_and_test_sets_are_fresh() -> None:
    graph = {
        "tests/unit/test_alpha.py": {"lib/alpha.py"},
        "tests/unit/test_beta.py": {"lib/beta.py"},
    }

    stale, reasons = pr_blast_radius.decide({"lib/alpha.py"}, {"lib/beta.py"}, graph)

    assert stale is False
    assert reasons == []


def test_changes_paths_are_ignored_on_both_sides() -> None:
    stale, reasons = pr_blast_radius.decide(
        {"changes/1414.fix.md"}, {"changes/other.docs.md"}, {}
    )

    assert stale is False
    assert reasons == []


def test_main_with_fake_fetcher_prints_expected_line(tmp_path: Path, capsys) -> None:
    calls: list[tuple[str, bool]] = []

    def fake_fetcher(endpoint, *, method="GET", fields=None, paginate=False):
        calls.append((endpoint, paginate))
        if endpoint == "repos/example/project/pulls/7":
            return {"head": {"sha": "head-sha"}, "base": {"ref": "main"}}
        if endpoint == "repos/example/project":
            return {"default_branch": "main"}
        if endpoint == "repos/example/project/compare/main...head-sha":
            return {"behind_by": 1, "merge_base_commit": {"sha": "merge-sha"}}
        if endpoint == "repos/example/project/pulls/7/files?per_page=100":
            return [{"filename": "scripts/local_tool.py", "status": "modified"}]
        if endpoint == "repos/example/project/compare/merge-sha...main":
            return {"files": [{"filename": "docs/guide.md", "status": "modified"}]}
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    result = pr_blast_radius.main(
        ["--repo", "example/project", "--pr", "7", "--root", str(tmp_path)],
        fetcher=fake_fetcher,
    )

    assert result == 0
    assert capsys.readouterr().out == "#7 FRESH no relevant test impact\n"
    assert any(paginate for _, paginate in calls)


def test_rename_includes_old_path_for_overlap() -> None:
    renamed_paths, complete = pr_blast_radius._changed_paths(
        [
            {
                "filename": "pkg/new.py",
                "previous_filename": "pkg/old.py",
                "status": "renamed",
            }
        ]
    )

    assert complete is True
    stale, reasons = pr_blast_radius.decide(
        {"pkg/old.py"}, set(renamed_paths), {}
    )

    assert stale is True
    assert any("pkg/old.py" in reason for reason in reasons)


def test_package_initializer_change_shares_submodule_test_impact(
    tmp_path: Path,
) -> None:
    initializer = "packages/sample/src/sample/__init__.py"
    core = "packages/sample/src/sample/core.py"
    write(tmp_path, initializer, "")
    write(tmp_path, core, "")
    write(tmp_path, "tests/unit/test_sample.py", "from sample import core\n")
    graph = pr_blast_radius.build_import_graph(tmp_path)

    stale, reasons = pr_blast_radius.decide({initializer}, {core}, graph)

    assert stale is True
    assert any(reason.startswith("rule 3:") for reason in reasons)


def test_graph_failure_posts_failure_for_each_listed_open_head(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    def fail_graph(root: Path) -> dict[str, set[str]]:
        raise RuntimeError("graph unavailable")

    monkeypatch.setattr(pr_blast_radius, "build_import_graph", fail_graph)
    status_posts: list[tuple[str, str, str]] = []

    def fake_fetcher(endpoint, *, method="GET", fields=None, paginate=False):
        if endpoint == "repos/example/project":
            return {"default_branch": "main"}
        if endpoint == "repos/example/project/pulls?state=open&per_page=100":
            return [
                {"number": 7, "base": {"ref": "main"}, "head": {"sha": "head-7"}},
                {"number": 8, "base": {"ref": "main"}, "head": {"sha": "head-8"}},
            ]
        if endpoint.startswith("repos/example/project/statuses/") and method == "POST":
            status_posts.append(
                (endpoint.rsplit("/", 1)[-1], fields["state"], fields["description"])
            )
            return {}
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    result = pr_blast_radius.main(
        [
            "--repo",
            "example/project",
            "--all-open",
            "--root",
            str(tmp_path),
            "--post",
        ],
        fetcher=fake_fetcher,
    )

    assert result == 1
    assert status_posts == [
        ("head-7", "failure", "blast-radius error: RuntimeError"),
        ("head-8", "failure", "blast-radius error: RuntimeError"),
    ]
    assert "blast-radius error: RuntimeError" in capsys.readouterr().err


def test_status_write_failures_return_nonzero_and_continue(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    status_attempts: list[tuple[str, str]] = []

    def fake_fetcher(endpoint, *, method="GET", fields=None, paginate=False):
        if endpoint == "repos/example/project":
            return {"default_branch": "main"}
        if endpoint == "repos/example/project/pulls?state=open&per_page=100":
            return [
                {"number": 7, "base": {"ref": "main"}, "head": {"sha": "head-7"}},
                {"number": 8, "base": {"ref": "main"}, "head": {"sha": "head-8"}},
            ]
        if endpoint in {
            "repos/example/project/pulls/7",
            "repos/example/project/pulls/8",
        }:
            head = endpoint.rsplit("/", 1)[-1]
            return {"head": {"sha": f"head-{head}"}, "base": {"ref": "main"}}
        if endpoint in {
            "repos/example/project/compare/main...head-7",
            "repos/example/project/compare/main...head-8",
        }:
            return {"behind_by": 0}
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    def fail_status(
        fetcher,
        repo: str,
        head_sha: str,
        state: str,
        context: str,
        description: str,
    ) -> None:
        status_attempts.append((head_sha, state))
        raise RuntimeError("status unavailable")

    monkeypatch.setattr(pr_blast_radius, "_post_status", fail_status)
    result = pr_blast_radius.main(
        [
            "--repo",
            "example/project",
            "--all-open",
            "--root",
            str(tmp_path),
            "--post",
        ],
        fetcher=fake_fetcher,
    )

    assert result == 1
    assert status_attempts == [
        ("head-7", "success"),
        ("head-7", "failure"),
        ("head-8", "success"),
        ("head-8", "failure"),
    ]
    assert capsys.readouterr().out.count("STALE") == 2


@pytest.mark.parametrize(
    ("behind_by", "expected_state", "expected_label", "expected_exit"),
    [
        (False, "failure", "STALE", 1),
        (0.5, "failure", "STALE", 1),
        (0, "success", "FRESH", 0),
    ],
)
def test_behind_by_requires_a_nonnegative_integer(
    tmp_path: Path,
    capsys,
    behind_by: object,
    expected_state: str,
    expected_label: str,
    expected_exit: int,
) -> None:
    status_posts: list[tuple[str, str]] = []

    def fake_fetcher(endpoint, *, method="GET", fields=None, paginate=False):
        if endpoint == "repos/example/project":
            return {"default_branch": "main"}
        if endpoint == "repos/example/project/pulls/7":
            return {"head": {"sha": "head-sha"}, "base": {"ref": "main"}}
        if endpoint == "repos/example/project/compare/main...head-sha":
            return {"behind_by": behind_by}
        if endpoint == "repos/example/project/statuses/head-sha" and method == "POST":
            status_posts.append(("head-sha", fields["state"]))
            return {}
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    result = pr_blast_radius.main(
        [
            "--repo",
            "example/project",
            "--pr",
            "7",
            "--root",
            str(tmp_path),
            "--post",
        ],
        fetcher=fake_fetcher,
    )

    assert result == expected_exit
    assert capsys.readouterr().out.startswith(f"#7 {expected_label} ")
    assert status_posts == [("head-sha", expected_state)]


def test_missing_base_ref_posts_failure_for_head_sha(
    tmp_path: Path, capsys
) -> None:
    status_posts: list[tuple[str, str]] = []

    def fake_fetcher(endpoint, *, method="GET", fields=None, paginate=False):
        if endpoint == "repos/example/project":
            return {"default_branch": "main"}
        if endpoint == "repos/example/project/pulls/7":
            return {"head": {"sha": "head-sha"}}
        if endpoint == "repos/example/project/statuses/head-sha" and method == "POST":
            status_posts.append(("head-sha", fields["state"]))
            return {}
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    result = pr_blast_radius.main(
        [
            "--repo",
            "example/project",
            "--pr",
            "7",
            "--root",
            str(tmp_path),
            "--post",
        ],
        fetcher=fake_fetcher,
    )

    assert result == 1
    assert "#7 STALE" in capsys.readouterr().out
    assert status_posts == [("head-sha", "failure")]


def test_all_open_missing_base_ref_posts_failure_and_returns_nonzero(
    tmp_path: Path, capsys
) -> None:
    status_posts: list[tuple[str, str]] = []

    def fake_fetcher(endpoint, *, method="GET", fields=None, paginate=False):
        if endpoint == "repos/example/project":
            return {"default_branch": "main"}
        if endpoint == "repos/example/project/pulls?state=open&per_page=100":
            return [{"number": 7, "head": {"sha": "head-sha"}}]
        if endpoint == "repos/example/project/pulls/7":
            return {"head": {"sha": "head-sha"}}
        if endpoint == "repos/example/project/statuses/head-sha" and method == "POST":
            status_posts.append(("head-sha", fields["state"]))
            return {}
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    result = pr_blast_radius.main(
        [
            "--repo",
            "example/project",
            "--all-open",
            "--root",
            str(tmp_path),
            "--post",
        ],
        fetcher=fake_fetcher,
    )

    assert result == 1
    assert "#7 STALE" in capsys.readouterr().out
    assert status_posts == [("head-sha", "failure")]


def test_importlib_loaded_test_tracks_script_path_expression(tmp_path: Path) -> None:
    write(tmp_path, "scripts/tool.py", "VALUE = 1\n")
    write(
        tmp_path,
        "tests/unit/test_dynamic_loader.py",
        "from pathlib import Path\n"
        "ROOT = Path(__file__).resolve().parents[2]\n"
        'spec = importlib.util.spec_from_file_location("tool", ROOT / "scripts" / "tool.py")\n',
    )

    graph = pr_blast_radius.build_import_graph(tmp_path)

    assert "scripts/tool.py" in graph["tests/unit/test_dynamic_loader.py"]


def test_star_import_tracks_initializer_and_direct_package_modules(tmp_path: Path) -> None:
    write(tmp_path, "sample_pkg/__init__.py", "__all__ = ['public']\n")
    write(tmp_path, "sample_pkg/public.py", "")
    write(tmp_path, "sample_pkg/private.py", "")
    write(tmp_path, "sample_pkg/nested/__init__.py", "")
    write(tmp_path, "sample_pkg/nested/deeper.py", "")
    write(tmp_path, "tests/unit/test_star_import.py", "from sample_pkg import *\n")

    graph = pr_blast_radius.build_import_graph(tmp_path)

    assert graph["tests/unit/test_star_import.py"] == {
        "sample_pkg/__init__.py",
        "sample_pkg/public.py",
        "sample_pkg/private.py",
        "sample_pkg/nested/__init__.py",
    }


def test_empty_changed_filename_marks_the_comparison_incomplete() -> None:
    paths, complete = pr_blast_radius._changed_paths(
        [{"filename": "", "status": "modified"}]
    )

    assert paths == []
    assert complete is False


@pytest.mark.parametrize("status", ["renamed", "copied"])
def test_renamed_or_copied_file_requires_previous_filename(status: str) -> None:
    paths, complete = pr_blast_radius._changed_paths(
        [{"filename": "pkg/new.py", "status": status}]
    )

    assert paths == ["pkg/new.py"]
    assert complete is False


def test_all_open_empty_default_branch_returns_nonzero(
    tmp_path: Path, capsys
) -> None:
    def fake_fetcher(endpoint, *, method="GET", fields=None, paginate=False):
        if endpoint == "repos/example/project":
            return {"default_branch": ""}
        if endpoint == "repos/example/project/pulls?state=open&per_page=100":
            return []
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    result = pr_blast_radius.main(
        [
            "--repo",
            "example/project",
            "--all-open",
            "--root",
            str(tmp_path),
        ],
        fetcher=fake_fetcher,
    )

    assert result == 1
    assert "STALE" not in capsys.readouterr().out


def test_all_open_empty_base_ref_is_kept_and_posts_failure(
    tmp_path: Path, capsys
) -> None:
    status_posts: list[tuple[str, str]] = []

    def fake_fetcher(endpoint, *, method="GET", fields=None, paginate=False):
        if endpoint == "repos/example/project":
            return {"default_branch": "main"}
        if endpoint == "repos/example/project/pulls?state=open&per_page=100":
            return [
                {
                    "number": 7,
                    "base": {"ref": ""},
                    "head": {"sha": "listed-head"},
                }
            ]
        if endpoint == "repos/example/project/pulls/7":
            return {"head": {"sha": "detail-head"}, "base": {"ref": ""}}
        if endpoint == "repos/example/project/statuses/detail-head" and method == "POST":
            status_posts.append((endpoint.rsplit("/", 1)[-1], fields["state"]))
            return {}
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    result = pr_blast_radius.main(
        [
            "--repo",
            "example/project",
            "--all-open",
            "--root",
            str(tmp_path),
            "--post",
        ],
        fetcher=fake_fetcher,
    )

    assert result == 1
    assert "#7 STALE" in capsys.readouterr().out
    assert status_posts == [("detail-head", "failure")]


def test_single_pr_fetches_default_branch_and_rejects_other_base(
    tmp_path: Path, capsys
) -> None:
    default_branch_calls = 0
    comparisons: list[str] = []
    status_posts: list[tuple[str, str]] = []

    def fake_fetcher(endpoint, *, method="GET", fields=None, paginate=False):
        nonlocal default_branch_calls
        if endpoint == "repos/example/project":
            default_branch_calls += 1
            return {"default_branch": "main"}
        if endpoint == "repos/example/project/pulls/7":
            return {"head": {"sha": "head-sha"}, "base": {"ref": "release"}}
        if endpoint.startswith("repos/example/project/compare/"):
            comparisons.append(endpoint)
            return {"behind_by": 0}
        if endpoint == "repos/example/project/statuses/head-sha" and method == "POST":
            status_posts.append(("head-sha", fields["state"]))
            return {}
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    result = pr_blast_radius.main(
        [
            "--repo",
            "example/project",
            "--pr",
            "7",
            "--root",
            str(tmp_path),
            "--post",
        ],
        fetcher=fake_fetcher,
    )

    assert result == 1
    assert default_branch_calls == 1
    assert comparisons == []
    assert "#7 STALE" in capsys.readouterr().out
    assert status_posts == [("head-sha", "failure")]


def test_pull_number_rejects_boolean() -> None:
    with pytest.raises(ValueError):
        pr_blast_radius._number(True)


def test_empty_head_sha_with_zero_behind_is_stale_and_posts_failure(
    tmp_path: Path, capsys
) -> None:
    comparisons: list[str] = []
    status_posts: list[tuple[str, str]] = []

    def fake_fetcher(endpoint, *, method="GET", fields=None, paginate=False):
        if endpoint == "repos/example/project":
            return {"default_branch": "main"}
        if endpoint == "repos/example/project/pulls?state=open&per_page=100":
            return [
                {
                    "number": 7,
                    "base": {"ref": "main"},
                    "head": {"sha": "listed-head"},
                }
            ]
        if endpoint == "repos/example/project/pulls/7":
            return {"head": {"sha": ""}, "base": {"ref": "main"}}
        if endpoint.startswith("repos/example/project/compare/"):
            comparisons.append(endpoint)
            return {"behind_by": 0}
        if endpoint.startswith("repos/example/project/statuses/") and method == "POST":
            status_posts.append((endpoint.rsplit("/", 1)[-1], fields["state"]))
            return {}
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    result = pr_blast_radius.main(
        [
            "--repo",
            "example/project",
            "--all-open",
            "--root",
            str(tmp_path),
            "--post",
        ],
        fetcher=fake_fetcher,
    )

    assert result == 1
    assert comparisons == []
    assert "#7 STALE" in capsys.readouterr().out
    assert status_posts == [("listed-head", "failure")]
