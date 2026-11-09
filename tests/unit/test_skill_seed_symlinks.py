"""Retiring old skill seeds must not traverse user-owned links."""

import errno
import hashlib
import stat
import sys
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


def test_retirement_does_not_traverse_linked_config_parent(tmp_path, seed_hashes):
    outside = tmp_path / "outside"
    (outside / "skills" / "tool").mkdir(parents=True)
    (outside / "skills" / "tool" / "SKILL.md").write_text("known skill")
    config = tmp_path / ".kollab"
    _link(config, outside)

    config_utils._retire_seeded_skills(config / "skills")

    assert config.is_symlink()
    assert (outside / "skills" / "tool" / "SKILL.md").read_text() == "known skill"
    assert not (outside / "skills" / ".seed-backup").exists()


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


def test_backup_directory_swap_does_not_move_a_skill_outside(tmp_path, monkeypatch, seed_hashes):
    skills = tmp_path / "skills"
    (skills / "tool").mkdir(parents=True)
    (skills / "tool" / "SKILL.md").write_bytes(b"known skill")
    backup = skills / ".seed-backup"
    backup.mkdir()
    moved_backup = skills / ".seed-backup-original"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "user-marker.txt").write_text("user")
    real_rename = config_utils._rename_seed_noreplace
    real_os_rename = config_utils.os.rename
    raced = False

    def swap_before_move(src, dst, *args, **kwargs):
        nonlocal raced
        if not raced and Path(src).name == "tool":
            raced = True
            try:
                real_os_rename(backup, moved_backup)
            except OSError:
                if sys.platform != "win32":
                    raise
                return real_rename(src, dst, *args, **kwargs)
            _link(backup, outside)
        return real_rename(src, dst, *args, **kwargs)

    monkeypatch.setattr(config_utils, "_rename_seed_noreplace", swap_before_move)
    try:
        config_utils._retire_seeded_skills(skills)
    except OSError:
        pass

    assert raced
    assert sorted(p.name for p in outside.iterdir()) == ["user-marker.txt"]
    assert (skills / "tool" / "SKILL.md").exists() or (
        (moved_backup if moved_backup.exists() else backup) / "tool" / "SKILL.md"
    ).read_bytes() == b"known skill"


def test_concurrent_backup_content_is_not_overwritten(tmp_path, monkeypatch, seed_hashes):
    skills = tmp_path / "skills"
    (skills / "tool").mkdir(parents=True)
    (skills / "tool" / "SKILL.md").write_bytes(b"known skill")
    backup = skills / ".seed-backup"
    backup.mkdir()
    real_rename = config_utils._rename_seed_noreplace
    raced = False

    def create_before_move(src, dst, *args, **kwargs):
        nonlocal raced
        if not raced:
            raced = True
            target = backup / Path(dst).name
            target.mkdir(exist_ok=True)
            (target / "user.txt").write_text("user")
        return real_rename(src, dst, *args, **kwargs)

    monkeypatch.setattr(config_utils, "_rename_seed_noreplace", create_before_move)
    config_utils._retire_seeded_skills(skills)

    assert raced
    assert (backup / "tool" / "user.txt").read_text() == "user"
    copies = [path for path in backup.glob("tool-*/SKILL.md")]
    assert len(copies) == 1 and copies[0].read_bytes() == b"known skill"


@pytest.mark.parametrize("recreate", [False, True])
def test_skill_changed_during_retirement_is_restored_or_preserved(
    tmp_path, monkeypatch, seed_hashes, caplog, recreate
):
    skills = tmp_path / "skills"
    skill_dir = skills / "tool"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_bytes(b"known skill")
    real_rename = config_utils._rename_seed_noreplace
    raced = False

    def change_during_move(src, dst, *args, **kwargs):
        nonlocal raced
        if not raced and Path(src).name == "tool":
            raced = True
            (skill_dir / "SKILL.md").write_bytes(b"user edit")
            result = real_rename(src, dst, *args, **kwargs)
            if recreate:
                skill_dir.mkdir()
                (skill_dir / "SKILL.md").write_bytes(b"new user copy")
            return result
        return real_rename(src, dst, *args, **kwargs)

    monkeypatch.setattr(config_utils, "_rename_seed_noreplace", change_during_move)
    config_utils._retire_seeded_skills(skills)

    assert raced
    backup = skills / ".seed-backup" / "tool" / "SKILL.md"
    if recreate:
        assert (skill_dir / "SKILL.md").read_bytes() == b"new user copy"
        assert backup.read_bytes() == b"user edit"
        assert "Skill retirement conflict" in caplog.text
        assert str(skill_dir) in caplog.text
        assert str(backup.parent) in caplog.text
    else:
        assert (skill_dir / "SKILL.md").read_bytes() == b"user edit"
        assert not backup.exists()
        assert "changed during retirement and was restored" in caplog.text


@pytest.mark.skipif(
    sys.platform not in ("darwin", "win32") and not sys.platform.startswith("linux"),
    reason="native exclusive directory rename unavailable",
)
def test_native_exclusive_rename_preserves_existing_empty_directory(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (source / "SKILL.md").write_bytes(b"source")

    with config_utils._seed_directory(tmp_path, create=False) as directory_fd:
        with pytest.raises(FileExistsError):
            if directory_fd is None:
                config_utils._rename_seed_noreplace(source, target)
            else:
                config_utils._rename_seed_noreplace(
                    source.name, target.name,
                    src_dir_fd=directory_fd, dst_dir_fd=directory_fd,
                )

    assert (source / "SKILL.md").read_bytes() == b"source"
    assert target.is_dir() and list(target.iterdir()) == []


def test_unavailable_exclusive_rename_keeps_skill_active(tmp_path, monkeypatch, seed_hashes, caplog):
    skills = tmp_path / "skills"
    (skills / "tool").mkdir(parents=True)
    (skills / "tool" / "SKILL.md").write_bytes(b"known skill")

    def unavailable(*args, **kwargs):
        raise OSError(errno.ENOTSUP, "exclusive rename unavailable")

    monkeypatch.setattr(config_utils, "_rename_seed_noreplace", unavailable)
    config_utils._retire_seeded_skills(skills)

    assert (skills / "tool" / "SKILL.md").read_bytes() == b"known skill"
    assert "Could not safely retire" in caplog.text


def test_unavailable_restore_preserves_changed_backup(tmp_path, monkeypatch, seed_hashes, caplog):
    skills = tmp_path / "skills"
    skill_dir = skills / "tool"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_bytes(b"known skill")
    real_rename = config_utils._rename_seed_noreplace
    moved = False

    def unavailable_restore(src, dst, *args, **kwargs):
        nonlocal moved
        if moved:
            raise OSError(errno.ENOTSUP, "exclusive rename unavailable")
        moved = True
        (skill_dir / "SKILL.md").write_bytes(b"user edit")
        return real_rename(src, dst, *args, **kwargs)

    monkeypatch.setattr(config_utils, "_rename_seed_noreplace", unavailable_restore)
    config_utils._retire_seeded_skills(skills)

    assert not skill_dir.exists()
    assert (skills / ".seed-backup" / "tool" / "SKILL.md").read_bytes() == b"user edit"
    assert "Skill retirement conflict" in caplog.text


@pytest.mark.parametrize("component", ["parent", "root", "backup"])
def test_retirement_does_not_traverse_reparse_directories(tmp_path, monkeypatch, seed_hashes, component):
    skills = tmp_path / "skills"
    (skills / "tool").mkdir(parents=True)
    (skills / "tool" / "SKILL.md").write_text("known skill")
    backup = skills / ".seed-backup"
    backup.mkdir()
    reparse = {"parent": skills.parent, "root": skills, "backup": backup}[component]
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
