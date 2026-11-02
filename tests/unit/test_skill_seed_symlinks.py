"""Retiring old skill seeds must not traverse user-owned links."""

import hashlib
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from kollabor_config import config_utils


def _link(path, target):
    try:
        path.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")


@pytest.fixture
def seed_hashes(monkeypatch):
    digest = hashlib.sha256(b"known skill").hexdigest()
    monkeypatch.setattr(config_utils, "_load_skill_seed_hashes", lambda: {"tool": {"SKILL.md": {digest}}})


def test_retirement_does_not_move_skills_through_linked_root(tmp_path, seed_hashes):
    outside = tmp_path / "outside"
    (outside / "tool").mkdir(parents=True)
    (outside / "tool" / "SKILL.md").write_text("known skill")
    skills = tmp_path / "skills"
    _link(skills, outside)

    config_utils._retire_seeded_skills(skills)

    assert skills.is_symlink()
    assert (outside / "tool" / "SKILL.md").read_text() == "known skill"
    assert not (outside / ".seed-backup").exists()


def test_retirement_does_not_move_skills_into_linked_backup(tmp_path, seed_hashes):
    skills = tmp_path / "skills"
    (skills / "tool").mkdir(parents=True)
    (skills / "tool" / "SKILL.md").write_text("known skill")
    outside = tmp_path / "outside"
    outside.mkdir()
    _link(skills / ".seed-backup", outside)

    config_utils._retire_seeded_skills(skills)

    assert (skills / ".seed-backup").is_symlink()
    assert (skills / "tool" / "SKILL.md").read_text() == "known skill"
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("component", ["root", "backup"])
def test_retirement_does_not_traverse_reparse_directories(tmp_path, monkeypatch, seed_hashes, component):
    skills = tmp_path / "skills"
    (skills / "tool").mkdir(parents=True)
    (skills / "tool" / "SKILL.md").write_text("known skill")
    backup = skills / ".seed-backup"
    backup.mkdir()
    reparse = skills if component == "root" else backup
    original_lstat = Path.lstat

    def fake_lstat(path, *args, **kwargs):
        if path == reparse:
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", fake_lstat)
    config_utils._retire_seeded_skills(skills)

    assert (skills / "tool" / "SKILL.md").read_text() == "known skill"
    assert list(backup.iterdir()) == []


def test_retirement_preserves_a_dangling_backup_entry(tmp_path, seed_hashes):
    skills = tmp_path / "skills"
    (skills / "tool").mkdir(parents=True)
    (skills / "tool" / "SKILL.md").write_text("known skill")
    backup = skills / ".seed-backup"
    backup.mkdir()
    outside = tmp_path / "missing"
    _link(backup / "tool", outside)

    config_utils._retire_seeded_skills(skills)

    assert (backup / "tool").is_symlink()
    assert not outside.exists()
    saved = [p for p in backup.iterdir() if not p.is_symlink()]
    assert len(saved) == 1
    assert (saved[0] / "SKILL.md").read_text() == "known skill"
