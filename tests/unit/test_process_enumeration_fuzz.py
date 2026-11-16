"""Generated security checks for process enumeration boundaries."""

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENUMERATION_METHODS = {"process_iter", "pids", "environ", "children"}
REQUIRED_METHODS = {"process_iter", "pids", "environ", "children"}

# Each production call site must name a property/fuzz test that exercises its
# method. This starts empty so the guard exposes uncovered boundaries first.
COVERED_CALLS: dict[tuple[str, str, str], str] = {}


def _enumeration_calls() -> set[tuple[str, str, str]]:
    """Find psutil enumeration calls in production Python source."""
    calls: set[tuple[str, str, str]] = set()
    for source_root in (REPO_ROOT / "kollabor", REPO_ROOT / "plugins", REPO_ROOT / "packages"):
        for path in source_root.rglob("*.py"):
            if any(part in {"node_modules", ".venv", "__pycache__"} for part in path.parts):
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, SyntaxError):
                continue
            imports_psutil = any(
                isinstance(node, ast.Import)
                and any(alias.name == "psutil" for alias in node.names)
                or isinstance(node, ast.ImportFrom)
                and node.module == "psutil"
                for node in ast.walk(tree)
            )
            if not imports_psutil:
                continue

            class Calls(ast.NodeVisitor):
                def __init__(self) -> None:
                    self.functions: list[str] = []

                def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                    self.functions.append(node.name)
                    self.generic_visit(node)
                    self.functions.pop()

                visit_AsyncFunctionDef = visit_FunctionDef

                def visit_Call(self, node: ast.Call) -> None:
                    func = node.func
                    if isinstance(func, ast.Attribute) and func.attr in ENUMERATION_METHODS:
                        calls.add(
                            (
                                path.relative_to(REPO_ROOT).as_posix(),
                                self.functions[-1] if self.functions else "<module>",
                                func.attr,
                            )
                        )
                    self.generic_visit(node)

            Calls().visit(tree)
    return calls


def test_every_process_enumerator_has_property_fuzz_coverage() -> None:
    """Fail when an enumeration API or call site has no named property test."""
    missing_methods = REQUIRED_METHODS - {method for _, _, method in COVERED_CALLS}
    missing_sites = _enumeration_calls() - set(COVERED_CALLS)
    assert not missing_methods and not missing_sites, (
        f"missing fuzz coverage methods={sorted(missing_methods)} "
        f"sites={sorted(missing_sites)}"
    )
    missing_tests = sorted(set(COVERED_CALLS.values()) - globals().keys())
    assert not missing_tests, f"coverage test functions not defined: {missing_tests}"
