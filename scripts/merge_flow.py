#!/usr/bin/env python3
"""Merge flow, dry run (#1472): plan one merge that lands every ready PR.

Ready PRs keep their heads. Every PR is checked against main, and every pair of PRs that
shares a file is merged with ``git merge-tree`` (seconds, no CI). A clash marks the later
PR for a fix, after any fix it depends on. The rest pair up by shared files into one
combined commit, which merges onto main as the window commit. Merges are git objects
only: nothing is pushed and nothing is written to GitHub.

    python scripts/merge_flow.py                  # every ready PR
    python scripts/merge_flow.py --prs 1450,1458  # just these
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REQUIRED_CHECKS = ("ci/tests", "ci/standards-check", "ci/security-scan")
IGNORED_PREFIXES = ("changes/",)  # one fragment per PR, never shared
CI_MINUTES = 7.2  # median green PR CI run after #1417
FIX_MINUTES = 8.4  # average pr-chain agent run
FIX_AGENTS = 4
_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "merge-flow",
    "GIT_AUTHOR_EMAIL": "merge-flow@users.noreply.github.com",
    "GIT_COMMITTER_NAME": "merge-flow",
    "GIT_COMMITTER_EMAIL": "merge-flow@users.noreply.github.com",
}


@dataclass(frozen=True)
class Head:
    number: int
    sha: str
    files: frozenset[str] = frozenset()


def git(repo: Path, *args: str, ok: tuple[int, ...] = (0,)) -> tuple[int, str]:
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=_ENV)
    if proc.returncode not in ok:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.returncode, proc.stdout.strip()


def changed_files(repo: Path, base: str, sha: str) -> frozenset[str]:
    """Files the PR changes since it branched from ``base``, changelog fragments left out."""
    fork = git(repo, "merge-base", base, sha)[1]
    names = git(repo, "diff", "--name-only", fork, sha)[1].splitlines()
    return frozenset(n for n in names if n and not n.startswith(IGNORED_PREFIXES))


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    return len(a & b) / len(a | b) if a | b else 0.0


def clash(repo: Path, x: str, y: str) -> list[str] | None:
    """Files that clash when ``x`` and ``y`` merge, or None when they merge cleanly."""
    rc, out = git(repo, "merge-tree", "--write-tree", "--name-only", "--no-messages", x, y, ok=(0, 1))
    return sorted({line for line in out.splitlines()[1:] if line}) if rc else None


def merge(repo: Path, x: str, y: str, message: str) -> str | None:
    """A merge commit with parents ``x`` and ``y``, or None on a clash."""
    rc, out = git(repo, "merge-tree", "--write-tree", x, y, ok=(0, 1))
    if rc:
        return None
    return git(repo, "commit-tree", out.splitlines()[0], "-p", x, "-p", y, "-m", message)[1]


def _build_tree(repo: Path, heads: list[Head]) -> tuple[list[dict[str, Any]], list[list[list[int]]]]:
    """Pair the most similar nodes level by level until one combined commit is left."""
    nodes = [{"sha": h.sha, "files": h.files, "prs": [h.number]} for h in heads]
    failed: set[frozenset[int]] = set()
    levels: list[list[list[int]]] = []
    while len(nodes) > 1:
        pairs = sorted(
            ((i, j) for i in range(len(nodes)) for j in range(i + 1, len(nodes))),
            key=lambda p: (-jaccard(nodes[p[0]]["files"], nodes[p[1]]["files"]), p),
        )
        used: set[int] = set()
        merged: list[dict[str, Any]] = []
        for i, j in pairs:
            a, b = nodes[i], nodes[j]
            key = frozenset((min(a["prs"]), min(b["prs"])))
            if i in used or j in used or key in failed:
                continue
            sha = merge(repo, a["sha"], b["sha"], f"merge-flow: #{min(a['prs'])} + #{min(b['prs'])}")
            if sha is None:  # a clash only three or more PRs make together
                failed.add(key)
                continue
            used |= {i, j}
            merged.append({"sha": sha, "files": a["files"] | b["files"], "prs": sorted(a["prs"] + b["prs"])})
        if not merged:
            break
        levels.append([m["prs"] for m in merged])
        nodes = merged + [n for k, n in enumerate(nodes) if k not in used]
    return nodes, levels


def plan(repo: Path, base: str, heads: list[Head], agents: int = FIX_AGENTS) -> dict[str, Any]:
    """Plan one window: what clashes, what needs a fix and in which order, what lands now.

    ``heads`` are in ready order; of two clashing PRs the later one takes the fix.
    """
    clashes: list[dict[str, Any]] = []
    depth: dict[int, int] = {}
    for j, b in enumerate(heads):
        partners: list[int] = []
        files = clash(repo, base, b.sha)
        if files is not None:
            clashes.append({"prs": [b.number], "with": "main", "files": files})
        for a in heads[:j]:
            if a.files & b.files and (pair := clash(repo, a.sha, b.sha)) is not None:
                clashes.append({"prs": [a.number, b.number], "files": pair})
                partners.append(a.number)
        if files is not None or partners:
            depth[b.number] = 1 + max((depth.get(p, 0) for p in partners), default=0)

    window = [h for h in heads if h.number not in depth]
    roots, levels = _build_tree(repo, window)
    roots.sort(key=lambda n: -len(n["prs"]))
    window_commit, landed, held = None, [], []
    current = base
    for node in roots:
        sha = merge(repo, current, node["sha"], f"merge-flow: land {len(node['prs'])} PRs")
        if sha is None:
            held += node["prs"]
            continue
        current, window_commit = sha, sha
        landed += node["prs"]

    per_depth = [sum(1 for d in depth.values() if d == lv) for lv in range(1, max(depth.values(), default=0) + 1)]
    fix_rounds = sum(math.ceil(n / agents) for n in per_depth)
    return {
        "base": base,
        "prs": [h.number for h in heads],
        "clashes": clashes,
        "needs_fix": [{"pr": n, "depth": d} for n, d in sorted(depth.items(), key=lambda kv: (kv[1], kv[0]))],
        "fix_rounds": fix_rounds,
        "levels": levels,
        "landed": sorted(landed),
        "held": sorted(held),
        "window_commit": window_commit,
        "minutes_to_land": round(fix_rounds * FIX_MINUTES + CI_MINUTES, 1),
    }


def _passed(check: dict[str, Any]) -> tuple[str, bool]:
    name = check.get("name") or check.get("context") or ""
    return name, (check.get("conclusion") or check.get("state")) == "SUCCESS"


def ready_prs(items: list[dict[str, Any]], required: tuple[str, ...] = REQUIRED_CHECKS) -> list[int]:
    """Numbers of open PRs that are not drafts, are approved and pass every required check."""
    ready = []
    for item in items:
        passed = {name for name, ok in map(_passed, item.get("statusCheckRollup") or []) if ok}
        if not item.get("isDraft") and item.get("reviewDecision") == "APPROVED" and set(required) <= passed:
            ready.append(item["number"])
    return sorted(ready)


def _gh_open_prs(repo: str) -> list[dict[str, Any]]:
    fields = "number,isDraft,reviewDecision,statusCheckRollup"
    out = subprocess.run(
        ["gh", "pr", "list", "--repo", repo, "--state", "open", "--limit", "200", "--json", fields],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return json.loads(out)


def _summary(result: dict[str, Any]) -> str:
    lines = [f"base {result['base'][:9]}, {len(result['prs'])} ready PRs"]
    for c in result["clashes"]:
        who = " + ".join(f"#{n}" for n in c["prs"]) + (" vs main" if c.get("with") == "main" else "")
        lines.append(f"  clash {who}: {', '.join(c['files'])}")
    for fix in result["needs_fix"]:
        lines.append(f"  fix #{fix['pr']} (round {fix['depth']})")
    landed = ", ".join(f"#{n}" for n in result["landed"]) or "none"
    lines.append(f"lands now in one merge: {landed}")
    if result["held"]:
        lines.append(f"held (clash only in combination): {', '.join(f'#{n}' for n in result['held'])}")
    lines.append(f"fix rounds {result['fix_rounds']}, about {result['minutes_to_land']} min to land all")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo", default="kollaborai/kollab")
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--base", default="main")
    parser.add_argument("--prs", help="comma-separated PR numbers in ready order; default: every ready PR")
    parser.add_argument("--json", type=Path, help="also write the plan here")
    args = parser.parse_args(argv)

    root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel")[1])
    numbers = [int(n) for n in args.prs.split(",")] if args.prs else ready_prs(_gh_open_prs(args.repo))
    refspecs = [f"+refs/heads/{args.base}:refs/merge-flow/base"]
    refspecs += [f"+refs/pull/{n}/head:refs/merge-flow/pr/{n}" for n in numbers]
    git(root, "fetch", "-q", args.remote, *refspecs)
    base = git(root, "rev-parse", "refs/merge-flow/base")[1]
    heads = []
    for n in numbers:
        sha = git(root, "rev-parse", f"refs/merge-flow/pr/{n}")[1]
        heads.append(Head(n, sha, changed_files(root, base, sha)))

    result = plan(root, base, heads)
    print(_summary(result))
    if args.json:
        args.json.write_text(json.dumps(result, indent=1) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
