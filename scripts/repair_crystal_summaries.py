#!/usr/bin/env python3
"""Re-extract summary lines on broken crystallized.md files.

Existing entries written before the dreaming-output list-marker fix
have summaries like "1", "2", "3" (the leading list digit) because the
old _extract_summary split on the first "." which landed on the marker
dot. This script walks every crystallized.md under ~/.kollab/, re-runs
the summary extractor against each entry's body, and writes the file
back if anything changed.

Idempotent. Safe to run repeatedly. Backs up each modified file to
<path>.bak before writing.

Usage:
    python scripts/repair_crystal_summaries.py            # repair under ~/.kollab
    python scripts/repair_crystal_summaries.py --dry-run  # report only
    python scripts/repair_crystal_summaries.py /custom/path
"""

import argparse
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from plugins.hub.crystal_store import CrystalStore  # noqa: E402


def looks_broken(summary: str) -> bool:
    """Return True for summaries that are obviously malformed."""
    s = (summary or "").strip()
    if not s:
        return True
    if s.isdigit():
        return True
    if len(s) <= 2:
        return True
    return False


def repair_file(path: Path, dry_run: bool = False) -> tuple[int, int]:
    """Repair one crystallized.md. Returns (fixed_count, total_count)."""
    # CrystalStore takes a vault directory; path is the file inside it.
    store = CrystalStore(path.parent)
    entries = store.load()
    fixed = 0
    for entry in entries:
        if not looks_broken(entry.summary):
            continue
        new_summary = store._extract_summary(entry.body)
        if new_summary and new_summary != entry.summary:
            print(
                f"  {entry.id}: '{entry.summary}' -> '{new_summary[:80]}'"
            )
            entry.summary = new_summary
            fixed += 1
    if fixed and not dry_run:
        backup = path.with_suffix(path.suffix + ".bak")
        shutil.copy2(path, backup)
        store._entries = entries
        store._save_to_disk()
        print(f"  wrote {path} (backup at {backup.name})")
    return fixed, len(entries)


def find_crystal_files(root: Path) -> list[Path]:
    return sorted(root.rglob("crystallized.md"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "root",
        nargs="?",
        default=str(Path.home() / ".kollab"),
        help="Root directory to scan (default: ~/.kollab)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing",
    )
    args = parser.parse_args()

    root = Path(args.root).expanduser()
    if not root.exists():
        print(f"error: {root} does not exist", file=sys.stderr)
        return 1

    files = find_crystal_files(root)
    if not files:
        print(f"no crystallized.md files under {root}")
        return 0

    print(f"scanning {len(files)} crystallized.md files under {root}")
    if args.dry_run:
        print("(dry run - no files will be modified)")
    print()

    total_fixed = 0
    total_entries = 0
    files_touched = 0
    for path in files:
        try:
            fixed, count = repair_file(path, dry_run=args.dry_run)
        except Exception as e:
            print(f"  ERROR {path}: {e}")
            continue
        total_entries += count
        if fixed:
            files_touched += 1
            total_fixed += fixed
            print(f"  {path}: {fixed}/{count} repaired")
            print()

    print()
    print(
        f"summary: {total_fixed} entries repaired across "
        f"{files_touched} files ({total_entries} entries scanned)"
    )
    if args.dry_run and total_fixed:
        print("re-run without --dry-run to apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
