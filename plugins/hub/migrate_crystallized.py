"""One-time migration: convert raw crystallized.md to structured format.

Parses the existing dash-prefixed paragraph format with bold headers
and date markers into structured CrystalEntry format with IDs,
keywords, summaries, and deduplication.

Usage:
    python -m plugins.hub.migrate_crystallized [vault_name]
    python -m plugins.hub.migrate_crystallized --all
"""

import re
import shutil
import sys

from .crystal_store import CrystalStore
from .text_utils import extract_keywords, keyword_overlap
from .vault import get_global_vaults_dir, get_vaults_dir


def parse_raw_entries(text: str) -> list[dict]:
    """Parse raw crystallized.md into individual insight entries.

    Each entry gets a date (from nearest [YYYY-MM-DD] marker)
    and text (from bold header + body or plain paragraph).
    """
    entries: list[dict] = []
    current_date = "2026-04-09"  # fallback

    # Split into lines for processing
    lines = text.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i].strip()

        # Date marker: - [YYYY-MM-DD] ...
        date_match = re.match(r"^-?\s*\[(\d{4}-\d{2}-\d{2})\](.*)$", line)
        if date_match:
            current_date = date_match.group(1)
            rest = date_match.group(2).strip()
            # If the rest starts with a bold header, treat as entry
            if rest and "**" in rest:
                entry_text = _collect_paragraph(lines, i, rest)
                entries.append({"date": current_date, "text": entry_text})
            # Otherwise it's just a date marker (intro text), skip
            i += 1
            continue

        # Numbered insight: 1. **Bold header** ...
        num_match = re.match(r"^\d+\.\s+\*\*(.+)", line)
        if num_match:
            entry_text = _collect_paragraph(lines, i)
            # Strip the number prefix
            entry_text = re.sub(r"^\d+\.\s+", "", entry_text)
            entries.append({"date": current_date, "text": entry_text})
            i += 1
            # Skip continuation lines
            while i < len(lines) and lines[i].strip() and not _is_entry_start(lines[i]):
                i += 1
            continue

        # Bold header: **text** ...
        if line.startswith("**") and "**" in line[2:]:
            entry_text = _collect_paragraph(lines, i)
            entries.append({"date": current_date, "text": entry_text})
            i += 1
            # Skip continuation lines
            while i < len(lines) and lines[i].strip() and not _is_entry_start(lines[i]):
                i += 1
            continue

        # Plain paragraph (non-empty, not a header)
        if line and not line.startswith("---") and not line.startswith("Looking at"):
            entry_text = _collect_paragraph(lines, i)
            if len(entry_text) > 50:  # Skip tiny fragments
                entries.append({"date": current_date, "text": entry_text})
            i += 1
            while i < len(lines) and lines[i].strip() and not _is_entry_start(lines[i]):
                i += 1
            continue

        i += 1

    return entries


def _is_entry_start(line: str) -> bool:
    """Check if a line starts a new entry."""
    line = line.strip()
    if not line:
        return True
    if line.startswith("**"):
        return True
    if re.match(r"^\d+\.\s+\*\*", line):
        return True
    if re.match(r"^-?\s*\[\d{4}-\d{2}-\d{2}\]", line):
        return True
    return False


def _collect_paragraph(lines: list[str], start: int, prefix: str = "") -> str:
    """Collect a full paragraph starting at line index."""
    parts = [prefix] if prefix else [lines[start].strip()]
    i = start + 1
    while i < len(lines):
        line = lines[i].strip()
        if not line or _is_entry_start(line):
            break
        parts.append(line)
        i += 1
    return " ".join(parts)


def migrate_vault(vault_name: str, dry_run: bool = False) -> dict:
    """Migrate a single vault's crystallized.md to structured format.

    Returns stats dict with counts.
    """
    vault_dir = get_vaults_dir() / vault_name
    global_vault_dir = get_global_vaults_dir() / vault_name

    # Check both project-scoped and global locations for source
    crystal_path = global_vault_dir / "crystallized.md"
    if not crystal_path.exists():
        crystal_path = vault_dir / "crystallized.md"
    if not crystal_path.exists():
        return {"vault": vault_name, "status": "no crystallized.md"}

    raw_text = crystal_path.read_text()
    if not raw_text.strip():
        return {"vault": vault_name, "status": "empty"}

    # Already structured?
    if raw_text.lstrip().startswith("---\nid:"):
        return {"vault": vault_name, "status": "already structured"}

    # Parse raw entries
    raw_entries = parse_raw_entries(raw_text)
    if not raw_entries:
        return {"vault": vault_name, "status": "no entries parsed"}

    # Build structured entries with dedup — always write to global
    store = CrystalStore(global_vault_dir)
    added = 0
    merged = 0

    for entry_data in raw_entries:
        text = entry_data["text"]
        date = entry_data["date"]

        # Check against already-added entries for dedup
        new_kw = extract_keywords(text)
        is_dup = False
        for existing in store.get_all():
            if keyword_overlap(new_kw, existing.keywords) > 0.55:
                is_dup = True
                merged += 1
                break

        if not is_dup:
            store.add_entry(text, date=date)
            added += 1

    if dry_run:
        return {
            "vault": vault_name,
            "status": "dry_run",
            "raw_entries": len(raw_entries),
            "unique": added,
            "duplicates": merged,
        }

    # Backup original
    backup_path = crystal_path.with_suffix(".md.raw_backup")
    if not backup_path.exists():
        shutil.copy2(crystal_path, backup_path)

    return {
        "vault": vault_name,
        "status": "migrated",
        "raw_entries": len(raw_entries),
        "unique": added,
        "duplicates": merged,
        "backup": str(backup_path),
    }


def migrate_all(dry_run: bool = False) -> list[dict]:
    """Migrate all vaults."""
    vaults_dir = get_vaults_dir()
    results = []
    for vault_dir in sorted(vaults_dir.iterdir()):
        if vault_dir.is_dir() and not vault_dir.name.startswith("_"):
            results.append(migrate_vault(vault_dir.name, dry_run=dry_run))
    return results


if __name__ == "__main__":
    args = sys.argv[1:]
    dry_run = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]

    if "--all" in args:
        results = migrate_all(dry_run=dry_run)
        for r in results:
            print(f"  {r['vault']:15} {r['status']}", end="")
            if "raw_entries" in r:
                print(
                    f" ({r['raw_entries']} raw -> "
                    f"{r['unique']} unique, "
                    f"{r['duplicates']} dupes)"
                )
            else:
                print()
    elif args:
        result = migrate_vault(args[0], dry_run=dry_run)
        print(result)
    else:
        print("usage: python -m plugins.hub.migrate_crystallized [--all|vault_name] [--dry-run]")
