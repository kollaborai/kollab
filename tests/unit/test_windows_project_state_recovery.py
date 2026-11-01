"""Non-destructive selection of state created before Windows path encoding changed."""

import os
import subprocess
import sys
import threading
import time
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
    monkeypatch.setattr(paths, "_PROJECT_OWNER_WAIT_SECONDS", 0.05)
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


def test_concurrent_config_startups_wait_for_the_same_claim(locations, monkeypatch):
    from kollabor_config.service import ConfigService

    project, root, _, new = locations
    created = threading.Event()
    finish = threading.Event()
    observed = threading.Event()
    publish = paths._write_project_owner
    read = paths._read_project_owner

    monkeypatch.chdir(project)
    monkeypatch.setattr(paths, "IS_WINDOWS", True)
    monkeypatch.setattr(paths, "get_config_directory_candidates", lambda: [root])
    monkeypatch.setattr(paths, "_PROJECT_OWNER_WAIT_SECONDS", 2.0)
    config = project / "config.json"
    config.write_text("{}")

    def delayed_publish(directory, owner):
        created.set()
        assert finish.wait(5)
        publish(directory, owner)

    def observed_read(directory):
        value = read(directory)
        if value is None:
            observed.set()
        return value

    monkeypatch.setattr(paths, "_write_project_owner", delayed_publish)
    monkeypatch.setattr(paths, "_read_project_owner", observed_read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(ConfigService, config, None, True)
        assert created.wait(5)
        try:
            second = pool.submit(ConfigService, config, None, True)
            assert observed.wait(5)
            assert not second.done()
        finally:
            finish.set()
        assert isinstance(first.result(timeout=10), ConfigService)
        assert isinstance(second.result(timeout=10), ConfigService)
    assert paths._windows_project_state_dir(project, root) == new.resolve()


def test_concurrent_config_startups_in_separate_processes(tmp_path):
    project, home = tmp_path / "workspace", tmp_path / "home"
    project.mkdir()
    home.mkdir(mode=0o700)
    (project / "config.json").write_text("{}")
    created, observed, release = (tmp_path / name for name in ("created", "observed", "release"))
    script = r'''
import os, sys, time
from pathlib import Path
from kollabor_config import config_utils as paths
from kollabor_config.service import ConfigService
project, home, created, observed, release, role = sys.argv[1:]
project, home, created, observed, release = map(Path, (project, home, created, observed, release))
os.chdir(project)
paths.IS_WINDOWS = True
paths.get_config_directory_candidates = lambda: [home / ".kollab"]
paths.encode_project_path = lambda _: "new"
paths._legacy_encode_project_path = lambda _: "old"
if role == "first":
    publish = paths._write_project_owner
    def delayed(directory, owner):
        created.write_text("ready")
        deadline = time.monotonic() + 30
        while not release.exists():
            if time.monotonic() >= deadline:
                raise RuntimeError("release was not signalled")
            time.sleep(0.01)
        publish(directory, owner)
    paths._write_project_owner = delayed
else:
    read = paths._read_project_owner
    def observed_read(directory):
        value = read(directory)
        if value is None:
            observed.write_text("waiting")
        return value
    paths._read_project_owner = observed_read
ConfigService(project / "config.json", None, True)
print("started")
'''
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "KOLLAB_NO_KEYRING": "1"}
    for name in ("KOLLAB_PROJECT_DATA_PROJECT", "KOLLAB_PROJECT_DATA_DIR"):
        env.pop(name, None)
    args = [sys.executable, "-c", script, str(project), str(home), str(created), str(observed), str(release)]
    first = subprocess.Popen([*args, "first"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    second = None
    try:
        deadline = time.monotonic() + 15
        while not created.exists() and first.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert created.exists(), first.communicate(timeout=2) if first.poll() is not None else "first did not start"
        second = subprocess.Popen([*args, "second"], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 15
        while not observed.exists() and second.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert observed.exists(), second.communicate(timeout=2) if second.poll() is not None else "second did not wait"
        release.write_text("continue")
        first_out, first_err = first.communicate(timeout=15)
        second_out, second_err = second.communicate(timeout=15)
        assert first.returncode == second.returncode == 0, (first_err, second_err)
        assert "started" in first_out and "started" in second_out
        assert (home / ".kollab" / "projects" / "new" / paths._PROJECT_OWNER_FILE).exists()
    finally:
        for process in (first, second):
            if process is not None and process.poll() is None:
                process.terminate()
                process.wait(timeout=5)


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


def test_competing_project_cannot_take_a_claim_in_progress(locations, monkeypatch):
    project, root, _, new = locations
    other = project.parent / "another"
    other.mkdir()
    monkeypatch.setattr(paths, "_PROJECT_OWNER_WAIT_SECONDS", 2.0)
    created, release = threading.Event(), threading.Event()
    publish = paths._write_project_owner

    def delayed(directory, owner):
        created.set()
        assert release.wait(5)
        publish(directory, owner)

    monkeypatch.setattr(paths, "_write_project_owner", delayed)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(paths._windows_project_state_dir, project, root)
        assert created.wait(5)
        second = pool.submit(paths._windows_project_state_dir, other, root)
        release.set()
        assert first.result(timeout=10) == new.resolve()
        with pytest.raises(paths.ProjectStateLocationError, match="belongs to another project"):
            second.result(timeout=10)


def test_legacy_state_appearing_during_publication_stops_both_starts(locations, monkeypatch):
    project, root, old, new = locations
    monkeypatch.setattr(paths, "_PROJECT_OWNER_WAIT_SECONDS", 2.0)
    created, release, observed = threading.Event(), threading.Event(), threading.Event()
    publish, read = paths._write_project_owner, paths._read_project_owner

    def delayed(directory, owner):
        created.set()
        assert release.wait(5)
        publish(directory, owner)

    def observed_read(directory):
        value = read(directory)
        if value is None:
            observed.set()
        return value

    monkeypatch.setattr(paths, "_write_project_owner", delayed)
    monkeypatch.setattr(paths, "_read_project_owner", observed_read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(paths._windows_project_state_dir, project, root)
        assert created.wait(5)
        second = pool.submit(paths._windows_project_state_dir, project, root)
        assert observed.wait(5)
        old.mkdir()
        (old / "saved").write_text("old")
        release.set()
        for result in (first, second):
            with pytest.raises(paths.ProjectStateLocationError, match="legacy project state|Multiple project state"):
                result.result(timeout=10)
    assert (old / "saved").read_text() == "old"
    assert (new / paths._PROJECT_OWNER_FILE).exists()


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
