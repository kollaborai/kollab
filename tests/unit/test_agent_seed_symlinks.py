"""Startup must treat user-owned agent links as existing entries."""

import errno
import stat
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from kollabor_config import config_utils


def _link(path: Path, target: Path, *, directory: bool = False) -> None:
    try:
        path.symlink_to(target, target_is_directory=directory)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")


def test_seed_preserves_section_and_dangling_file_links(tmp_path):
    source = tmp_path / "bundled"
    target = tmp_path / "agents" / "operator"
    outside = tmp_path / "outside"
    (source / "sections").mkdir(parents=True)
    (source / "sections" / "header.md").write_text("bundle header")
    (source / "system_prompt.md").write_text("bundle prompt")
    (source / "agent.json").write_text("{}")
    target.mkdir(parents=True)
    outside.mkdir()
    _link(target / "sections", outside, directory=True)
    _link(target / "system_prompt.md", outside / "missing.md")

    copied, preserved = config_utils._copy_missing_tree_contents(source, target)

    assert (copied, preserved) == (1, 2)
    assert (target / "sections").is_symlink()
    assert (target / "system_prompt.md").is_symlink()
    assert list(outside.iterdir()) == []
    assert (target / "agent.json").read_text() == "{}"


def test_seed_preserves_a_dangling_directory_link(tmp_path):
    source = tmp_path / "bundled"
    target = tmp_path / "global"
    (source / "sections").mkdir(parents=True)
    (source / "sections" / "header.md").write_text("bundle")
    target.mkdir()
    outside = tmp_path / "missing-directory"
    _link(target / "sections", outside, directory=True)

    assert config_utils._copy_missing_tree_contents(source, target) == (0, 1)
    assert (target / "sections").is_symlink()
    assert not outside.exists()


@pytest.mark.parametrize("linked_component", ["agents", "agent"])
def test_seed_preserves_directory_links(tmp_path, monkeypatch, linked_component):
    bundled = tmp_path / "bundled"
    (bundled / "operator").mkdir(parents=True)
    (bundled / "operator" / "system_prompt.md").write_text("bundle")
    agents = tmp_path / "global" / "agents"
    agents.parent.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    if linked_component == "agents":
        link = agents
    else:
        agents.mkdir()
        link = agents / "operator"
    _link(link, outside, directory=True)
    monkeypatch.setattr(config_utils, "_get_bundled_dir", lambda name: bundled)

    config_utils._copy_seed_agents_to_global(agents, tmp_path / "old")

    assert link.is_symlink()
    assert list(outside.iterdir()) == []


def test_seed_still_copies_missing_and_preserves_existing_files(tmp_path):
    source = tmp_path / "bundled"
    target = tmp_path / "global"
    (source / "sections").mkdir(parents=True)
    (source / "sections" / "old.md").write_text("bundle old")
    (source / "sections" / "new.md").write_text("bundle new")
    (target / "sections").mkdir(parents=True)
    (target / "sections" / "old.md").write_text("user edit")

    assert config_utils._copy_missing_tree_contents(source, target) == (1, 1)
    assert (target / "sections" / "old.md").read_text() == "user edit"
    assert (target / "sections" / "new.md").read_text() == "bundle new"


def test_seed_does_not_clobber_a_concurrently_created_file(tmp_path, monkeypatch):
    source = tmp_path / "bundled"
    target = tmp_path / "global"
    source.mkdir()
    target.mkdir()
    (source / "system_prompt.md").write_text("bundle")
    destination = target / "system_prompt.md"
    original_link = config_utils.os.link
    raced = False

    def create_before_link(source_path, target_path, *args, **kwargs):
        nonlocal raced
        if Path(target_path) == destination and not raced:
            raced = True
            destination.write_text("concurrent user content")
        return original_link(source_path, target_path, *args, **kwargs)

    monkeypatch.setattr(config_utils.os, "link", create_before_link)
    assert config_utils._copy_missing_tree_contents(source, target) == (0, 1)
    assert raced
    assert destination.read_text() == "concurrent user content"


def test_seed_does_not_follow_a_concurrently_created_dangling_link(tmp_path, monkeypatch):
    source = tmp_path / "source.md"
    destination = tmp_path / "prompt.md"
    outside = tmp_path / "outside.md"
    source.write_text("bundle")
    original_link = config_utils.os.link

    def create_before_link(source_path, target_path, *args, **kwargs):
        _link(destination, outside)
        return original_link(source_path, target_path, *args, **kwargs)

    monkeypatch.setattr(config_utils.os, "link", create_before_link)
    assert not config_utils._copy_file_if_missing(source, destination)
    assert destination.is_symlink()
    assert not outside.exists()


@pytest.mark.parametrize("error", [PermissionError, NotImplementedError, AttributeError])
def test_seed_with_symlink_creation_denied_uses_regular_files(tmp_path, monkeypatch, error):
    source = tmp_path / "bundled"
    target = tmp_path / "global"
    (source / "sections").mkdir(parents=True)
    (source / "sections" / "header.md").write_text("bundle")

    def deny_symlink(*args, **kwargs):
        raise error("symlink creation unavailable")

    monkeypatch.setattr(config_utils.os, "symlink", deny_symlink)
    config_utils._copy_missing_tree_contents(source, target)
    assert (target / "sections" / "header.md").read_text() == "bundle"
    assert not (target / "sections").is_symlink()


@pytest.mark.parametrize(
    "error",
    [
        OSError(errno.ENOTSUP, "unsupported"),
        PermissionError("denied"),
        NotImplementedError("unavailable"),
        AttributeError("unavailable"),
    ],
)
def test_no_safe_primitive_leaves_bundle_usable(tmp_path, monkeypatch, caplog, error):
    from kollabor_agent import agent_manager

    home = tmp_path / "home"
    home.mkdir()
    bundled = tmp_path / "bundled"
    (bundled / "operator").mkdir(parents=True)
    (bundled / "operator" / "system_prompt.md").write_text("bundle")
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project)
    monkeypatch.setattr(config_utils, "IS_WINDOWS", False)
    monkeypatch.setattr(config_utils, "_get_bundled_dir", lambda name: bundled if name == "agents" else None)
    monkeypatch.setattr(agent_manager, "get_bundled_agents_dir", lambda: bundled)

    def deny_link(*args, **kwargs):
        raise error

    monkeypatch.setattr(config_utils.os, "link", deny_link)
    config_utils.initialize_system_prompt()
    manager = agent_manager.AgentManager(project_path=project)

    assert manager.find_agent_dir("operator") == (bundled / "operator", "bundled")
    global_agent = home / ".kollab" / "agents" / "operator"
    assert not (global_agent / "system_prompt.md").exists()
    assert not list(global_agent.glob(".*.seed-*"))
    assert "create-if-absent is unavailable" in caplog.text
    assert "Existing files were preserved" in caplog.text


def test_no_safe_primitive_reports_an_existing_global_shadow(tmp_path, monkeypatch, caplog):
    from kollabor_agent import agent_manager

    home = tmp_path / "home"
    global_agent = home / ".kollab" / "agents" / "operator"
    global_agent.mkdir(parents=True)
    (global_agent / "system_prompt.md").write_text("user-owned prompt")
    bundled = tmp_path / "bundled"
    (bundled / "operator" / "sections").mkdir(parents=True)
    (bundled / "operator" / "system_prompt.md").write_text("bundled prompt")
    (bundled / "operator" / "sections" / "header.md").write_text("header")
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project)
    monkeypatch.setattr(config_utils, "IS_WINDOWS", False)
    monkeypatch.setattr(
        config_utils,
        "_get_bundled_dir",
        lambda name: bundled if name == "agents" else None,
    )
    monkeypatch.setattr(agent_manager, "get_bundled_agents_dir", lambda: bundled)

    def deny_link(*args, **kwargs):
        raise PermissionError("hard links denied")

    monkeypatch.setattr(config_utils.os, "link", deny_link)
    config_utils.initialize_system_prompt()
    manager = agent_manager.AgentManager(project_path=project)

    assert manager.find_agent_dir("operator") == (global_agent, "global")
    assert (global_agent / "system_prompt.md").read_text() == "user-owned prompt"
    assert not (global_agent / "sections" / "header.md").exists()
    assert "still shadows the bundle" in caplog.text
    assert "may leave it incomplete" in caplog.text
    assert "Review" in caplog.text


@pytest.mark.parametrize("race", [False, True])
def test_windows_rename_fallback_preserves_concurrent_file(tmp_path, monkeypatch, race):
    source = tmp_path / "source.md"
    target = tmp_path / "target.md"
    source.write_text("bundle")
    real_rename = config_utils.os.rename
    monkeypatch.setattr(config_utils, "IS_WINDOWS", True)

    def deny_link(*args, **kwargs):
        raise PermissionError("hard links denied")

    def windows_rename(src, dst):
        if race:
            target.write_text("user")
        if Path(dst).exists() or Path(dst).is_symlink():
            raise FileExistsError(str(dst))
        return real_rename(src, dst)

    monkeypatch.setattr(config_utils.os, "link", deny_link)
    monkeypatch.setattr(config_utils.os, "rename", windows_rename)
    assert config_utils._copy_file_if_missing(source, target) is not race
    assert target.read_text() == ("user" if race else "bundle")
    assert not list(tmp_path.glob(".*.seed-*"))


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows rename only")
def test_native_windows_rename_does_not_replace_concurrent_file(tmp_path, monkeypatch):
    source = tmp_path / "source.md"
    target = tmp_path / "target.md"
    source.write_text("bundle")
    real_rename = config_utils.os.rename

    def deny_link(*args, **kwargs):
        raise PermissionError("hard links denied")

    def create_before_rename(src, dst):
        target.write_text("concurrent user content")
        return real_rename(src, dst)

    monkeypatch.setattr(config_utils.os, "link", deny_link)
    monkeypatch.setattr(config_utils.os, "rename", create_before_rename)
    assert not config_utils._copy_file_if_missing(source, target)
    assert target.read_text() == "concurrent user content"


def test_windows_denied_link_and_rename_leaves_existing_user_files(tmp_path, monkeypatch):
    source = tmp_path / "source.md"
    target = tmp_path / "target.md"
    source.write_text("bundle")
    other = tmp_path / "user.md"
    other.write_text("private user content")
    monkeypatch.setattr(config_utils, "IS_WINDOWS", True)

    def deny(*args, **kwargs):
        raise PermissionError("denied")

    monkeypatch.setattr(config_utils.os, "link", deny)
    monkeypatch.setattr(config_utils.os, "rename", deny)
    with pytest.raises(OSError, match="both hard-link and no-overwrite rename failed"):
        config_utils._copy_file_if_missing(source, target)
    assert not target.exists()
    assert other.read_text() == "private user content"
    assert not list(tmp_path.glob(".*.seed-*"))


def test_interrupted_copy_never_installs_a_partial_target(tmp_path, monkeypatch):
    source = tmp_path / "source.md"
    target = tmp_path / "target.md"
    source.write_text("complete bundle")

    def interrupted_copy(src, dst):
        dst.write(b"partial")
        raise OSError("simulated interrupted copy")

    monkeypatch.setattr(config_utils.shutil, "copyfileobj", interrupted_copy)
    with pytest.raises(OSError, match="interrupted copy"):
        config_utils._copy_file_if_missing(source, target)
    assert not target.exists()
    assert not list(tmp_path.glob(".*.seed-*"))


def test_process_exit_during_copy_leaves_no_partial_target(tmp_path):
    source = tmp_path / "source.md"
    target = tmp_path / "target.md"
    source.write_text("complete bundle")
    package_root = Path(config_utils.__file__).resolve().parents[1]
    script = """
import os, sys
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from kollabor_config import config_utils
def interrupted(src, dst):
    dst.write(b'partial')
    dst.flush()
    os._exit(23)
config_utils.shutil.copyfileobj = interrupted
config_utils._copy_file_if_missing(Path(sys.argv[2]), Path(sys.argv[3]))
"""
    process = subprocess.run(
        [sys.executable, "-c", script, str(package_root), str(source), str(target)],
        capture_output=True,
        check=False,
    )
    assert process.returncode == 23
    assert not target.exists()
    staged = list(tmp_path.glob(".*.seed-*"))
    assert len(staged) == 1 and staged[0].read_bytes() == b"partial"
    assert config_utils._copy_file_if_missing(source, target)
    assert target.read_text() == "complete bundle"
    assert staged[0].read_bytes() == b"partial"  # never silently cleaned up


def test_interruption_before_publish_never_installs_a_target(tmp_path, monkeypatch):
    source = tmp_path / "source.md"
    target = tmp_path / "target.md"
    source.write_text("complete bundle")

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(config_utils.os, "link", interrupt)
    with pytest.raises(KeyboardInterrupt):
        config_utils._copy_file_if_missing(source, target)
    assert not target.exists()
    assert not list(tmp_path.glob(".*.seed-*"))


def test_reparse_directory_is_not_traversed(tmp_path, monkeypatch):
    source = tmp_path / "source"
    target = tmp_path / "target"
    (source / "sections").mkdir(parents=True)
    (source / "sections" / "header.md").write_text("bundle")
    (target / "sections").mkdir(parents=True)
    original_lstat = Path.lstat

    def reparse_lstat(path, *args, **kwargs):
        if path == target / "sections":
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", reparse_lstat)
    assert config_utils._copy_missing_tree_contents(source, target) == (0, 1)
    assert list((target / "sections").iterdir()) == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows junctions only")
def test_existing_windows_junction_is_not_traversed(tmp_path):
    source = tmp_path / "source"
    target = tmp_path / "target"
    outside = tmp_path / "outside"
    (source / "sections").mkdir(parents=True)
    (source / "sections" / "header.md").write_text("bundle")
    target.mkdir()
    outside.mkdir()
    junction = target / "sections"
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(outside)],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        pytest.skip(f"junction creation unavailable: {result.stderr}")
    assert junction.is_junction()
    assert config_utils._copy_missing_tree_contents(source, target) == (0, 1)
    assert list(outside.iterdir()) == []


def test_seed_skips_linked_global_root_but_agent_resolution_still_works(tmp_path, monkeypatch):
    from kollabor_agent import agent_manager

    home = tmp_path / "home"
    config = home / ".kollab"
    config.mkdir(parents=True)
    outside = tmp_path / "outside"
    (outside / "coder").mkdir(parents=True)
    (outside / "coder" / "system_prompt.md").write_text("user's coder")
    _link(config / "agents", outside, directory=True)
    bundled = tmp_path / "bundled"
    for name in ("coder", "operator"):
        (bundled / name).mkdir(parents=True)
        (bundled / name / "system_prompt.md").write_text("bundled " + name)
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project)
    monkeypatch.setattr(config_utils, "_get_bundled_dir", lambda name: bundled if name == "agents" else None)
    monkeypatch.setattr(agent_manager, "get_bundled_agents_dir", lambda: bundled)

    config_utils.initialize_system_prompt()
    manager = agent_manager.AgentManager(project_path=project)

    assert manager.find_agent_dir("operator") == (bundled / "operator", "bundled")
    assert manager.find_agent_dir("coder") == (config / "agents" / "coder", "global")
    assert sorted(p.name for p in outside.iterdir()) == ["coder"]
    assert (outside / "coder" / "system_prompt.md").read_text() == "user's coder"

    local = project / ".kollab" / "agents" / "operator"
    local.mkdir(parents=True)
    (local / "system_prompt.md").write_text("project override")
    assert agent_manager.AgentManager(project_path=project).find_agent_dir("operator") == (
        local,
        "local",
    )


def test_prompt_migration_preserves_a_dangling_link(tmp_path, monkeypatch):
    agents = tmp_path / "agents"
    default = agents / "default"
    default.mkdir(parents=True)
    old = tmp_path / "old"
    old.mkdir()
    (old / "default.md").write_text("old prompt")
    outside = tmp_path / "outside.md"
    _link(default / "system_prompt.md", outside)
    monkeypatch.setattr(config_utils, "_get_bundled_dir", lambda name: None)

    config_utils._copy_seed_agents_to_global(agents, old)

    assert (default / "system_prompt.md").is_symlink()
    assert not outside.exists()
    assert not (default / "agent.json").exists()


@pytest.mark.parametrize("linked_component", ["agents", "default"])
def test_prompt_migration_preserves_directory_links(tmp_path, monkeypatch, linked_component):
    agents = tmp_path / "agents"
    outside = tmp_path / "outside"
    outside.mkdir()
    if linked_component == "agents":
        link = agents
    else:
        agents.mkdir()
        link = agents / "default"
    _link(link, outside, directory=True)
    old = tmp_path / "old"
    old.mkdir()
    (old / "default.md").write_text("old prompt")
    monkeypatch.setattr(config_utils, "_get_bundled_dir", lambda name: None)

    config_utils._copy_seed_agents_to_global(agents, old)

    assert link.is_symlink()
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("bundled", [False, True])
def test_create_defaults_preserves_dangling_prompt_link(tmp_path, monkeypatch, bundled):
    target = tmp_path / "agents" / "default"
    target.mkdir(parents=True)
    outside = tmp_path / "outside.md"
    _link(target / "system_prompt.md", outside)
    seed = tmp_path / "bundled"
    (seed / "default").mkdir(parents=True)
    (seed / "default" / "system_prompt.md").write_text("bundle")
    monkeypatch.setattr(config_utils, "_get_bundled_dir", lambda name: seed if bundled else None)

    config_utils._create_agent_from_defaults(target)

    assert (target / "system_prompt.md").is_symlink()
    assert not outside.exists()
