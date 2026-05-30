#!/usr/bin/env python3
"""Mechanical duplicate-function detector.

Walks every FunctionDef/AsyncFunctionDef under the source roots, normalizes
the body (strips docstrings, optionally anonymizes arg/local names), hashes
it, and groups by hash. Emits JSON clusters of >=2 functions sharing a body.

Two passes:
  exact  -> identical normalized source (whitespace/docstring insensitive)
  near   -> identical structure with arg/local NAMES anonymized

False-positive classes (dunders, trivial stubs, plugin protocol methods,
test fixtures) are filtered out so the report isn't 90% noise.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import sys
from pathlib import Path

ROOTS = ["kollabor", "packages", "plugins"]

# Names that are SUPPOSED to be reimplemented per class/plugin -- not dupes.
NAME_DENYLIST = {
    "__init__", "__repr__", "__str__", "__eq__", "__hash__", "__enter__",
    "__exit__", "__aenter__", "__aexit__", "__call__", "__len__",
    "setUp", "tearDown", "setUpClass", "tearDownClass", "asyncSetUp",
    "asyncTearDown",
    # plugin protocol surface (CLAUDE.md): per-plugin by design
    "initialize", "shutdown", "register_hooks", "get_status_line",
    "get_default_config", "get_config_widgets", "render_frame",
}

MIN_STATEMENTS = 3  # skip trivial stubs (pass / single return / one assign)


def iter_py_files(base: Path):
    for root in ROOTS:
        rp = base / root
        if not rp.exists():
            continue
        for p in rp.rglob("*.py"):
            if "__pycache__" in p.parts:
                continue
            yield p


def strip_docstring(node: ast.AST) -> None:
    """Remove a leading string-expression docstring from a function body."""
    body = getattr(node, "body", None)
    if not body:
        return
    first = body[0]
    if isinstance(first, ast.Expr) and isinstance(
        getattr(first, "value", None), ast.Constant
    ) and isinstance(first.value.value, str):
        node.body = body[1:]


def count_statements(node: ast.AST) -> int:
    return sum(1 for _ in ast.walk(node) if isinstance(_, ast.stmt))


class Anonymizer(ast.NodeTransformer):
    """Rename arg + locally-bound names to positional placeholders so two
    functions that differ only in variable naming hash identically."""

    def __init__(self) -> None:
        self.mapping: dict[str, str] = {}
        self.counter = 0

    def _placeholder(self, name: str) -> str:
        if name not in self.mapping:
            self.mapping[name] = f"_v{self.counter}"
            self.counter += 1
        return self.mapping[name]

    def visit_arg(self, node: ast.arg):
        node.arg = self._placeholder(node.arg)
        node.annotation = None
        return node

    def visit_Name(self, node: ast.Name):
        if node.id in self.mapping:
            node.id = self.mapping[node.id]
        elif isinstance(node.ctx, ast.Store):
            node.id = self._placeholder(node.id)
        return node


def normalize(node, anonymize: bool) -> str:
    # work on a copy so we don't mutate the shared tree between passes
    clone = ast.parse(ast.unparse(node)).body[0]
    strip_docstring(clone)
    clone.name = "_f"  # function name itself is irrelevant
    clone.decorator_list = []
    if anonymize:
        clone = Anonymizer().visit(clone)
        ast.fix_missing_locations(clone)
    return ast.unparse(clone)


def collect(base: Path, anonymize: bool):
    buckets: dict[str, list[dict]] = {}
    for path in iter_py_files(base):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name in NAME_DENYLIST:
                continue
            if count_statements(node) < MIN_STATEMENTS:
                continue
            try:
                norm = normalize(node, anonymize)
            except Exception:
                continue
            # skip bodies that are pure pragma after normalization
            stripped = norm.split("\n", 1)[1] if "\n" in norm else ""
            if stripped.strip() in ("pass", "...", ""):
                continue
            h = hashlib.sha256(norm.encode()).hexdigest()[:16]
            buckets.setdefault(h, []).append(
                {
                    "file": str(path.relative_to(base)),
                    "line": node.lineno,
                    "name": node.name,
                    "loc": count_statements(node),
                    "norm": norm,
                }
            )
    clusters = []
    for h, members in buckets.items():
        if len(members) < 2:
            continue
        files = {m["file"] for m in members}
        clusters.append(
            {
                "hash": h,
                "count": len(members),
                "cross_file": len(files) > 1,
                "loc": members[0]["loc"],
                "members": [
                    {k: m[k] for k in ("file", "line", "name", "loc")}
                    for m in members
                ],
                "sample": members[0]["norm"],
            }
        )
    # rank: cross-file first, then bigger functions, then more copies
    clusters.sort(
        key=lambda c: (c["cross_file"], c["loc"], c["count"]), reverse=True
    )
    return clusters


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=".")
    ap.add_argument(
        "--mode", choices=["exact", "near"], default="exact",
        help="exact = identical body; near = anonymized var names",
    )
    ap.add_argument("--out", default=None)
    ap.add_argument(
        "--summary", action="store_true", help="print human summary to stderr"
    )
    args = ap.parse_args()

    base = Path(args.base).resolve()
    clusters = collect(base, anonymize=(args.mode == "near"))

    payload = {
        "mode": args.mode,
        "base": str(base),
        "cluster_count": len(clusters),
        "cross_file_clusters": sum(1 for c in clusters if c["cross_file"]),
        "total_duplicate_funcs": sum(c["count"] for c in clusters),
        "clusters": clusters,
    }

    out = json.dumps(payload, indent=2)
    if args.out:
        Path(args.out).write_text(out)
    else:
        print(out)

    if args.summary:
        print(
            f"[{args.mode}] {payload['cluster_count']} clusters "
            f"({payload['cross_file_clusters']} cross-file), "
            f"{payload['total_duplicate_funcs']} dup funcs",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
