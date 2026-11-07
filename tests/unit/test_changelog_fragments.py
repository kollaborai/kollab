"""Changelog entries as one file per PR (#855): scripts/changelog.py check and build."""

import importlib.util
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "changelog.py"
_spec = importlib.util.spec_from_file_location("changelog_script", SCRIPT)
changelog = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(changelog)

BASE = """# Changelog

Intro line.

## [Unreleased]

### Added
- Older added entry (#1).

### Fixed
- Older fix (#2).

## [0.1.0] - 2026-01-01

### Added
- First release.
"""


def make_repo(root: Path, text: str = BASE) -> Path:
    (root / "kollabor" / "updates").mkdir(parents=True)
    (root / "changes").mkdir()
    for copy in changelog.COPIES:
        (root / copy).write_text(text, encoding="utf-8")
    (root / "changes" / "README.md").write_text("How to add an entry.\n", encoding="utf-8")
    return root


def entry(root: Path, name: str, text: str) -> None:
    (root / "changes" / name).write_text(text, encoding="utf-8")


def git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=test", "-c", "commit.gpgsign=false",
         "-c", "core.hooksPath=/dev/null", *args],
        cwd=root, check=True, capture_output=True,
    )


def test_build_puts_entries_first_under_their_headings_in_both_copies(tmp_path):
    root = make_repo(tmp_path)
    entry(root, "9.fixed.md", "- Fix nine (#9).\n")
    entry(root, "10.fixed.md", "- Fix ten (#10).\n")
    entry(root, "11.security.md", "- Keys stay out of logs (#11).\n  Second line of the same entry.\n")
    entry(root, "12.added.md", "- New feature (#12).\n")

    assert changelog.build(root) == 4

    expected = """# Changelog

Intro line.

## [Unreleased]

### Added
- New feature (#12).
- Older added entry (#1).

### Fixed
- Fix ten (#10).
- Fix nine (#9).
- Older fix (#2).

### Security
- Keys stay out of logs (#11).
  Second line of the same entry.

## [0.1.0] - 2026-01-01

### Added
- First release.
"""
    assert (root / "CHANGELOG.md").read_text(encoding="utf-8") == expected
    assert (root / "kollabor/updates/CHANGELOG.md").read_text(encoding="utf-8") == expected
    assert sorted(p.name for p in (root / "changes").iterdir()) == ["README.md"]


def test_build_adds_a_missing_heading_before_later_ones(tmp_path):
    root = make_repo(tmp_path, BASE.replace("### Added\n- Older added entry (#1).\n\n", ""))
    entry(root, "5.changed.md", "- Changed thing (#5).\n")

    changelog.build(root)

    unreleased = (root / "CHANGELOG.md").read_text(encoding="utf-8").split("## [0.1.0]")[0]
    assert unreleased.endswith(
        "## [Unreleased]\n\n### Changed\n- Changed thing (#5).\n\n### Fixed\n- Older fix (#2).\n\n"
    )


def test_build_without_entries_changes_nothing(tmp_path):
    root = make_repo(tmp_path)

    assert changelog.build(root) == 0
    assert (root / "CHANGELOG.md").read_text(encoding="utf-8") == BASE


def test_build_refuses_when_the_two_copies_differ(tmp_path):
    root = make_repo(tmp_path)
    (root / "kollabor/updates/CHANGELOG.md").write_text(BASE + "\nDrift.\n", encoding="utf-8")
    entry(root, "10.fixed.md", "- New fix (#10).\n")

    with pytest.raises(SystemExit):
        changelog.build(root)
    assert (root / "changes" / "10.fixed.md").exists()
    assert (root / "CHANGELOG.md").read_text(encoding="utf-8") == BASE


def test_check_names_each_bad_entry_file(tmp_path):
    root = make_repo(tmp_path)
    entry(root, "10.bugfix.md", "- Wrong section.\n")
    entry(root, "11.fixed.md", "\n")
    entry(root, "12.fixed.md", "Fixed a thing.\n")
    entry(root, "13.fixed.md", "- Fine start.\n### Not a heading\n")
    entry(root, "14.fixed.md", "- Good entry (#14).\n  More words.\n")

    found = changelog.problems(root)

    assert [line.split(":")[0] for line in found] == [
        "changes/10.bugfix.md", "changes/11.fixed.md", "changes/12.fixed.md", "changes/13.fixed.md",
    ]
    assert changelog.main(["check"], root=root) == 1


def test_check_refuses_a_direct_changelog_edit_outside_release_branches(tmp_path):
    root = make_repo(tmp_path)
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    (root / "CHANGELOG.md").write_text(
        BASE.replace("- Older fix (#2).", "- Older fix (#2).\n- Edited by hand (#3)."), encoding="utf-8"
    )
    git(root, "commit", "-q", "-am", "edit the changelog by hand")

    assert changelog.direct_edits(root, "HEAD~1") == ["CHANGELOG.md"]
    assert changelog.main(["check", "--diff-base", "HEAD~1", "--branch", "fix/thing"], root=root) == 1
    assert changelog.main(["check", "--diff-base", "HEAD~1", "--branch", "release/1.2.3"], root=root) == 0
    assert changelog.main(["check", "--diff-base", "HEAD~1", "--branch", "changelog/repair"], root=root) == 0


def test_check_passes_an_entry_file_instead_of_a_changelog_edit(tmp_path):
    root = make_repo(tmp_path)
    git(root, "init", "-q", "-b", "main")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    entry(root, "3.fixed.md", "- Added as an entry file (#3).\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "add an entry")

    assert changelog.main(["check", "--diff-base", "HEAD~1", "--branch", "fix/thing"], root=root) == 0


def test_the_repository_entries_are_valid():
    assert changelog.problems(changelog.ROOT) == []
