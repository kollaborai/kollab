"""Changelog Unreleased structure regression tests.

A merge wave once landed duplicate ``###`` headers and a verbatim-duplicated
bullet in both changelog mirrors. These guards fail CI on the next
structural regression instead of letting it merge silently. They check
shape only (no duplicate headers, no duplicate bullets) and stay silent
about legitimate empty Unreleased sections, e.g. right after a release.
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent.parent
MIRRORS = (
    REPO / "CHANGELOG.md",
    REPO / "kollabor" / "updates" / "CHANGELOG.md",
)


def _unreleased_section(text: str) -> str:
    match = re.search(r"^## \[Unreleased\]\n(.*?)(?=^## \[|\Z)", text, re.M | re.S)
    assert match is not None, "changelog needs a '## [Unreleased]' section"
    return match.group(1)


@pytest.mark.parametrize("changelog", MIRRORS, ids=lambda p: str(p.relative_to(REPO)))
def test_unreleased_has_no_duplicate_section_headers(changelog: Path) -> None:
    section = _unreleased_section(changelog.read_text())
    headers = re.findall(r"^### (.+)$", section, re.M)
    duplicates = sorted({h for h in headers if headers.count(h) > 1})
    assert not duplicates, f"duplicate ### headers in Unreleased: {duplicates}"


@pytest.mark.parametrize("changelog", MIRRORS, ids=lambda p: str(p.relative_to(REPO)))
def test_unreleased_has_no_duplicate_bullets(changelog: Path) -> None:
    section = _unreleased_section(changelog.read_text())
    bullets = [line for line in section.splitlines() if line.startswith("- ")]
    assert len(set(bullets)) == len(bullets), (
        "verbatim-duplicated bullets in Unreleased: "
        + "; ".join(sorted({b for b in bullets if bullets.count(b) > 1})[:3])
    )
