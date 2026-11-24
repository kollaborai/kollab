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


def _pull_detail(head_sha: str, files: list[dict[str, str]]) -> dict[str, object]:
    return {
        "head": {"sha": head_sha},
        "base": {"ref": "main"},
        "changed_files": len(files),
    }


def _comparison_fetcher(
    pr_files: list[dict[str, object]],
    main_files: list[dict[str, object]],
    changed_files: int | None = None,
):
    pull = {
        "head": {"sha": "head-sha"},
        "base": {"ref": "main"},
        "changed_files": len(pr_files) if changed_files is None else changed_files,
    }

    def fake_fetcher(endpoint, *, method="GET", fields=None, paginate=False):
        if endpoint == "repos/example/project/pulls/7":
            return pull
        if endpoint == "repos/example/project":
            return {"default_branch": "main"}
        if endpoint == "repos/example/project/compare/main...head-sha":
            return {"behind_by": 1, "merge_base_commit": {"sha": "merge-sha"}}
        if endpoint == "repos/example/project/pulls/7/files?per_page=100":
            return pr_files
        if endpoint == "repos/example/project/compare/merge-sha...main":
            return {"files": main_files}
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    return fake_fetcher


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


def test_literal_runtime_imports_add_python_edges(tmp_path: Path) -> None:
    write(tmp_path, "pkg/__init__.py", "")
    write(tmp_path, "pkg/mod.py", "")
    write(tmp_path, "pkg/other.py", "")
    write(
        tmp_path,
        "tests/unit/test_runtime_import.py",
        "import importlib\n"
        'importlib.import_module("pkg.mod")\n'
        '__import__("pkg.other")\n',
    )

    graph = pr_blast_radius.build_import_graph(tmp_path)

    assert graph["tests/unit/test_runtime_import.py"] == {
        "pkg/mod.py",
        "pkg/other.py",
    }


def test_dynamic_directory_loader_makes_comparison_stale(tmp_path: Path) -> None:
    loader = "plugins/loader.py"
    write(
        tmp_path,
        loader,
        'from pathlib import Path\n'
        'for path in Path("plugins").glob("*.py"):\n'
        "    pass\n",
    )
    dynamic: set[str] = set()
    graph = pr_blast_radius.build_import_graph(tmp_path, dynamic=dynamic)

    stale, reasons = pr_blast_radius.decide(
        {loader}, {"plugins/new_plugin.py"}, graph, dynamic=dynamic
    )

    assert stale is True
    assert reasons[0] == f"rule 5: cannot analyze dynamic loading in {loader}"


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
    pr_files = [
        {
            "filename": "scripts/local_tool.py",
            "status": "modified",
            "patch": "",
        }
    ]

    def fake_fetcher(endpoint, *, method="GET", fields=None, paginate=False):
        calls.append((endpoint, paginate))
        if endpoint == "repos/example/project/pulls/7":
            return _pull_detail("head-sha", pr_files)
        if endpoint == "repos/example/project":
            return {"default_branch": "main"}
        if endpoint == "repos/example/project/compare/main...head-sha":
            return {"behind_by": 1, "merge_base_commit": {"sha": "merge-sha"}}
        if endpoint == "repos/example/project/pulls/7/files?per_page=100":
            return pr_files
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


def test_pr_file_list_requires_exact_unique_records(
    tmp_path: Path, capsys
) -> None:
    cases = [
        (
            [
                {"filename": "src/one.txt", "status": "modified"},
                {"filename": "src/two.txt", "status": "modified"},
            ],
            1,
        ),
        (
            [
                {"filename": "src/one.txt", "status": "modified"},
                {"filename": "src/one.txt", "status": "modified"},
            ],
            2,
        ),
    ]
    for pr_files, changed_files in cases:
        result = pr_blast_radius.main(
            ["--repo", "example/project", "--pr", "7", "--root", str(tmp_path)],
            fetcher=_comparison_fetcher(pr_files, [], changed_files),
        )

        output = capsys.readouterr().out
        assert result == 1
        assert "#7 STALE" in output
        assert "rule 5:" in output


def test_patch_import_connects_new_pr_test_to_main_change(
    tmp_path: Path, capsys
) -> None:
    write(tmp_path, "pkg/__init__.py", "")
    write(tmp_path, "pkg/core.py", "VALUE = 1\n")
    pr_files: list[dict[str, object]] = [
        {
            "filename": "tests/unit/test_added.py",
            "status": "added",
            "patch": "@@ -0,0 +1 @@\n+from pkg import core\n",
        }
    ]
    main_files: list[dict[str, object]] = [
        {"filename": "pkg/core.py", "status": "modified"}
    ]

    result = pr_blast_radius.main(
        ["--repo", "example/project", "--pr", "7", "--root", str(tmp_path)],
        fetcher=_comparison_fetcher(pr_files, main_files),
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "#7 STALE" in output
    assert "rule 3:" in output


def test_patch_parses_imports_from_every_added_python_statement(
    tmp_path: Path, capsys
) -> None:
    write(tmp_path, "app/__init__.py", "")
    write(tmp_path, "app/core.py", "VALUE = 1\n")
    pr_files: list[dict[str, object]] = [
        {
            "filename": "tests/unit/test_added.py",
            "status": "added",
            "patch": (
                "@@ -0,0 +1,2 @@\n"
                "+if ready: import app.core\n"
                '+module = importlib.import_module("app.core")\n'
            ),
        }
    ]

    result = pr_blast_radius.main(
        ["--repo", "example/project", "--pr", "7", "--root", str(tmp_path)],
        fetcher=_comparison_fetcher(
            pr_files, [{"filename": "app/core.py", "status": "modified"}]
        ),
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "#7 STALE" in output
    assert "rule 3:" in output


def test_literal_runtime_import_in_patch_includes_package_initializers(
    tmp_path: Path, capsys
) -> None:
    write(tmp_path, "app/__init__.py", "")
    write(tmp_path, "app/core.py", "VALUE = 1\n")
    pr_files: list[dict[str, object]] = [
        {
            "filename": "tests/unit/test_added.py",
            "status": "added",
            "patch": '+module = importlib.import_module("app.core")\n',
        }
    ]

    result = pr_blast_radius.main(
        ["--repo", "example/project", "--pr", "7", "--root", str(tmp_path)],
        fetcher=_comparison_fetcher(
            pr_files, [{"filename": "app/__init__.py", "status": "modified"}]
        ),
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "#7 STALE" in output
    assert "rule 3:" in output


def test_removed_main_module_matching_unresolved_test_import_is_rule_three_stale(
    tmp_path: Path, capsys
) -> None:
    write(tmp_path, "app/__init__.py", "")
    pr_files: list[dict[str, object]] = [
        {
            "filename": "tests/unit/test_removed.py",
            "status": "added",
            "patch": "@@ -0,0 +1 @@\n+from app.core import Widget\n",
        }
    ]

    result = pr_blast_radius.main(
        ["--repo", "example/project", "--pr", "7", "--root", str(tmp_path)],
        fetcher=_comparison_fetcher(
            pr_files, [{"filename": "app/core.py", "status": "removed"}]
        ),
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "#7 STALE" in output
    assert "rule 3:" in output


@pytest.mark.parametrize(
    ("side", "filename", "status", "previous_filename"),
    [
        ("main", "../uv.lock", "modified", None),
        ("pr", "/absolute/file.py", "modified", None),
        ("main", "src\\file.py", "modified", None),
        ("pr", "src//file.py", "modified", None),
        ("main", "src/file.py\x00tail", "modified", None),
        ("pr", "docs/new.md", "renamed", "../docs/old.md"),
        ("main", "docs/new.md", "renamed", "docs//old.md"),
    ],
)
def test_api_paths_must_be_safe_repo_relative_paths(
    tmp_path: Path,
    capsys,
    side: str,
    filename: str,
    status: str,
    previous_filename: str | None,
) -> None:
    record: dict[str, object] = {"filename": filename, "status": status}
    if previous_filename is not None:
        record["previous_filename"] = previous_filename
    pr_files = [record] if side == "pr" else [{"filename": "docs/pr.md", "status": "modified"}]
    main_files = [record] if side == "main" else [{"filename": "docs/main.md", "status": "modified"}]

    result = pr_blast_radius.main(
        ["--repo", "example/project", "--pr", "7", "--root", str(tmp_path)],
        fetcher=_comparison_fetcher(pr_files, main_files),
    )

    output = capsys.readouterr().out
    assert result == 1
    assert "#7 STALE" in output
    assert "rule 5:" in output


def test_duplicate_main_filenames_make_comparison_rule_five_stale(
    tmp_path: Path, capsys
) -> None:
    pr_files: list[dict[str, object]] = [
        {"filename": "docs/pr.md", "status": "modified"}
    ]
    main_files: list[dict[str, object]] = [
        {"filename": "src/shared.py", "status": "modified"},
        {"filename": "src/shared.py", "status": "modified"},
    ]

    result = pr_blast_radius.main(
        ["--repo", "example/project", "--pr", "7", "--root", str(tmp_path)],
        fetcher=_comparison_fetcher(pr_files, main_files),
    )

    output = capsys.readouterr().out
    assert result == 1
    assert "#7 STALE" in output
    assert "rule 5:" in output


def test_changed_python_file_without_patch_is_rule_five_stale(
    tmp_path: Path, capsys
) -> None:
    pr_files: list[dict[str, object]] = [
        {"filename": "src/new_module.py", "status": "added"}
    ]

    result = pr_blast_radius.main(
        ["--repo", "example/project", "--pr", "7", "--root", str(tmp_path)],
        fetcher=_comparison_fetcher(
            pr_files, [{"filename": "docs/guide.md", "status": "modified"}]
        ),
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "#7 STALE" in output
    assert "rule 5:" in output


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
    pr_files = {"7": [], "8": []}

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
            pr_number = endpoint.rsplit("/", 1)[-1]
            return _pull_detail(f"head-{pr_number}", pr_files[pr_number])
        if "/files?per_page=100" in endpoint:
            pr_number = endpoint.split("/pulls/", 1)[1].split("/", 1)[0]
            return pr_files[pr_number]
        if endpoint in {
            "repos/example/project/compare/main...head-7",
            "repos/example/project/compare/main...head-8",
        }:
            return {
                "behind_by": 0,
                "merge_base_commit": {"sha": "merge-sha"},
            }
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
            return _pull_detail("head-sha", [])
        if endpoint == "repos/example/project/compare/main...head-sha":
            return {
                "behind_by": behind_by,
                "merge_base_commit": {"sha": "merge-sha"},
            }
        if endpoint == "repos/example/project/pulls/7/files?per_page=100":
            return []
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


def test_zero_behind_without_merge_base_is_stale_and_posts_failure(
    tmp_path: Path, capsys
) -> None:
    status_posts: list[tuple[str, str]] = []

    def fake_fetcher(endpoint, *, method="GET", fields=None, paginate=False):
        if endpoint == "repos/example/project":
            return {"default_branch": "main"}
        if endpoint == "repos/example/project/pulls/7":
            return {"head": {"sha": "head-sha"}, "base": {"ref": "main"}}
        if endpoint == "repos/example/project/compare/main...head-sha":
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

    output = capsys.readouterr().out
    assert result == 1
    assert "#7 STALE" in output
    assert "rule 5:" in output
    assert status_posts == [("head-sha", "failure")]


def test_short_pr_file_list_is_stale_and_posts_failure(
    tmp_path: Path, capsys
) -> None:
    status_posts: list[tuple[str, str]] = []

    def fake_fetcher(endpoint, *, method="GET", fields=None, paginate=False):
        if endpoint == "repos/example/project":
            return {"default_branch": "main"}
        if endpoint == "repos/example/project/pulls/7":
            return {
                "head": {"sha": "head-sha"},
                "base": {"ref": "main"},
                "changed_files": 101,
            }
        if endpoint == "repos/example/project/compare/main...head-sha":
            return {"behind_by": 1, "merge_base_commit": {"sha": "merge-sha"}}
        if endpoint == "repos/example/project/pulls/7/files?per_page=100":
            return [
                {"filename": f"docs/pr-{index}.md", "status": "modified"}
                for index in range(100)
            ]
        if endpoint == "repos/example/project/compare/merge-sha...main":
            return {"files": [{"filename": "docs/main.md", "status": "modified"}]}
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

    output = capsys.readouterr().out
    assert result == 1
    assert "#7 STALE" in output
    assert "rule 5:" in output
    assert status_posts == [("head-sha", "failure")]


def test_dynamic_directory_glob_loader_tracks_plugin_files(tmp_path: Path) -> None:
    write(tmp_path, "plugins/demo/mod.py", "VALUE = 1\n")
    test_path = "tests/unit/test_dynamic_loader.py"
    module_path = "plugins/demo/mod.py"
    write(
        tmp_path,
        test_path,
        "from pathlib import Path\n"
        "import importlib.util\n"
        "ROOT = Path(__file__).resolve().parents[2]\n"
        'for p in (ROOT / "plugins" / "demo").glob("*.py"):\n'
        '    spec = importlib.util.spec_from_file_location(p.stem, p)\n',
    )

    graph = pr_blast_radius.build_import_graph(tmp_path)
    stale, reasons = pr_blast_radius.decide({test_path}, {module_path}, graph)

    assert module_path in graph[test_path]
    assert stale is True
    assert any(reason.startswith("rule 3:") for reason in reasons)


def test_single_pr_repo_lookup_failure_posts_failure_for_fetched_head(
    tmp_path: Path, capsys
) -> None:
    calls: list[str] = []
    status_posts: list[tuple[str, str]] = []

    def fake_fetcher(endpoint, *, method="GET", fields=None, paginate=False):
        calls.append(endpoint)
        if endpoint == "repos/example/project/pulls/7":
            return {"head": {"sha": "head-sha"}, "base": {"ref": "main"}}
        if endpoint == "repos/example/project":
            raise RuntimeError("repository lookup unavailable")
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

    output = capsys.readouterr().out
    assert result == 1
    assert calls[:2] == [
        "repos/example/project/pulls/7",
        "repos/example/project",
    ]
    assert "#7 STALE" in output
    assert status_posts == [("head-sha", "failure")]


def test_all_open_skips_other_base_even_when_head_sha_is_missing(
    tmp_path: Path, capsys
) -> None:
    calls: list[str] = []

    def fake_fetcher(endpoint, *, method="GET", fields=None, paginate=False):
        calls.append(endpoint)
        if endpoint == "repos/example/project":
            return {"default_branch": "main"}
        if endpoint == "repos/example/project/pulls?state=open&per_page=100":
            return [{"number": 7, "base": {"ref": "release"}, "head": {}}]
        return {}

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

    assert result == 0
    assert calls == [
        "repos/example/project",
        "repos/example/project/pulls?state=open&per_page=100",
    ]
    assert capsys.readouterr().out == ""
