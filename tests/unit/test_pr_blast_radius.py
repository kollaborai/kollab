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

    assert graph["kollab/nested/runner.py"] == {
        "kollab/core.py",
        "kollab/helpers.py",
        "kollab/nested/sibling.py",
        "kollab/nested/sibling/deeper.py",
    }
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
        if endpoint == "repos/example/project/compare/main...head-sha":
            return {"behind_by": 1, "merge_base_commit": {"sha": "merge-sha"}}
        if endpoint == "repos/example/project/pulls/7/files?per_page=100":
            return [{"filename": "scripts/local_tool.py"}]
        if endpoint == "repos/example/project/compare/merge-sha...main":
            return {"files": [{"filename": "docs/guide.md"}]}
        raise AssertionError(f"unexpected endpoint: {endpoint}")

    result = pr_blast_radius.main(
        ["--repo", "example/project", "--pr", "7", "--root", str(tmp_path)],
        fetcher=fake_fetcher,
    )

    assert result == 0
    assert capsys.readouterr().out == "#7 FRESH no relevant test impact\n"
    assert any(paginate for _, paginate in calls)
