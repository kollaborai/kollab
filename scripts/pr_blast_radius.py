"""Decide whether changes on the base branch can affect a pull request's tests."""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from collections import defaultdict, deque
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable

MAX_FILE_LIST = 300
_JS_IMPORT = re.compile(
    r"\bfrom\s*(['\"])(\.[^'\"]+)\1|"
    r"\bimport\s*\(\s*(['\"])(\.[^'\"]+)\3\s*\)"
)
_JS_SUFFIXES = (".ts", ".tsx", ".js", ".mjs")


def _repo_path(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _python_sources(root: Path) -> tuple[set[Path], list[Path]]:
    paths: set[Path] = set()
    for name in ("kollab", "plugins", "scripts", "tests"):
        folder = root / name
        if folder.is_dir():
            paths.update(path for path in folder.rglob("*.py") if path.is_file())

    for initializer in root.glob("*/__init__.py"):
        folder = initializer.parent
        paths.update(path for path in folder.rglob("*.py") if path.is_file())

    package_src_roots = sorted(path for path in (root / "packages").glob("*/src") if path.is_dir())
    for folder in package_src_roots:
        paths.update(path for path in folder.rglob("*.py") if path.is_file())

    packages = root / "packages"
    if packages.is_dir():
        for folder in packages.glob("*/tests"):
            if folder.is_dir():
                paths.update(path for path in folder.rglob("*.py") if path.is_file())
    return paths, package_src_roots


def _module_name(path: Path, root: Path, package_src_roots: list[Path]) -> tuple[str, bool]:
    module_root = root
    for source_root in package_src_roots:
        if path.is_relative_to(source_root):
            module_root = source_root
            break
    relative = path.relative_to(module_root).with_suffix("")
    parts = list(relative.parts)
    is_package = parts[-1] == "__init__"
    if is_package:
        parts.pop()
    return ".".join(parts), is_package


def _resolve_module(name: str, module_index: dict[str, str]) -> str | None:
    return module_index.get(name) if name else None


def _add_python_dependency(
    dependencies: set[str],
    name: str,
    module_index: dict[str, str],
    package_index: dict[str, str],
) -> None:
    target = _resolve_module(name, module_index)
    if target is None:
        return
    dependencies.add(target)
    parts = name.split(".")
    for end in range(1, len(parts) + 1):
        initializer = package_index.get(".".join(parts[:end]))
        if initializer is not None:
            dependencies.add(initializer)


def _python_imports(
    tree: ast.Module,
    module_name: str,
    is_package: bool,
    module_index: dict[str, str],
    package_index: dict[str, str],
) -> set[str]:
    dependencies: set[str] = set()
    current_parts = module_name.split(".") if module_name else []
    package_parts = current_parts if is_package else current_parts[:-1]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                _add_python_dependency(
                    dependencies, alias.name, module_index, package_index
                )
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parent_count = len(package_parts) - (node.level - 1)
                if parent_count < 0:
                    continue
                base_parts = package_parts[:parent_count]
                if node.module:
                    base_parts.extend(node.module.split("."))
                base_name = ".".join(base_parts)
            else:
                base_name = node.module or ""

            if any(alias.name == "*" for alias in node.names):
                _add_python_dependency(
                    dependencies, base_name, module_index, package_index
                )
                prefix = f"{base_name}." if base_name else ""
                depth = len(base_name.split(".")) + 1 if base_name else 1
                for candidate, target in module_index.items():
                    if (
                        prefix
                        and candidate.startswith(prefix)
                        and len(candidate.split(".")) == depth
                    ):
                        dependencies.add(target)
                continue

            aliases = [alias.name for alias in node.names if alias.name != "*"]
            candidates = [
                f"{base_name}.{alias}" if base_name else alias for alias in aliases
            ] or [base_name]
            for candidate in candidates:
                resolved_name = (
                    candidate
                    if _resolve_module(candidate, module_index) is not None
                    else base_name
                )
                _add_python_dependency(
                    dependencies, resolved_name, module_index, package_index
                )
    return dependencies


def _path_string_parts(node: ast.AST) -> list[str]:
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
        return _path_string_parts(node.left) + _path_string_parts(node.right)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    return []


def _existing_repo_file(root: Path, value: str) -> str | None:
    candidate = Path(value.replace("\\", "/"))
    try:
        resolved = (root / candidate).resolve()
        relative = resolved.relative_to(root).as_posix()
    except ValueError:
        return None
    return relative if resolved.is_file() else None


def _js_sources(root: Path) -> set[Path]:
    paths: set[Path] = set()
    for folder in (
        root / "packages" / "kollabor-webui" / "frontend" / "src",
        root / "packages" / "kollabor-webui" / "frontend" / "tests",
    ):
        if folder.is_dir():
            for suffix in ("*.ts", "*.tsx", "*.js", "*.mjs"):
                paths.update(path for path in folder.rglob(suffix) if path.is_file())
    return paths


def _resolve_js_import(root: Path, importer: Path, specifier: str) -> str | None:
    base = importer.parent / specifier
    candidates = [base]
    candidates.extend(Path(f"{base}{suffix}") for suffix in _JS_SUFFIXES)
    candidates.extend((base / "index.ts", base / "index.tsx"))
    root_resolved = root.resolve()
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            return candidate.resolve().relative_to(root_resolved).as_posix()
        except ValueError:
            continue
    return None


def build_import_graph(root: Path) -> dict[str, set[str]]:
    """Build a static Python and frontend import graph without importing code."""
    root = root.resolve()
    python_paths, package_src_roots = _python_sources(root)
    module_index: dict[str, str] = {}
    module_info: dict[str, tuple[str, bool]] = {}
    for path in python_paths:
        relative_path = _repo_path(root, path)
        name, is_package = _module_name(path, root, package_src_roots)
        if name:
            module_index[name] = relative_path
        module_info[relative_path] = (name, is_package)

    graph: dict[str, set[str]] = {path: set() for path in module_info}
    package_index = {
        name: path
        for path, (name, is_package) in module_info.items()
        if name and is_package
    }
    for path in sorted(python_paths):
        relative_path = _repo_path(root, path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative_path)
        except (OSError, UnicodeError, SyntaxError) as exc:
            raise RuntimeError(f"could not parse {relative_path}") from exc
        name, is_package = module_info[relative_path]
        graph[relative_path] = _python_imports(
            tree, name, is_package, module_index, package_index
        )
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                target = _existing_repo_file(root, node.value)
                if target is not None:
                    graph[relative_path].add(target)
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                parts = _path_string_parts(node)
                if parts:
                    target = _existing_repo_file(root, "/".join(parts))
                    if target is not None:
                        graph[relative_path].add(target)

    for path in sorted(_js_sources(root)):
        relative_path = _repo_path(root, path)
        dependencies: set[str] = set()
        source = path.read_text(encoding="utf-8")
        for match in _JS_IMPORT.finditer(source):
            specifier = match.group(2) or match.group(4)
            target = _resolve_js_import(root, path, specifier)
            if target is not None:
                dependencies.add(target)
        graph[relative_path] = dependencies
    return graph


def reverse_closure(graph: dict[str, set[str]], files: Iterable[str]) -> set[str]:
    """Return the input files and every transitive importer of those files."""
    reverse: dict[str, set[str]] = defaultdict(set)
    for importer, dependencies in graph.items():
        for dependency in dependencies:
            reverse[dependency].add(importer)

    closure = set(files)
    pending = deque(closure)
    while pending:
        dependency = pending.popleft()
        for importer in reverse.get(dependency, ()):
            if importer not in closure:
                closure.add(importer)
                pending.append(importer)
    return closure


def _is_test_file(path: str) -> bool:
    parts = PurePosixPath(path).parts
    if parts and parts[0] == "tests":
        return True
    if len(parts) >= 4 and parts[0] == "packages" and parts[2] == "tests":
        return True
    return path.startswith("packages/kollabor-webui/frontend/tests/")


def tests_of(graph: dict[str, set[str]], files: Iterable[str]) -> set[str]:
    """Find test files that are changed or depend on any given file."""
    return {path for path in reverse_closure(graph, files) if _is_test_file(path)}


def _is_global_main_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    if path in {"uv.lock", "pyproject.toml", ".python-version"}:
        return True
    if len(parts) == 3 and parts[0] == "packages" and parts[2] == "pyproject.toml":
        return True
    if PurePosixPath(path).name in {"package.json", "package-lock.json", "conftest.py"}:
        return True
    return path.startswith((".github/workflows/", ".github/actions/", "tests/tmux/lib/"))


def _examples(paths: Iterable[str]) -> str:
    return ", ".join(sorted(paths)[:3])


def _without_changes(paths: Iterable[str]) -> set[str]:
    return {
        PurePosixPath(path).as_posix()
        for path in paths
        if not (path == "changes" or path.startswith("changes/"))
    }


def decide(
    pr_files: Iterable[str],
    main_files: Iterable[str],
    graph: dict[str, set[str]],
    complete: bool = True,
) -> tuple[bool, list[str]]:
    """Apply the five stale rules to changed paths and their test impact."""
    pr_paths = _without_changes(pr_files)
    main_paths = _without_changes(main_files)
    reasons: list[str] = []

    global_paths = {path for path in main_paths if _is_global_main_path(path)}
    if global_paths:
        reasons.append(f"rule 1: global main path ({_examples(global_paths)})")

    overlap = pr_paths & main_paths
    if overlap:
        reasons.append(f"rule 2: changed on both sides ({_examples(overlap)})")

    shared_tests = tests_of(graph, pr_paths) & tests_of(graph, main_paths)
    if shared_tests:
        reasons.append(f"rule 3: shared test impact ({_examples(shared_tests)})")

    frontend_root = "packages/kollabor-webui/frontend/"
    pr_frontend = {path for path in pr_paths if path.startswith(frontend_root)}
    main_frontend = {path for path in main_paths if path.startswith(frontend_root)}
    if pr_frontend and main_frontend:
        examples = pr_frontend | main_frontend
        reasons.append(f"rule 4: both sides touch frontend ({_examples(examples)})")

    if not complete:
        reasons.append("rule 5: comparison data incomplete")
    return bool(reasons), reasons


def _gh_fetcher(
    endpoint: str,
    *,
    method: str = "GET",
    fields: dict[str, str] | None = None,
    paginate: bool = False,
) -> Any:
    args = ["api"]
    if paginate:
        args.extend(("--paginate", "--slurp"))
    if method != "GET":
        args.extend(("--method", method))
    for name, value in (fields or {}).items():
        args.extend(("-f", f"{name}={value}"))
    args.append(endpoint)
    proc = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"gh api failed ({proc.returncode}): {proc.stderr.strip()}")
    return json.loads(proc.stdout) if proc.stdout.strip() else None


def _fetch(
    fetcher: Callable[..., Any],
    endpoint: str,
    *,
    method: str = "GET",
    fields: dict[str, str] | None = None,
    paginate: bool = False,
) -> Any:
    return fetcher(endpoint, method=method, fields=fields, paginate=paginate)


def _records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise ValueError("expected a paginated list")
    if any(isinstance(page, list) for page in value):
        if not all(isinstance(page, list) for page in value):
            raise ValueError("invalid paginated response")
        value = [record for page in value for record in page]
    if not all(isinstance(record, dict) for record in value):
        raise ValueError("invalid record in paginated response")
    return value


def _text(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("expected non-empty text")
    return value


def _number(value: Any) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError("expected a positive integer")
    return value


def _count(value: Any) -> int:
    if type(value) is not int or value < 0:
        raise ValueError("expected a non-negative integer")
    return value


def _changed_paths(value: Any) -> tuple[list[str], bool]:
    if not isinstance(value, list):
        return [], False
    complete = len(value) < MAX_FILE_LIST
    paths: list[str] = []
    for record in value:
        if not isinstance(record, dict):
            complete = False
            continue
        try:
            filename = _text(record.get("filename"))
        except ValueError:
            complete = False
            continue
        paths.append(filename)
        try:
            status = _text(record.get("status"))
        except ValueError:
            status = ""
            complete = False
        if status not in {"added", "removed", "modified", "renamed", "copied"}:
            complete = False
        previous_filename = record.get("previous_filename")
        if status in {"renamed", "copied"}:
            try:
                paths.append(_text(previous_filename))
            except ValueError:
                complete = False
        elif previous_filename is not None:
            try:
                paths.append(_text(previous_filename))
            except ValueError:
                complete = False
    return paths, complete


def _default_branch(fetcher: Callable[..., Any], repo: str) -> str:
    repository = _fetch(fetcher, f"repos/{repo}")
    if not isinstance(repository, dict):
        raise ValueError("invalid repository response")
    return _text(repository.get("default_branch"))


def _inspect_pr(
    repo: str,
    number: int,
    default_branch: str,
    graph: dict[str, set[str]],
    fetcher: Callable[..., Any],
    head_state: dict[str, str],
    selection_error: str | None = None,
) -> tuple[bool, list[str]]:
    number = _number(number)
    default_branch = _text(default_branch)
    pull = _fetch(fetcher, f"repos/{repo}/pulls/{number}")
    if not isinstance(pull, dict):
        raise ValueError("invalid pull request response")
    head = pull.get("head")
    head_sha = _text(head.get("sha") if isinstance(head, dict) else None)
    head_state["sha"] = head_sha
    base = pull.get("base")
    base_ref = _text(base.get("ref") if isinstance(base, dict) else None)
    if base_ref != default_branch:
        raise ValueError("pull request base does not match the repository default branch")
    if selection_error is not None:
        raise ValueError(selection_error)

    comparison = _fetch(fetcher, f"repos/{repo}/compare/{base_ref}...{head_sha}")
    if not isinstance(comparison, dict):
        raise ValueError("invalid pull request comparison response")
    behind_by = _count(comparison.get("behind_by"))
    merge_base_sha: str | None = None
    if "merge_base_commit" in comparison:
        merge_base_commit = comparison["merge_base_commit"]
        if not isinstance(merge_base_commit, dict):
            raise ValueError("invalid merge base response")
        merge_base_sha = _text(merge_base_commit.get("sha"))
    if behind_by == 0:
        return False, ["up to date"]
    if merge_base_sha is None:
        raise ValueError("comparison is missing its merge base")

    pr_records = _records(
        _fetch(fetcher, f"repos/{repo}/pulls/{number}/files?per_page=100", paginate=True)
    )
    main_comparison = _fetch(
        fetcher, f"repos/{repo}/compare/{merge_base_sha}...{base_ref}"
    )
    if not isinstance(main_comparison, dict):
        raise ValueError("invalid base comparison response")
    pr_paths, pr_complete = _changed_paths(pr_records)
    main_paths, main_complete = _changed_paths(main_comparison.get("files"))
    return decide(pr_paths, main_paths, graph, complete=pr_complete and main_complete)


def _post_status(
    fetcher: Callable[..., Any],
    repo: str,
    head_sha: str,
    state: str,
    context: str,
    description: str,
) -> None:
    _fetch(
        fetcher,
        f"repos/{repo}/statuses/{head_sha}",
        method="POST",
        fields={"state": state, "context": context, "description": description[:140]},
    )


def _pull_numbers(
    fetcher: Callable[..., Any], repo: str
) -> tuple[str, list[tuple[int | None, str, str | None]]]:
    default_branch = _default_branch(fetcher, repo)
    pulls = _records(
        _fetch(fetcher, f"repos/{repo}/pulls?state=open&per_page=100", paginate=True)
    )
    selected: list[tuple[int | None, str, str | None]] = []
    for pull in pulls:
        errors: list[str] = []
        try:
            number = _number(pull.get("number"))
        except ValueError:
            number = None
            errors.append("open pull request has an invalid number")
        base = pull.get("base")
        try:
            base_ref = _text(base.get("ref") if isinstance(base, dict) else None)
        except ValueError:
            base_ref = None
            errors.append("open pull request has an invalid base ref")
        head = pull.get("head")
        try:
            head_sha = _text(head.get("sha") if isinstance(head, dict) else None)
        except ValueError:
            head_sha = ""
            errors.append("open pull request has an invalid head sha")
        if base_ref is not None and base_ref != default_branch and not errors:
            continue
        selected.append(
            (number, head_sha, "; ".join(errors) if errors else None)
        )
    return default_branch, selected


def _line(number: int | None, stale: bool, reasons: list[str]) -> str:
    status = "STALE" if stale else "FRESH"
    detail = reasons[0] if reasons else "no relevant test impact"
    identity = f"#{number}" if number is not None else "#?"
    return f"{identity} {status} {detail}"


def main(argv: list[str] | None = None, fetcher: Callable[..., Any] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="owner/name")
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--pr", type=int)
    selection.add_argument("--all-open", action="store_true")
    parser.add_argument("--root", type=Path, default=Path("."))
    parser.add_argument("--post", action="store_true")
    parser.add_argument("--context", default="ci/base-fresh")
    args = parser.parse_args(argv)

    api = fetcher or _gh_fetcher
    graph_error: Exception | None = None
    try:
        graph = build_import_graph(args.root)
    except Exception as exc:
        graph = {}
        graph_error = exc
        if not args.post:
            print(f"blast-radius error: {type(exc).__name__}", file=sys.stderr)
            return 1

    try:
        if args.all_open:
            default_branch, pulls = _pull_numbers(api, args.repo)
        else:
            default_branch = _default_branch(api, args.repo)
            pulls = [(_number(args.pr), "", None)]
    except Exception as exc:
        print(f"blast-radius error: {type(exc).__name__}", file=sys.stderr)
        return 1

    if graph_error is not None:
        print(f"blast-radius error: {type(graph_error).__name__}", file=sys.stderr)
        description = f"blast-radius error: {type(graph_error).__name__}"
        for number, head_hint, _selection_error in pulls:
            head_sha = head_hint
            if not head_sha and number is not None:
                try:
                    pull = _fetch(api, f"repos/{args.repo}/pulls/{number}")
                    candidate = (
                        pull.get("head", {}).get("sha")
                        if isinstance(pull, dict)
                        and isinstance(pull.get("head"), dict)
                        else None
                    )
                    head_sha = _text(candidate)
                except Exception:
                    head_sha = ""
            if head_sha:
                try:
                    _post_status(
                        api, args.repo, head_sha, "failure", args.context, description
                    )
                except Exception as exc:
                    print(
                        f"blast-radius status error: {type(exc).__name__}",
                        file=sys.stderr,
                    )
            if number is None:
                print(
                    _line(
                        None,
                        True,
                        ["rule 5: comparison data incomplete (invalid pull number)"],
                    )
                )
                continue
            print(
                _line(
                    number,
                    True,
                    [f"rule 5: comparison data incomplete ({type(graph_error).__name__})"],
                )
            )
        return 1

    exit_code = 0
    for number, head_hint, selection_error in pulls:
        if number is None:
            exit_code = 1
            if args.post and head_hint:
                try:
                    _post_status(
                        api,
                        args.repo,
                        head_hint,
                        "failure",
                        args.context,
                        "blast-radius error: ValueError",
                    )
                except Exception as status_exc:
                    print(
                        f"blast-radius status error: {type(status_exc).__name__}",
                        file=sys.stderr,
                    )
            print(
                _line(
                    None,
                    True,
                    ["rule 5: comparison data incomplete (invalid pull number)"],
                )
            )
            continue
        head_state = {"sha": head_hint}
        try:
            stale, reasons = _inspect_pr(
                args.repo,
                number,
                default_branch,
                graph,
                api,
                head_state,
                selection_error=selection_error,
            )
        except Exception as exc:
            exit_code = 1
            stale = True
            reasons = [f"rule 5: comparison data incomplete ({type(exc).__name__})"]
            if args.post and head_state["sha"]:
                try:
                    _post_status(
                        api,
                        args.repo,
                        head_state["sha"],
                        "failure",
                        args.context,
                        f"blast-radius error: {type(exc).__name__}",
                    )
                except Exception as status_exc:
                    exit_code = 1
                    print(
                        f"blast-radius status error: {type(status_exc).__name__}",
                        file=sys.stderr,
                    )
            print(_line(number, stale, reasons))
            continue

        if args.post:
            state = "failure" if stale else "success"
            description = reasons[0] if reasons else "no relevant test impact"
            try:
                _post_status(api, args.repo, head_state["sha"], state, args.context, description)
            except Exception as exc:
                stale = True
                reasons = [f"rule 5: comparison data incomplete ({type(exc).__name__})"]
                exit_code = 1
                try:
                    _post_status(
                        api,
                        args.repo,
                        head_state["sha"],
                        "failure",
                        args.context,
                        f"blast-radius error: {type(exc).__name__}",
                    )
                except Exception as status_exc:
                    print(
                        f"blast-radius status error: {type(status_exc).__name__}",
                        file=sys.stderr,
                    )
        print(_line(number, stale, reasons))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
