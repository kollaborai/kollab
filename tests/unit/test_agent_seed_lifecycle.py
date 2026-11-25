import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from kollabor_agent.agent_manager import AgentManager
from kollabor_config import config_utils


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode("utf-8"))


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


@pytest.fixture
def agent_seed_layout(tmp_path, monkeypatch):
    bundle_root = tmp_path / "site-packages" / "bundles"
    bundled_agent = bundle_root / "agents" / "tech-dude"
    data_dir = bundle_root / "data"
    config_dir = tmp_path / "home" / ".kollab"
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    bundled_files = {
        "system_prompt.md": "Prompt v2\n",
        "agent.json": '{"description":"Bundled v2"}\n',
        "sections/00-header.md": "Header v2\n",
        "sections/custom.md": "Bundled custom v2\n",
        "sections/stable.md": "Stable v2\n",
        "sections/added.md": "Added file\n",
    }
    known_versions = {
        "system_prompt.md": ["Prompt v1\n"],
        "agent.json": ['{"description":"Bundled v1"}\n'],
        "sections/00-header.md": ["Header v1\n"],
        "sections/custom.md": ["Bundled custom v1\n"],
        "sections/stable.md": [],
        "sections/added.md": [],
        "sections/removed.md": ["Removed seed\n"],
    }
    for relative, content in bundled_files.items():
        _write(bundled_agent / relative, content)
    seed_hashes = {
        "tech-dude": {
            relative: (
                [
                    _digest(version)
                    for version in [*history, bundled_files.get(relative, "")]
                ]
                if relative in bundled_files
                else [_digest(version) for version in history]
            )
            for relative, history in known_versions.items()
        }
    }
    _write(data_dir / "agent-seed-hashes.json", json.dumps(seed_hashes))
    _write(data_dir / "skill-seed-hashes.json", "{}")

    global_agent = config_dir / "agents" / "tech-dude"
    global_files = {
        "system_prompt.md": "Prompt v1\n",
        "agent.json": '{"description":"Bundled v1"}\n',
        "sections/00-header.md": "Header v1\n",
        "sections/custom.md": "Hand-edited custom\n",
        "sections/stable.md": "Stable v2\n",
        "sections/removed.md": "Removed seed\n",
    }
    for relative, content in global_files.items():
        _write(global_agent / relative, content)

    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.chdir(workspace)
    monkeypatch.setattr(config_utils, "get_config_directory", lambda: config_dir)
    monkeypatch.setattr(
        config_utils,
        "_get_bundled_dir",
        lambda name: bundle_root / name,
    )
    monkeypatch.setattr(config_utils, "get_bundled_skills_dir", lambda: None)

    return SimpleNamespace(
        bundle_root=bundle_root,
        config_dir=config_dir,
        global_agent=global_agent,
        workspace=workspace,
    )


def test_startup_updates_seeded_files_and_retires_removed_files(
    agent_seed_layout, caplog
):
    layout = agent_seed_layout

    config_utils.initialize_system_prompt()

    assert (layout.global_agent / "system_prompt.md").read_text(
        encoding="utf-8"
    ) == "Prompt v2\n"
    assert (layout.global_agent / "agent.json").read_text(
        encoding="utf-8"
    ) == '{"description":"Bundled v2"}\n'
    assert (layout.global_agent / "sections/00-header.md").read_text(
        encoding="utf-8"
    ) == "Header v2\n"
    assert (layout.global_agent / "sections/stable.md").read_text(
        encoding="utf-8"
    ) == "Stable v2\n"
    assert (layout.global_agent / "sections/added.md").read_text(
        encoding="utf-8"
    ) == "Added file\n"

    custom_file = layout.global_agent / "sections/custom.md"
    assert custom_file.read_text(encoding="utf-8") == "Hand-edited custom\n"
    assert any(str(custom_file) in record.getMessage() for record in caplog.records)
    assert not (layout.global_agent / "sections/removed.md").exists()

    backup = layout.config_dir / "agents" / ".seed-backup" / "tech-dude"
    expected_backups = {
        "system_prompt.md": "Prompt v1\n",
        "agent.json": '{"description":"Bundled v1"}\n',
        "sections/00-header.md": "Header v1\n",
        "sections/removed.md": "Removed seed\n",
    }
    for relative, content in expected_backups.items():
        assert (backup / relative).read_text(encoding="utf-8") == content

    manager = AgentManager(project_path=layout.workspace)
    assert manager.find_agent_dir("tech-dude") == (layout.global_agent, "global")
    assert manager.get_agent("tech-dude").directory == layout.global_agent

    backed_up = {
        path.relative_to(backup).as_posix(): path.read_text(encoding="utf-8")
        for path in backup.rglob("*")
        if path.is_file()
    }
    config_utils.initialize_system_prompt()
    assert {
        path.relative_to(backup).as_posix(): path.read_text(encoding="utf-8")
        for path in backup.rglob("*")
        if path.is_file()
    } == backed_up


def test_existing_backup_is_preserved_and_updated_seed_gets_unique_name(
    agent_seed_layout,
):
    layout = agent_seed_layout
    backup = layout.config_dir / "agents" / ".seed-backup" / "tech-dude"
    _write(backup / "system_prompt.md", "Earlier backup\n")

    config_utils.initialize_system_prompt()

    assert (backup / "system_prompt.md").read_text(
        encoding="utf-8"
    ) == "Earlier backup\n"
    siblings = list(backup.glob("system_prompt.md-*"))
    assert len(siblings) == 1
    assert siblings[0].read_text(encoding="utf-8") == "Prompt v1\n"
    assert (layout.global_agent / "system_prompt.md").read_text(
        encoding="utf-8"
    ) == "Prompt v2\n"


def test_removed_agent_retires_known_files_but_keeps_user_files(agent_seed_layout):
    layout = agent_seed_layout
    bundled_agent = layout.bundle_root / "agents" / "tech-dude"
    bundled_agent.rename(layout.bundle_root / "agents" / "removed-agent")
    user_file = layout.global_agent / "notes.md"
    _write(user_file, "User note\n")

    config_utils.initialize_system_prompt()

    backup = layout.config_dir / "agents" / ".seed-backup" / "tech-dude"
    assert not (layout.global_agent / "system_prompt.md").exists()
    assert (backup / "system_prompt.md").read_text(encoding="utf-8") == "Prompt v1\n"
    assert user_file.read_text(encoding="utf-8") == "User note\n"


def test_unreadable_seed_hashes_preserve_existing_files(agent_seed_layout, caplog):
    layout = agent_seed_layout
    hash_file = layout.bundle_root / "data" / "agent-seed-hashes.json"
    _write(hash_file, '{"tech-dude":{"system_prompt.md":"not-a-hash-list"}}')

    config_utils.initialize_system_prompt()

    assert (layout.global_agent / "system_prompt.md").read_text(
        encoding="utf-8"
    ) == "Prompt v1\n"
    assert (layout.global_agent / "sections/added.md").read_text(
        encoding="utf-8"
    ) == "Added file\n"
    assert not (layout.config_dir / "agents" / ".seed-backup").exists()
    assert any(
        "Agent seed hashes unreadable" in record.getMessage()
        for record in caplog.records
    )


def test_unsafe_seed_hash_path_preserves_existing_files(agent_seed_layout, caplog):
    layout = agent_seed_layout
    hash_file = layout.bundle_root / "data" / "agent-seed-hashes.json"
    _write(
        hash_file,
        json.dumps({"tech-dude": {"C:/outside.md": [_digest("Prompt v1\n")]}}),
    )

    config_utils.initialize_system_prompt()

    assert (layout.global_agent / "system_prompt.md").read_text(
        encoding="utf-8"
    ) == "Prompt v1\n"
    assert not (layout.config_dir / "agents" / ".seed-backup").exists()
    assert any(
        "Agent seed hashes unreadable" in record.getMessage()
        for record in caplog.records
    )


def test_failed_update_restores_the_original_seed_file(agent_seed_layout, monkeypatch):
    layout = agent_seed_layout
    original_copy = config_utils._copy_file_if_missing

    def fail_updated_prompt(source, target, *, directory_fd=None):
        if source.read_text(encoding="utf-8") == "Prompt v2\n":
            raise OSError("simulated publish failure")
        return original_copy(source, target, directory_fd=directory_fd)

    monkeypatch.setattr(config_utils, "_copy_file_if_missing", fail_updated_prompt)

    config_utils.initialize_system_prompt()

    assert (layout.global_agent / "system_prompt.md").read_text(
        encoding="utf-8"
    ) == "Prompt v1\n"
    assert not (
        layout.config_dir / "agents" / ".seed-backup" / "tech-dude" / "system_prompt.md"
    ).exists()


def test_linked_global_seed_is_preserved(agent_seed_layout, tmp_path):
    layout = agent_seed_layout
    prompt = layout.global_agent / "system_prompt.md"
    prompt.unlink()
    outside = tmp_path / "outside-prompt.md"
    _write(outside, "Linked user prompt\n")
    try:
        prompt.symlink_to(outside)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    config_utils.initialize_system_prompt()

    assert prompt.is_symlink()
    assert outside.read_text(encoding="utf-8") == "Linked user prompt\n"
    assert not (
        layout.config_dir / "agents" / ".seed-backup" / "tech-dude" / "system_prompt.md"
    ).exists()


def test_linked_bundled_agent_is_not_treated_as_removed(agent_seed_layout, tmp_path):
    layout = agent_seed_layout
    bundled_agent = layout.bundle_root / "agents" / "tech-dude"
    target = tmp_path / "linked-bundle-agent"
    bundled_agent.rename(target)
    try:
        bundled_agent.symlink_to(target, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")

    config_utils.initialize_system_prompt()

    assert (layout.global_agent / "system_prompt.md").read_text(
        encoding="utf-8"
    ) == "Prompt v1\n"
    assert not (layout.config_dir / "agents" / ".seed-backup").exists()
