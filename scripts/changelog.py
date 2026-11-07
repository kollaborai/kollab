#!/usr/bin/env python3
"""Changelog entries as one file per PR, folded into both changelog copies at release time (#855).

A PR adds ``changes/<issue>.<section>.md`` instead of editing ``CHANGELOG.md``. ``<section>`` is one of
added, changed, deprecated, removed, fixed, security, and the file holds the entry as Markdown bullets.
Two PRs never write the same file, so their entries cannot conflict. See ``changes/README.md``.

    python scripts/changelog.py check [--diff-base REV] [--branch NAME]
        Validate the entry files. With --diff-base, also refuse a direct edit of either changelog copy
        between REV and HEAD, unless the branch starts with release/ or changelog/.
    python scripts/changelog.py build
        Put every entry first under its heading in ``## [Unreleased]`` of both copies (one heading per
        section, Keep a Changelog order), keep the copies byte-identical, and delete the entry files.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECTIONS = ("added", "changed", "deprecated", "removed", "fixed", "security")
COPIES = ("CHANGELOG.md", "kollabor/updates/CHANGELOG.md")
EDIT_BRANCHES = ("release/", "changelog/")
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.(%s)\.md$" % "|".join(SECTIONS))
UNRELEASED = "## [Unreleased]"


def entry_files(root: Path) -> list[Path]:
    """Every entry file in changes/, by name. README.md explains the folder and is not an entry."""
    folder = root / "changes"
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.is_file() and p.name != "README.md")


def problems(root: Path) -> list[str]:
    """One line per invalid entry file; empty when every entry is valid."""
    found = []
    for path in entry_files(root):
        where = f"changes/{path.name}"
        if not NAME.match(path.name):
            found.append(f"{where}: name it <issue>.<section>.md, with <section> one of {', '.join(SECTIONS)}")
            continue
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not lines:
            found.append(f"{where}: the file is empty")
        elif not lines[0].startswith("- "):
            found.append(f"{where}: an entry starts with a '- ' bullet")
        elif any(not line.startswith(("- ", "  ")) for line in lines):
            found.append(f"{where}: every line is a '- ' bullet or a continuation indented by two spaces")
    return found


def direct_edits(root: Path, base: str) -> list[str]:
    """The changelog copies that differ between ``base`` and HEAD."""
    out = subprocess.run(
        ["git", "diff", "--name-only", base, "HEAD", "--", *COPIES],
        cwd=root, capture_output=True, text=True, check=True,
    ).stdout
    return out.split()


def fold(text: str, entries: dict[str, list[str]]) -> str:
    """``text`` with each section's entry lines first under its heading in ``## [Unreleased]``."""
    lines = text.split("\n")
    try:
        start = lines.index(UNRELEASED)
    except ValueError:
        raise SystemExit(f"the changelog has no '{UNRELEASED}' heading") from None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    block = lines[start + 1 : end]
    heads = [i for i, line in enumerate(block) if line.startswith("### ")]
    lead = block[: heads[0]] if heads else block
    sections = []
    for n, i in enumerate(heads):
        stop = heads[n + 1] if n + 1 < len(heads) else len(block)
        sections.append((block[i][4:].strip(), block[i + 1 : stop]))
    order = {name: i for i, name in enumerate(SECTIONS)}
    for name in SECTIONS:
        new = entries.get(name)
        if not new:
            continue
        body = next((body for title, body in sections if title.lower() == name), None)
        if body is not None:
            body[:0] = new
            continue
        # A missing heading goes after the last heading that comes before it in Keep a Changelog order.
        at = 0
        for n, (title, _) in enumerate(sections):
            if order.get(title.lower(), len(SECTIONS)) < order[name]:
                at = n + 1
        before = sections[at - 1][1] if at else lead
        if not before or before[-1] != "":
            before.append("")
        sections.insert(at, (name.capitalize(), [*new, ""]))
    out = lines[: start + 1] + lead
    for title, body in sections:
        out += [f"### {title}", *body]
    return "\n".join(out + lines[end:])


def natural(name: str) -> list:
    """Sort key that orders 9 before 10."""
    return [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name)]


def build(root: Path) -> int:
    """Fold every entry file into both changelog copies, then delete the files. Returns how many were folded."""
    texts = [(root / copy).read_text(encoding="utf-8") for copy in COPIES]
    if texts[0] != texts[1]:
        raise SystemExit(f"{COPIES[0]} and {COPIES[1]} differ; make them byte-identical first")
    found = problems(root)
    if found:
        raise SystemExit("\n".join(found))
    files = entry_files(root)
    entries: dict[str, list[str]] = {}
    # Newest first, as entries were added by hand: the highest issue number goes on top.
    for path in sorted(files, key=lambda p: natural(p.name), reverse=True):
        section = NAME.match(path.name).group(1)
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        entries.setdefault(section, []).extend(lines)
    text = fold(texts[0], entries) if entries else texts[0]
    for copy in COPIES:
        (root / copy).write_text(text, encoding="utf-8")
    for path in files:
        path.unlink()
    return len(files)


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(description="Changelog entries in changes/ (#855).")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="validate changes/; with --diff-base also refuse direct changelog edits")
    check.add_argument("--diff-base", help="revision the PR is compared with (CI passes HEAD^1)")
    check.add_argument("--branch", default="", help="the PR's head branch")
    sub.add_parser("build", help="fold changes/ into both changelog copies and delete the entry files")
    args = parser.parse_args(argv)
    if args.command == "build":
        count = build(root)
        print(f"folded {count} {'entry' if count == 1 else 'entries'} into {' and '.join(COPIES)}")
        return 0
    found = problems(root)
    if args.diff_base and not args.branch.startswith(EDIT_BRANCHES):
        for copy in direct_edits(root, args.diff_base):
            found.append(
                f"{copy}: edited directly. Put the entry in changes/<issue>.<section>.md "
                f"(see changes/README.md) and restore the file: git restore --source origin/main -- {copy}"
            )
    for line in found:
        print(line, file=sys.stderr)
    if found:
        return 1
    print(f"changelog entries ok: {len(entry_files(root))} in changes/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
