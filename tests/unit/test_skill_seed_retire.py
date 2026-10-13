"""Unmodified seed copies of bundled skills move aside; nothing is deleted."""

import hashlib
import json
from types import SimpleNamespace

import pytest

import kollabor_config.config_utils as config_utils

TDD_V1 = "---\nname: tdd\ndescription: seed v1\n---\nold body\n"
TDD_V2 = "---\nname: tdd\ndescription: seed v2\n---\nnew body\n"
DEBUG_V1 = "---\nname: debugging\ndescription: seed\n---\nbody\n"
RETIRED = "---\nname: old-tool\ndescription: gone\n---\nbody\n"
RUN_V1 = "echo v1\n"
RUN_V2 = "echo v2\n"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _global(layout, name, text):
    _write(layout.skills / name / "SKILL.md", text)


@pytest.fixture
def layout(tmp_path, monkeypatch):
    """Fake install (bundled skills + hash list), HOME under tmp_path."""
    site = tmp_path / "site-packages"
    (site / "kollabor_config").mkdir(parents=True)
    bundled = site / "bundles" / "skills"
    data = site / "bundles" / "data"
    _write(bundled / "tdd" / "SKILL.md", TDD_V2)
    _write(bundled / "debugging" / "SKILL.md", DEBUG_V1)
    _write(
        data / "skill-seed-hashes.json",
        json.dumps(
            {
                "tdd": {
                    "SKILL.md": [_sha(TDD_V1), _sha(TDD_V2)],
                    "scripts/run.sh": [_sha(RUN_V1), _sha(RUN_V2)],
                },
                "debugging": {"SKILL.md": [_sha(DEBUG_V1)]},
                "old-tool": {"SKILL.md": [_sha(RETIRED)]},  # no longer bundled
            }
        ),
    )

    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(
        config_utils,
        "__file__",
        str(site / "kollabor_config" / "config_utils.py"),
    )
    return SimpleNamespace(skills=home / ".kollab" / "skills", data=data)


@pytest.mark.parametrize("seed_text", [TDD_V1, TDD_V2], ids=["old", "current"])
def test_unmodified_seed_moves_to_seed_backup(layout, seed_text):
    _global(layout, "tdd", seed_text)

    config_utils._retire_seeded_skills(layout.skills)

    assert not (layout.skills / "tdd").exists()
    backup = layout.skills / ".seed-backup" / "tdd" / "SKILL.md"
    assert backup.read_text(encoding="utf-8") == seed_text


def test_edited_seed_stays(layout):
    edited = DEBUG_V1 + "my tweak\n"
    _global(layout, "debugging", edited)

    config_utils._retire_seeded_skills(layout.skills)

    kept = layout.skills / "debugging" / "SKILL.md"
    assert kept.read_text(encoding="utf-8") == edited
    assert not (layout.skills / ".seed-backup").exists()


def test_custom_skill_stays(layout):
    _global(layout, "my-custom", "custom body\n")

    config_utils._retire_seeded_skills(layout.skills)

    assert (layout.skills / "my-custom" / "SKILL.md").exists()


def test_seed_of_a_skill_no_longer_bundled_moves(layout):
    _global(layout, "old-tool", RETIRED)

    config_utils._retire_seeded_skills(layout.skills)

    assert not (layout.skills / "old-tool").exists()
    assert (layout.skills / ".seed-backup" / "old-tool" / "SKILL.md").exists()


def test_mixed_versions_and_caches_still_count_as_a_seed(layout):
    _global(layout, "tdd", TDD_V1)
    _write(layout.skills / "tdd" / "scripts" / "run.sh", RUN_V2)  # copied by a later release
    _write(layout.skills / "tdd" / ".DS_Store", "finder")
    _write(layout.skills / "tdd" / "scripts" / "__pycache__" / "x.pyc", "bytecode")

    config_utils._retire_seeded_skills(layout.skills)

    assert not (layout.skills / "tdd").exists()
    assert (layout.skills / ".seed-backup" / "tdd" / "scripts" / "run.sh").exists()


@pytest.mark.parametrize(
    "path, text",
    [("scripts/run.sh", RUN_V1 + "my tweak\n"), ("notes.md", "my notes\n")],
    ids=["edited-script", "added-file"],
)
def test_a_change_outside_skill_md_keeps_the_folder(layout, path, text):
    _global(layout, "tdd", TDD_V1)
    _write(layout.skills / "tdd" / path, text)

    config_utils._retire_seeded_skills(layout.skills)

    assert (layout.skills / "tdd" / path).read_text(encoding="utf-8") == text
    assert not (layout.skills / ".seed-backup").exists()


def test_a_link_inside_keeps_the_folder(layout, tmp_path):
    _global(layout, "tdd", TDD_V1)
    _write(tmp_path / "elsewhere.sh", RUN_V1)
    (layout.skills / "tdd" / "scripts").mkdir()
    (layout.skills / "tdd" / "scripts" / "run.sh").symlink_to(tmp_path / "elsewhere.sh")

    config_utils._retire_seeded_skills(layout.skills)

    assert (layout.skills / "tdd" / "SKILL.md").exists()


def test_hash_list_in_the_wrong_shape_moves_nothing(layout):
    (layout.data / "skill-seed-hashes.json").write_text(json.dumps({"tdd": [_sha(TDD_V1)]}), encoding="utf-8")
    _global(layout, "tdd", TDD_V1)

    config_utils._retire_seeded_skills(layout.skills)

    assert (layout.skills / "tdd" / "SKILL.md").exists()


def test_second_run_is_a_noop(layout):
    _global(layout, "tdd", TDD_V1)
    _global(layout, "debugging", DEBUG_V1 + "tweak\n")
    config_utils._retire_seeded_skills(layout.skills)

    def snapshot():
        return sorted(p.relative_to(layout.skills).as_posix() for p in layout.skills.rglob("*"))

    before = snapshot()
    config_utils._retire_seeded_skills(layout.skills)

    assert snapshot() == before


def test_taken_backup_name_gets_timestamp_suffix(layout):
    _global(layout, "tdd", TDD_V1)
    (layout.skills / ".seed-backup" / "tdd").mkdir(parents=True)  # earlier run

    config_utils._retire_seeded_skills(layout.skills)

    backups = sorted(p.name for p in (layout.skills / ".seed-backup").iterdir())
    assert backups[0] == "tdd"
    assert backups[1].startswith("tdd-")
    moved = layout.skills / ".seed-backup" / backups[1] / "SKILL.md"
    assert moved.read_text(encoding="utf-8") == TDD_V1


def test_missing_hash_list_moves_nothing(layout):
    (layout.data / "skill-seed-hashes.json").unlink()
    _global(layout, "tdd", TDD_V1)

    config_utils._retire_seeded_skills(layout.skills)

    assert (layout.skills / "tdd" / "SKILL.md").exists()


def test_startup_retires_seed_copies_and_copies_nothing(layout):
    _global(layout, "tdd", TDD_V1)

    config_utils.initialize_system_prompt()

    assert (layout.skills / ".seed-backup" / "tdd" / "SKILL.md").exists()
    assert not (layout.skills / "debugging").exists()
