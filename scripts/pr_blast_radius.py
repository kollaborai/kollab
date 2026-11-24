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
_DYNAMIC_MODULE_CALLS = {"import_module", "__import__", "run_module"}
_DYNAMIC_DIRECTORY_CALLS = {"glob", "rglob", "iterdir", "walk", "listdir"}
_DYNAMIC_CALL_IN_PATCH = re.compile(
    r"\b(?:import_module|__import__|spec_from_file_location|exec_module|"
    r"run_path|run_module|glob|rglob|iterdir|walk|listdir)\s*\("
)
_DYNAMIC_IMPORT_MENTION_IN_PATCH = re.compile(
    r"\b(?:import_module|__import__|spec_from_file_location|importlib)\b"
)


class _ImportGraph(dict[str, set[str]]):
    def __init__(
        self,
        graph: dict[str, set[str]],
        dynamic: set[str],
        module_index: dict[str, str],
        package_index: dict[str, str],
        package_src_roots: list[Path],
        unresolved_imports: dict[str, set[str]],
    ) -> None:
        super().__init__(graph)
        self.dynamic = dynamic
        self.module_index = module_index
        self.package_index = package_index
        self.package_src_roots = package_src_roots
        self.unresolved_imports = unresolved_imports


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


def _dynamic_import_aliases(tree: ast.Module) -> dict[str, str]:
    aliases: dict[str, str] = {}
    relevant_modules = {"builtins", "glob", "importlib", "importlib.util", "os", "runpy"}
    relevant_names = _DYNAMIC_MODULE_CALLS | {
        "spec_from_file_location",
        "exec_module",
        "run_path",
        "glob",
        "rglob",
        "iterdir",
        "walk",
        "listdir",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound_name = alias.asname or alias.name.split(".")[0]
                imported_name = alias.name if alias.asname else bound_name
                if imported_name in relevant_modules:
                    aliases[bound_name] = imported_name
        elif isinstance(node, ast.ImportFrom) and node.module in relevant_modules:
            for alias in node.names:
                if alias.name in relevant_names:
                    aliases[alias.asname or alias.name] = f"{node.module}.{alias.name}"
    return aliases


def _call_name(node: ast.Call, aliases: dict[str, str] | None = None) -> str | None:
    def dotted_name(value: ast.AST) -> str | None:
        if isinstance(value, ast.Name):
            return value.id
        if isinstance(value, ast.Attribute):
            prefix = dotted_name(value.value)
            return f"{prefix}.{value.attr}" if prefix else None
        return None

    name = dotted_name(node.func)
    if name is None and isinstance(node.func, ast.Attribute):
        return node.func.attr
    if name is None:
        return None
    aliases = aliases or {}
    first, separator, remainder = name.partition(".")
    if first in aliases:
        return aliases[first] + (f".{remainder}" if separator else "")
    return aliases.get(name, name)


def _call_argument(
    node: ast.Call, position: int, keyword_names: set[str]
) -> ast.AST | None:
    if len(node.args) > position:
        return node.args[position]
    return next(
        (keyword.value for keyword in node.keywords if keyword.arg in keyword_names),
        None,
    )


def _literal_string(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _module_resolves(
    name: str, module_index: dict[str, str], package_index: dict[str, str]
) -> bool:
    dependencies: set[str] = set()
    _add_python_dependency(dependencies, name, module_index, package_index)
    return bool(dependencies)


def _is_dynamic_load_call(
    root: Path,
    node: ast.Call,
    module_index: dict[str, str],
    package_index: dict[str, str],
    aliases: dict[str, str] | None = None,
) -> bool:
    name = _call_name(node, aliases)
    short_name = name.rsplit(".", 1)[-1] if name else None
    if short_name in _DYNAMIC_MODULE_CALLS:
        argument = _call_argument(node, 0, {"name", "name_or_path", "mod_name"})
        module_name = _literal_string(argument)
        return module_name is None or not _module_resolves(
            module_name, module_index, package_index
        )
    if short_name == "spec_from_file_location":
        argument = _call_argument(node, 1, {"location"})
        path = _literal_string(argument)
        return path is None or _existing_repo_file(root, path) is None
    if short_name == "run_path":
        argument = _call_argument(node, 0, {"path_name"})
        path = _literal_string(argument)
        return path is None or _existing_repo_file(root, path) is None
    if short_name == "exec_module":
        return True
    if short_name not in _DYNAMIC_DIRECTORY_CALLS:
        return False

    if short_name in {"glob", "rglob", "iterdir"} and isinstance(
        node.func, ast.Attribute
    ):
        if short_name == "glob" and name == "glob.glob":
            argument = _call_argument(node, 0, {"pathname"})
            path = _literal_string(argument)
            return path is None or _existing_repo_file(root, path) is None
        return True

    argument = _call_argument(node, 0, {"path", "pathname"})
    path = _literal_string(argument)
    return path is None or _existing_repo_file(root, path) is None


def _python_imports(
    tree: ast.Module,
    module_name: str,
    is_package: bool,
    module_index: dict[str, str],
    package_index: dict[str, str],
    unresolved: set[str] | None = None,
    include_dynamic_package_ancestors: bool = False,
) -> set[str]:
    dependencies: set[str] = set()
    current_parts = module_name.split(".") if module_name else []
    package_parts = current_parts if is_package else current_parts[:-1]
    call_aliases = _dynamic_import_aliases(tree)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if _resolve_module(alias.name, module_index) is None:
                    if unresolved is not None:
                        unresolved.add(alias.name)
                else:
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
                if (
                    base_name
                    and _resolve_module(base_name, module_index) is None
                    and unresolved is not None
                ):
                    unresolved.add(base_name)
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

            imported_names = [
                alias.name for alias in node.names if alias.name != "*"
            ]
            candidates = [
                f"{base_name}.{alias}" if base_name else alias
                for alias in imported_names
            ] or [base_name]
            for candidate in candidates:
                candidate_resolves = (
                    _resolve_module(candidate, module_index) is not None
                )
                resolved_name = candidate if candidate_resolves else base_name
                if not candidate_resolves and unresolved is not None:
                    if candidate:
                        unresolved.add(candidate)
                    if (
                        base_name
                        and _resolve_module(base_name, module_index) is None
                    ):
                        unresolved.add(base_name)
                _add_python_dependency(
                    dependencies, resolved_name, module_index, package_index
                )
        elif isinstance(node, ast.Call) and (
            (_call_name(node, call_aliases) or "").rsplit(".", 1)[-1]
            in _DYNAMIC_MODULE_CALLS
        ):
            module_name = _literal_string(
                _call_argument(node, 0, {"name", "name_or_path", "mod_name"})
            )
            if module_name is not None:
                target = _resolve_module(module_name, module_index)
                if target is not None and include_dynamic_package_ancestors:
                    _add_python_dependency(
                        dependencies, module_name, module_index, package_index
                    )
                elif target is not None:
                    dependencies.add(target)
                elif unresolved is not None:
                    unresolved.add(module_name)
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


def _glob_directory_prefix(value: str) -> str:
    normalized = value.replace("\\", "/")
    wildcard_positions = [
        normalized.find(marker) for marker in ("*", "?", "[") if marker in normalized
    ]
    if not wildcard_positions:
        return normalized.rstrip("/")
    prefix = normalized[: min(wildcard_positions)]
    if prefix and not prefix.endswith("/"):
        prefix = prefix.rpartition("/")[0]
    return prefix.rstrip("/")


def _existing_repo_targets(root: Path, value: str) -> set[str]:
    targets: set[str] = set()
    exact_file = _existing_repo_file(root, value)
    if exact_file is not None:
        targets.add(exact_file)

    prefix = _glob_directory_prefix(value)
    if not prefix:
        return targets
    try:
        directory = (root / Path(prefix)).resolve()
        directory.relative_to(root)
    except ValueError:
        return targets
    if directory.is_dir():
        for path in directory.rglob("*.py"):
            try:
                path.resolve().relative_to(root)
            except ValueError:
                continue
            if path.is_file():
                targets.add(_repo_path(root, path))
    return targets


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


def _build_import_analysis(
    root: Path,
) -> tuple[
    dict[str, set[str]],
    set[str],
    dict[str, str],
    dict[str, str],
    list[Path],
    dict[str, set[str]],
]:
    """Build the import graph and its statically resolvable loader context."""
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
    dynamic_paths: set[str] = set()
    unresolved_imports: dict[str, set[str]] = {}
    for path in sorted(python_paths):
        relative_path = _repo_path(root, path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative_path)
        except (OSError, UnicodeError, SyntaxError) as exc:
            raise RuntimeError(f"could not parse {relative_path}") from exc
        name, is_package = module_info[relative_path]
        unresolved: set[str] = set()
        graph[relative_path] = _python_imports(
            tree, name, is_package, module_index, package_index, unresolved
        )
        unresolved_imports[relative_path] = unresolved
        nodes = list(ast.walk(tree))
        aliases = _dynamic_import_aliases(tree)
        parents = {
            child: node for node in nodes for child in ast.iter_child_nodes(node)
        }
        if any(
            isinstance(node, ast.Call)
            and _is_dynamic_load_call(
                root,
                node,
                module_index,
                package_index,
                aliases,
            )
            for node in nodes
        ):
            dynamic_paths.add(relative_path)
        glob_calls = {
            node
            for node in nodes
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"glob", "rglob"}
            and node.args
        }
        glob_receivers = {node.func.value for node in glob_calls}
        glob_patterns = {node.args[0] for node in glob_calls}
        for node in nodes:
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                parent = parents.get(node)
                in_path_expression = False
                while parent is not None:
                    if isinstance(parent, ast.BinOp) and isinstance(parent.op, ast.Div):
                        in_path_expression = True
                        break
                    parent = parents.get(parent)
                if not in_path_expression and node not in glob_patterns:
                    graph[relative_path].update(
                        _existing_repo_targets(root, node.value)
                    )
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div):
                parent = parents.get(node)
                nested_path = isinstance(parent, ast.BinOp) and isinstance(
                    parent.op, ast.Div
                )
                if not nested_path and node not in glob_receivers:
                    parts = _path_string_parts(node)
                    if parts:
                        graph[relative_path].update(
                            _existing_repo_targets(root, "/".join(parts))
                        )
            if node in glob_calls:
                parts = _path_string_parts(node.func.value)
                pattern = node.args[0]
                if isinstance(pattern, ast.Constant) and isinstance(pattern.value, str):
                    prefix = _glob_directory_prefix(pattern.value)
                    if prefix:
                        parts.append(prefix)
                if parts:
                    graph[relative_path].update(
                        _existing_repo_targets(root, "/".join(parts))
                    )

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
    return (
        graph,
        dynamic_paths,
        module_index,
        package_index,
        package_src_roots,
        unresolved_imports,
    )


def build_import_graph(
    root: Path, dynamic: set[str] | None = None
) -> dict[str, set[str]]:
    """Build a static Python and frontend import graph without importing code."""
    (
        graph,
        dynamic_paths,
        module_index,
        package_index,
        package_src_roots,
        unresolved_imports,
    ) = _build_import_analysis(root)
    if dynamic is not None:
        dynamic.update(dynamic_paths)
    return _ImportGraph(
        graph,
        dynamic_paths,
        module_index,
        package_index,
        package_src_roots,
        unresolved_imports,
    )


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


def _module_name_for_changed_path(
    filename: str, root: Path, package_src_roots: list[Path]
) -> str | None:
    path = root / PurePosixPath(filename)
    source_roots = list(package_src_roots)
    parts = PurePosixPath(filename).parts
    if len(parts) >= 4 and parts[0] == "packages" and parts[2] == "src":
        inferred_root = root.joinpath(*parts[:3])
        if inferred_root not in source_roots:
            source_roots.append(inferred_root)
    try:
        name, _is_package = _module_name(path, root, source_roots)
    except ValueError:
        return None
    return name or None


def _top_level_module_for_path(filename: str) -> str | None:
    parts = list(PurePosixPath(filename).with_suffix("").parts)
    if len(parts) >= 4 and parts[0] == "packages" and parts[2] == "src":
        parts = parts[3:]
    if parts and parts[-1] == "__init__":
        parts.pop()
    return parts[0] if parts else None


def _removed_main_modules(
    records: list[dict[str, Any]], root: Path, package_src_roots: list[Path]
) -> tuple[set[str], set[str]]:
    modules: set[str] = set()
    unmapped_top_levels: set[str] = set()
    for record in records:
        status = record.get("status")
        if status == "removed":
            filename = record.get("filename")
        elif status == "renamed":
            filename = record.get("previous_filename")
        else:
            continue
        if not isinstance(filename, str) or not filename.endswith(".py"):
            continue
        module_name = _module_name_for_changed_path(
            filename, root, package_src_roots
        )
        if module_name is not None:
            modules.add(module_name)
            continue
        top_level = _top_level_module_for_path(filename)
        if top_level is not None:
            unmapped_top_levels.add(top_level)
    return modules, unmapped_top_levels


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


def _is_non_docs_change(path: str) -> bool:
    parts = PurePosixPath(path).parts
    if path.startswith(("docs/", "changes/")):
        return False
    return not (len(parts) == 1 and parts[0].endswith(".md"))


def decide(
    pr_files: Iterable[str],
    main_files: Iterable[str],
    graph: dict[str, set[str]],
    complete: bool = True,
    dynamic: set[str] | frozenset[str] = frozenset(),
    unresolved_imports: dict[str, set[str]] | None = None,
    removed_main_modules: set[str] | frozenset[str] = frozenset(),
    removed_main_top_levels: set[str] | frozenset[str] = frozenset(),
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

    affected_imports: set[str] = set()
    unresolved_imports = unresolved_imports or {}
    pr_tests = {path for path in pr_paths if _is_test_file(path)}
    for test_path in pr_tests:
        for imported_name in unresolved_imports.get(test_path, set()):
            if any(
                imported_name == module_name
                or imported_name.startswith(f"{module_name}.")
                for module_name in removed_main_modules
            ):
                affected_imports.add(imported_name)
            elif imported_name.split(".", 1)[0] in removed_main_top_levels:
                affected_imports.add(imported_name)
    if affected_imports:
        reasons.append(
            "rule 3: unresolved test import may match removed main module "
            f"({_examples(affected_imports)})"
        )

    frontend_root = "packages/kollabor-webui/frontend/"
    pr_frontend = {path for path in pr_paths if path.startswith(frontend_root)}
    main_frontend = {path for path in main_paths if path.startswith(frontend_root)}
    if pr_frontend and main_frontend:
        examples = pr_frontend | main_frontend
        reasons.append(f"rule 4: both sides touch frontend ({_examples(examples)})")

    pr_non_docs = {path for path in pr_paths if _is_non_docs_change(path)}
    main_non_docs = {path for path in main_paths if _is_non_docs_change(path)}
    dynamic_changes = {
        path
        for path in dynamic
        if path.endswith(".py")
        and (
            (path in pr_paths and main_non_docs)
            or (path in main_paths and pr_non_docs)
        )
    }
    if dynamic_changes:
        reasons.append(
            f"rule 5: cannot analyze dynamic loading in {sorted(dynamic_changes)[0]}"
        )

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


def _valid_api_path(value: Any) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or "\\" in value
        or PurePosixPath(value).is_absolute()
        or re.match(r"^[A-Za-z]:/", value) is not None
    ):
        return False
    return all(part not in {"", ".."} for part in value.split("/"))


def _valid_api_file_list(records: list[dict[str, Any]]) -> bool:
    filenames = [record.get("filename") for record in records]
    if not all(_valid_api_path(filename) for filename in filenames):
        return False
    if len(set(filenames)) != len(filenames):
        return False
    return all(
        record.get("previous_filename") is None
        or _valid_api_path(record.get("previous_filename"))
        for record in records
    )


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
        if not _valid_api_path(filename):
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
                previous_path = _text(previous_filename)
            except ValueError:
                complete = False
            else:
                if _valid_api_path(previous_path):
                    paths.append(previous_path)
                else:
                    complete = False
        elif previous_filename is not None:
            try:
                previous_path = _text(previous_filename)
            except ValueError:
                complete = False
            else:
                if _valid_api_path(previous_path):
                    paths.append(previous_path)
                else:
                    complete = False
    return paths, complete


def _patch_imports(
    filename: str,
    patch: str,
    root: Path,
    module_index: dict[str, str],
    package_index: dict[str, str],
    package_src_roots: list[Path],
) -> tuple[set[str], set[str], bool, bool]:
    dependencies: set[str] = set()
    unresolved: set[str] = set()
    complete = True
    dynamic = False
    try:
        module_name, is_package = _module_name(
            root / PurePosixPath(filename), root, package_src_roots
        )
    except ValueError:
        return dependencies, unresolved, False, False

    saw_dynamic_import_mention = False
    parsed_dynamic_import_mention = False
    for line in patch.splitlines():
        if not line.startswith("+") or line.startswith("+++"):
            continue
        source = line[1:].strip()
        if not source:
            continue
        mentions_dynamic_import = (
            _DYNAMIC_IMPORT_MENTION_IN_PATCH.search(source) is not None
        )
        saw_dynamic_import_mention = (
            saw_dynamic_import_mention or mentions_dynamic_import
        )
        looks_like_import = re.match(r"(?:import|from)\b", source) is not None
        try:
            tree = ast.parse(source, filename=filename)
        except SyntaxError:
            if looks_like_import:
                complete = False
            if _DYNAMIC_CALL_IN_PATCH.search(source):
                dynamic = True
            continue

        parsed_dynamic_import_mention = (
            parsed_dynamic_import_mention or mentions_dynamic_import
        )
        dependencies.update(
            _python_imports(
                tree,
                module_name,
                is_package,
                module_index,
                package_index,
                unresolved,
                include_dynamic_package_ancestors=True,
            )
        )
        aliases = _dynamic_import_aliases(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_dynamic_load_call(
                root, node, module_index, package_index, aliases
            ):
                dynamic = True
    if saw_dynamic_import_mention and not parsed_dynamic_import_mention:
        dynamic = True
    return dependencies, unresolved, complete, dynamic


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
    root: Path,
    dynamic: set[str],
    module_index: dict[str, str],
    package_index: dict[str, str],
    package_src_roots: list[Path],
    selection_error: str | None = None,
    pull_detail: dict[str, Any] | None = None,
) -> tuple[bool, list[str]]:
    number = _number(number)
    if selection_error is None:
        default_branch = _text(default_branch)
    pull = (
        pull_detail
        if pull_detail is not None
        else _fetch(fetcher, f"repos/{repo}/pulls/{number}")
    )
    if not isinstance(pull, dict):
        raise ValueError("invalid pull request response")
    head = pull.get("head")
    head_sha = _text(head.get("sha") if isinstance(head, dict) else None)
    head_state["sha"] = head_sha
    if selection_error is not None:
        raise ValueError(selection_error)
    base = pull.get("base")
    base_ref = _text(base.get("ref") if isinstance(base, dict) else None)
    if base_ref != default_branch:
        raise ValueError("pull request base does not match the repository default branch")

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
    if merge_base_sha is None:
        raise ValueError("comparison is missing its merge base")
    changed_files = _count(pull.get("changed_files"))
    if behind_by == 0:
        return False, ["up to date"]

    pr_records = _records(
        _fetch(fetcher, f"repos/{repo}/pulls/{number}/files?per_page=100", paginate=True)
    )
    filenames = [
        record.get("filename")
        for record in pr_records
        if isinstance(record.get("filename"), str)
    ]
    if (
        len(pr_records) != changed_files
        or len(set(filenames)) != len(pr_records)
        or not _valid_api_file_list(pr_records)
    ):
        raise ValueError("pull request file list is incomplete")
    main_comparison = _fetch(
        fetcher, f"repos/{repo}/compare/{merge_base_sha}...{base_ref}"
    )
    if not isinstance(main_comparison, dict):
        raise ValueError("invalid base comparison response")
    main_records = _records(main_comparison.get("files"))
    if not _valid_api_file_list(main_records):
        raise ValueError("base comparison file list is incomplete")
    pr_paths, pr_complete = _changed_paths(pr_records)
    main_paths, main_complete = _changed_paths(main_records)
    pr_graph = {path: set(dependencies) for path, dependencies in graph.items()}
    pr_dynamic = set(dynamic)
    unresolved_imports = {
        path: set(names)
        for path, names in getattr(graph, "unresolved_imports", {}).items()
    }
    for record in pr_records:
        filename = record.get("filename")
        status = record.get("status")
        if (
            not isinstance(filename, str)
            or not filename.endswith(".py")
            or status not in {"added", "modified", "renamed"}
        ):
            continue
        if status == "renamed" and type(record.get("changes")) is int:
            if record["changes"] == 0:
                continue
        patch = record.get("patch")
        if not isinstance(patch, str):
            pr_complete = False
            continue
        dependencies, patch_unresolved, patch_complete, patch_dynamic = _patch_imports(
            filename,
            patch,
            root,
            module_index,
            package_index,
            package_src_roots,
        )
        pr_graph.setdefault(filename, set()).update(dependencies)
        unresolved_imports.setdefault(filename, set()).update(patch_unresolved)
        pr_complete = pr_complete and patch_complete
        if patch_dynamic:
            pr_dynamic.add(filename)
    removed_modules, removed_top_levels = _removed_main_modules(
        main_records, root, package_src_roots
    )
    return decide(
        pr_paths,
        main_paths,
        pr_graph,
        complete=pr_complete and main_complete,
        dynamic=pr_dynamic,
        unresolved_imports=unresolved_imports,
        removed_main_modules=removed_modules,
        removed_main_top_levels=removed_top_levels,
    )


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
        base = pull.get("base")
        try:
            base_ref = _text(base.get("ref") if isinstance(base, dict) else None)
        except ValueError:
            base_ref = None
            errors.append("open pull request has an invalid base ref")
        if base_ref is not None and base_ref != default_branch:
            continue
        try:
            number = _number(pull.get("number"))
        except ValueError:
            number = None
            errors.append("open pull request has an invalid number")
        head = pull.get("head")
        try:
            head_sha = _text(head.get("sha") if isinstance(head, dict) else None)
        except ValueError:
            head_sha = ""
            errors.append("open pull request has an invalid head sha")
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
    dynamic: set[str] = set()
    module_index: dict[str, str] = {}
    package_index: dict[str, str] = {}
    package_src_roots: list[Path] = []
    try:
        graph = build_import_graph(args.root)
        if isinstance(graph, _ImportGraph):
            dynamic = set(graph.dynamic)
            module_index = graph.module_index
            package_index = graph.package_index
            package_src_roots = graph.package_src_roots
    except Exception as exc:
        graph = {}
        graph_error = exc
        if not args.post:
            print(f"blast-radius error: {type(exc).__name__}", file=sys.stderr)
            return 1

    single_pull_detail: dict[str, Any] | None = None
    try:
        if args.all_open:
            default_branch, pulls = _pull_numbers(api, args.repo)
        else:
            number = _number(args.pr)
            single_pull_detail = _fetch(
                api, f"repos/{args.repo}/pulls/{number}"
            )
            if not isinstance(single_pull_detail, dict):
                raise ValueError("invalid pull request response")
            head = single_pull_detail.get("head")
            try:
                head_hint = _text(
                    head.get("sha") if isinstance(head, dict) else None
                )
            except ValueError:
                head_hint = ""
            try:
                default_branch = _default_branch(api, args.repo)
                selection_error = None
            except Exception as exc:
                default_branch = ""
                selection_error = (
                    f"repository metadata lookup failed ({type(exc).__name__})"
                )
            pulls = [(number, head_hint, selection_error)]
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
                root=args.root.resolve(),
                dynamic=dynamic,
                module_index=module_index,
                package_index=package_index,
                package_src_roots=package_src_roots,
                selection_error=selection_error,
                pull_detail=single_pull_detail,
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
