"""Local release-note loading for the updates AltView."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional


@dataclass(frozen=True)
class ReleaseNoteSection:
    """A categorized group of release-note bullets."""

    title: str
    items: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ReleaseNote:
    """Release notes for one version."""

    version: str
    date: str = ""
    sections: list[ReleaseNoteSection] = field(default_factory=list)


_VERSION_RE = re.compile(
    r"^##\s+\[(?P<version>[^\]]+)\](?:\s+-\s+(?P<date>\d{4}-\d{2}-\d{2}))?\s*$"
)
_SECTION_RE = re.compile(r"^###\s+(?P<title>.+?)\s*$")
_BULLET_RE = re.compile(r"^\s*-\s+(?P<text>.+?)\s*$")


def parse_changelog(
    text: str,
    *,
    limit: int = 8,
    include_unreleased: bool = False,
) -> list[ReleaseNote]:
    """Parse recent Keep a Changelog sections from Markdown."""

    notes: list[ReleaseNote] = []
    current_version = ""
    current_date = ""
    current_sections: list[ReleaseNoteSection] = []
    current_section: Optional[ReleaseNoteSection] = None
    current_item: list[str] = []

    def flush_item() -> None:
        nonlocal current_item
        if current_section is not None and current_item:
            current_section.items.append(
                " ".join(part.strip() for part in current_item)
            )
        current_item = []

    def flush_note() -> None:
        nonlocal current_version, current_date, current_sections, current_section
        flush_item()
        if not current_version:
            return
        has_items = any(section.items for section in current_sections)
        if current_version.lower() != "unreleased" or (
            include_unreleased and has_items
        ):
            if has_items:
                notes.append(
                    ReleaseNote(
                        version=current_version,
                        date=current_date,
                        sections=[
                            section for section in current_sections if section.items
                        ],
                    )
                )
        current_version = ""
        current_date = ""
        current_sections = []
        current_section = None

    for raw_line in text.splitlines():
        line = raw_line.rstrip()

        version_match = _VERSION_RE.match(line)
        if version_match:
            flush_note()
            current_version = version_match.group("version").strip()
            current_date = (version_match.group("date") or "").strip()
            if len(notes) >= limit:
                break
            continue

        if not current_version:
            continue

        section_match = _SECTION_RE.match(line)
        if section_match:
            flush_item()
            current_section = ReleaseNoteSection(section_match.group("title").strip())
            current_sections.append(current_section)
            continue

        bullet_match = _BULLET_RE.match(line)
        if bullet_match and current_section is not None:
            flush_item()
            current_item = [bullet_match.group("text").strip()]
            continue

        if current_item and _is_continuation_line(raw_line):
            current_item.append(line.strip())
            continue

        if not line.strip():
            flush_item()

    if len(notes) < limit:
        flush_note()

    return notes[:limit]


def load_recent_release_notes(
    changelog_path: Optional[Path] = None,
    *,
    limit: int = 8,
) -> list[ReleaseNote]:
    """Load recent release notes from the local changelog if available."""

    path = changelog_path or find_changelog_path()
    if path is None:
        return []
    try:
        return parse_changelog(path.read_text(encoding="utf-8"), limit=limit)
    except OSError:
        return []


def find_changelog_path(extra_candidates: Iterable[Path] = ()) -> Optional[Path]:
    """Find the Kollab changelog in source or packaged-resource layouts."""

    env_path = os.environ.get("KOLLAB_CHANGELOG_PATH")
    candidates: list[Path] = []
    if env_path:
        candidates.append(Path(env_path).expanduser())

    module_path = Path(__file__).resolve()
    candidates.extend(
        [
            module_path.parents[2] / "CHANGELOG.md",
            module_path.with_name("CHANGELOG.md"),
            *extra_candidates,
        ]
    )

    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


def _is_continuation_line(raw_line: str) -> bool:
    stripped = raw_line.strip()
    return bool(stripped) and raw_line[:1].isspace() and not stripped.startswith("- ")
