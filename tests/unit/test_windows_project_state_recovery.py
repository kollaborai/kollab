"""Non-destructive selection of state created before Windows path encoding changed."""

import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from kollabor_config import config_utils as paths


@pytest.fixture
def locations(tmp_path, monkeypatch):
    project = tmp_path / "workspace"
    project.mkdir()
    root = tmp_path / "config"
    monkeypatch.setattr(paths, "encode_project_path", lambda _: "new")
    monkeypatch.setattr(paths, "_legacy_encode_project_path", lambda _: "old")
    return project, root, root / "projects" / "old", root / "projects" / "new"


def test_old_only_requires_selection_and_never_moves_files(locations, monkeypatch):
    project, root, old, new = locations
    old.mkdir(parents=True)
    saved = old / "config.json"
    saved.write_text("old data")
    with pytest.raises(paths.ProjectStateLocationError, match="Cannot establish ownership"):
        paths._windows_project_state_dir(project, root)
    monkeypatch.setenv("KOLLAB_PROJECT_DATA_PROJECT", str(project))
    monkeypatch.setenv("KOLLAB_PROJECT_DATA_DIR", str(old))
    for _ in range(3):
        assert paths._windows_project_state_dir(project, root) == old.resolve()
    assert saved.read_text() == "old data"
    assert not new.exists()


def test_new_only_and_fresh_locations(locations):
    project, root, old, new = locations
    assert paths._windows_project_state_dir(project, root, create_new=False) == new
    assert not new.exists()
    assert paths._windows_project_state_dir(project, root) == new.resolve()
    assert (new / paths._PROJECT_OWNER_FILE).is_file()
    assert paths._windows_project_state_dir(project, root) == new.resolve()
    assert not old.exists()


def test_existing_unmarked_new_location_is_not_assumed_to_belong_to_project(locations):
    project, root, _, new = locations
    new.mkdir(parents=True)
    (new / "config.json").write_text("other state")
    with pytest.raises(paths.ProjectStateLocationError, match="Cannot establish ownership"):
        paths._windows_project_state_dir(project, root)
    assert (new / "config.json").read_text() == "other state"


def test_both_are_preserved_and_selection_is_project_bound(locations, monkeypatch):
    project, root, old, new = locations
    for folder in (old, new):
        folder.mkdir(parents=True)
        (folder / "config.json").write_text(folder.name)
    with pytest.raises(paths.ProjectStateLocationError, match="Multiple project state"):
        paths._windows_project_state_dir(project, root)
    monkeypatch.setenv("KOLLAB_PROJECT_DATA_PROJECT", str(project))
    monkeypatch.setenv("KOLLAB_PROJECT_DATA_DIR", str(old))
    assert paths._windows_project_state_dir(project, root) == old.resolve()
    assert [(d / "config.json").read_text() for d in (old, new)] == ["old", "new"]
    other = project.parent / "other"
    other.mkdir()
    with pytest.raises(paths.ProjectStateLocationError, match="Multiple project state"):
        paths._windows_project_state_dir(other, root)


def test_real_underscore_collision_cannot_share_claimed_state(tmp_path, monkeypatch):
    first, second = tmp_path / "a_b", tmp_path / "a" / "b"
    first.mkdir()
    second.mkdir(parents=True)
    monkeypatch.setattr(paths, "_legacy_encode_project_path", lambda _: "legacy")
    root = tmp_path / "config"
    assert paths.encode_project_path(first) == paths.encode_project_path(second)
    selected = paths._windows_project_state_dir(first, root)
    with pytest.raises(paths.ProjectStateLocationError, match="belongs to another project"):
        paths._windows_project_state_dir(second, root)
    assert paths._read_project_owner(selected) == paths._project_owner(first, None)
    monkeypatch.setenv("KOLLAB_PROJECT_DATA_PROJECT", str(second))
    monkeypatch.setenv("KOLLAB_PROJECT_DATA_DIR", str(selected))
    with pytest.raises(paths.ProjectStateLocationError, match="belongs to another project"):
        paths._windows_project_state_dir(second, root)


def test_unsafe_state_location_is_not_followed(locations):
    project, root, old, new = locations
    old.parent.mkdir(parents=True)
    old.write_text("not a project state directory")
    with pytest.raises(paths.ProjectStateLocationError, match="not a regular directory"):
        paths._windows_project_state_dir(project, root)
    assert old.read_text() == "not a project state directory"
    assert not new.exists()


def test_incomplete_selection_never_silently_falls_back(locations, monkeypatch):
    project, root, _, _ = locations
    monkeypatch.setenv("KOLLAB_PROJECT_DATA_DIR", str(Path.cwd()))
    with pytest.raises(paths.ProjectStateLocationError, match="Incomplete"):
        paths._windows_project_state_dir(project, root)


def test_unreadable_claim_and_competing_projects_fail_closed(locations):
    project, root, _, new = locations
    chosen = paths._windows_project_state_dir(project, root)
    (chosen / paths._PROJECT_OWNER_FILE).write_text("not json")
    with pytest.raises(paths.ProjectStateLocationError, match="Invalid project ownership marker"):
        paths._windows_project_state_dir(project, root)
    assert (chosen / paths._PROJECT_OWNER_FILE).read_text() == "not json"


def test_marker_read_error_is_not_treated_as_absence(locations, monkeypatch):
    from kollabor_config import private_fs

    project, root, _, new = locations
    paths._windows_project_state_dir(project, root)

    def denied(*_args, **_kwargs):
        raise PermissionError("simulated inaccessible marker")

    monkeypatch.setattr(private_fs, "open_nofollow", denied)
    with pytest.raises(paths.ProjectStateLocationError, match="Cannot read project ownership marker"):
        paths._windows_project_state_dir(project, root)
    assert (new / paths._PROJECT_OWNER_FILE).is_file()


def test_competing_claims_never_share_the_same_state(locations):
    project, root, _, new = locations
    other = project.parent / "another"
    other.mkdir()

    def claim(path):
        try:
            return paths._windows_project_state_dir(path, root)
        except paths.ProjectStateLocationError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, (project, other)))
    assert results.count(new.resolve()) == 1
    owner = paths._read_project_owner(new)
    winner = project if results[0] else other
    assert owner == paths._project_owner(winner, None)


def test_incomplete_concurrent_claim_is_not_adopted(locations, monkeypatch):
    project, root, _, new = locations
    created = threading.Event()
    finish = threading.Event()
    publish = paths._write_project_owner

    def delayed_publish(directory, owner):
        created.set()
        assert finish.wait(5)
        publish(directory, owner)

    monkeypatch.setattr(paths, "_write_project_owner", delayed_publish)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(paths._windows_project_state_dir, project, root)
        assert created.wait(5)
        try:
            with pytest.raises(paths.ProjectStateLocationError, match="Cannot establish ownership"):
                paths._windows_project_state_dir(project, root)
        finally:
            finish.set()
        assert first.result() == new.resolve()
    assert paths._windows_project_state_dir(project, root) == new.resolve()


def test_interrupted_claim_cannot_be_adopted(locations, monkeypatch):
    project, root, _, new = locations

    def interrupted(*_args):
        raise OSError("simulated interruption")

    monkeypatch.setattr(paths.os, "link", interrupted)
    with pytest.raises(paths.ProjectStateLocationError, match="Could not claim"):
        paths._windows_project_state_dir(project, root)
    assert new.is_dir() and list(new.iterdir()) == []
    with pytest.raises(paths.ProjectStateLocationError, match="Cannot establish ownership"):
        paths._windows_project_state_dir(project, root)


def test_vault_selection_is_bound_to_identity(locations, monkeypatch):
    project, root, old, new = locations
    old.mkdir(parents=True)
    new.mkdir()
    monkeypatch.setenv("KOLLAB_VAULT_DATA_PROJECT", str(project))
    monkeypatch.setenv("KOLLAB_VAULT_DATA_IDENTITY", "one")
    monkeypatch.setenv("KOLLAB_VAULT_DATA_DIR", str(old))
    assert paths._windows_project_state_dir(
        project, root, selection_prefix="KOLLAB_VAULT_DATA", identity="one"
    ) == old.resolve()
    with pytest.raises(paths.ProjectStateLocationError, match="Multiple project state"):
        paths._windows_project_state_dir(project, root, selection_prefix="KOLLAB_VAULT_DATA", identity="two")


def test_conflicts_are_not_swallowed_by_config_or_permissions(tmp_path, monkeypatch):
    from kollabor_agent.permissions.manager import PermissionManager
    from kollabor_config.loader import ConfigLoader
    from kollabor_config.service import ConfigService

    def conflict(*_args, **_kwargs):
        raise paths.ProjectStateLocationError("ambiguous state")

    monkeypatch.setattr(ConfigLoader, "load_complete_config", conflict)
    with pytest.raises(paths.ProjectStateLocationError, match="ambiguous state"):
        ConfigService(tmp_path / "config.json", fast_mode=True)
    monkeypatch.setattr(PermissionManager, "_get_project_data_dir", conflict)
    manager = PermissionManager.__new__(PermissionManager)
    with pytest.raises(paths.ProjectStateLocationError, match="ambiguous state"):
        manager._load_project_approvals()
    with pytest.raises(paths.ProjectStateLocationError, match="ambiguous state"):
        manager._save_project_approvals()


def test_old_nested_vault_is_read_in_place_and_conflicts_stop(tmp_path, monkeypatch):
    from plugins.hub import vault

    vaults, shared = tmp_path / "vaults", tmp_path / "shared"
    identity = vaults / "agent"
    nested = identity / "projects" / "old"
    nested.mkdir(parents=True)
    stream = nested / "stream.jsonl"
    stream.write_text('{"content":"previous","identity":"agent"}\n')
    monkeypatch.setattr(vault, "os", SimpleNamespace(name="nt", environ=os.environ))
    monkeypatch.setattr(vault, "get_vaults_dir", lambda: vaults)
    monkeypatch.setattr(vault, "get_global_vaults_dir", lambda: shared)
    monkeypatch.setattr(vault, "_nested_project_dir", lambda _: nested)
    assert vault.find_active_stream(identity) == stream
    assert vault.AgentVault("agent").get_recent_stream()[0]["content"] == "previous"
    assert stream.exists() and not (identity / "stream.jsonl").exists()
    (identity / "stream.jsonl").write_text('{"content":"new"}\n')
    with pytest.raises(paths.ProjectStateLocationError, match="Both flat and nested"):
        vault.AgentVault("agent")
    with pytest.raises(paths.ProjectStateLocationError, match="Both flat and nested"):
        vault.find_active_stream(identity)
    monkeypatch.setattr(vault, "_vault_selection_applies", lambda _: True)
    assert vault.find_active_stream(identity) == stream
    assert vault.AgentVault("agent").get_recent_stream()[0]["content"] == "previous"
    assert stream.exists() and (identity / "stream.jsonl").exists()


def test_old_crystals_stay_available_to_crystal_store_without_removing_source(tmp_path, monkeypatch):
    from plugins.hub import vault
    from plugins.hub.crystal_store import CrystalStore

    class WindowsOS:
        name = "nt"

        def __getattr__(self, name):
            return getattr(os, name)

    vaults, shared = tmp_path / "vaults", tmp_path / "shared"
    identity = vaults / "agent"
    identity.mkdir(parents=True)
    CrystalStore(identity).add_entry("**Retained memory** Important saved context")
    source = identity / "crystallized.md"
    original = source.read_bytes()
    (identity / "meta.json").write_text('{"custom":"saved"}')
    monkeypatch.setattr(vault, "os", WindowsOS())
    monkeypatch.setattr(vault, "get_vaults_dir", lambda: vaults)
    monkeypatch.setattr(vault, "get_global_vaults_dir", lambda: shared)
    monkeypatch.setattr(vault, "_nested_project_dir", lambda _: identity / "projects" / "none")
    restored = vault.AgentVault("agent")
    assert source.read_bytes() == original
    assert CrystalStore(restored.global_vault_dir).load()[0].summary == "Retained memory"
    assert restored.get_meta()["custom"] == "saved"
    assert (identity / "meta.json").exists()
