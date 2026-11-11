"""The folder rule itself (#980, docs/specs/engine-folder-rule.md).

Traversal, symlinks, non-directories and missing paths.
"""

import os
from types import SimpleNamespace

import kollabor_engine.routes.workspaces as workspaces  # type: ignore[import-not-found]
import kollabor_engine.workspace_folder as workspace_folder  # type: ignore[import-not-found]
import pytest
from fastapi import HTTPException
from kollabor_engine.hub_bridge import HubBridge  # type: ignore[import-not-found]
from kollabor_engine.workspace_folder import (  # type: ignore[import-not-found]
    FolderError,
    checked_folder,
    folder_or_400,
    parent_folder,
)


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A scratch computer: a home folder, the engine's own folder, and a folder that is neither."""
    tmp_path = tmp_path.resolve()  # macOS keeps /var as a link to /private/var
    home, engine, elsewhere = (tmp_path / name for name in ("home", "engine", "elsewhere"))
    for folder in (home, engine, elsewhere, home / "proj", engine / "pkg"):
        folder.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.chdir(engine)
    return SimpleNamespace(root=tmp_path, home=home, engine=engine, elsewhere=elsewhere, proj=home / "proj")


def _refuses(path, *, saying):
    with pytest.raises(FolderError) as raised:
        checked_folder(path)
    assert saying in str(raised.value)


def _link(link, target):
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("this system cannot make symlinks")


# --- what is accepted ---------------------------------------------------------------------------------


def test_no_folder_means_the_engines_own(machine):
    assert checked_folder(None) == str(machine.engine)
    assert checked_folder("") == str(machine.engine)


def test_a_folder_in_home_is_accepted_as_its_real_path(machine):
    assert checked_folder(str(machine.proj)) == str(machine.proj)


def test_tilde_and_relative_parts_are_expanded(machine):
    assert checked_folder("~/proj") == str(machine.proj)
    assert checked_folder("pkg") == str(machine.engine / "pkg")  # relative to the engine's folder
    assert checked_folder(f"{machine.proj}{os.sep}.") == str(machine.proj)
    assert checked_folder(f"{machine.home}{os.sep}proj{os.sep}..{os.sep}proj") == str(machine.proj)


def test_a_trailing_separator_does_not_change_the_answer(machine):
    assert checked_folder(str(machine.proj) + os.sep) == str(machine.proj)


def test_home_and_the_engines_folder_are_themselves_accepted(machine):
    assert checked_folder(str(machine.home)) == str(machine.home)
    assert checked_folder(str(machine.engine)) == str(machine.engine)


def test_a_symlink_to_a_folder_inside_the_rule_is_accepted_as_its_target(machine):
    _link(machine.home / "alias", machine.proj)

    assert checked_folder(str(machine.home / "alias")) == str(machine.proj)


# --- traversal ----------------------------------------------------------------------------------------


def test_dot_dot_out_of_home_is_refused(machine):
    _refuses(f"{machine.home}{os.sep}..{os.sep}elsewhere", saying="outside")
    _refuses(f"~{os.sep}..{os.sep}elsewhere", saying="outside")


def test_dot_dot_out_of_the_engine_folder_is_refused(machine):
    _refuses(f"..{os.sep}elsewhere", saying="outside")


def test_the_top_of_the_disk_is_refused(machine):
    _refuses(os.path.abspath(os.sep), saying="outside")


def test_a_folder_that_only_shares_a_name_prefix_with_home_is_refused(machine):
    (machine.root / "home2").mkdir()

    _refuses(str(machine.root / "home2"), saying="outside")


# --- symlinks -----------------------------------------------------------------------------------------


def test_a_symlink_in_home_to_a_folder_outside_is_refused(machine):
    _link(machine.home / "link", machine.elsewhere)

    _refuses(str(machine.home / "link"), saying="outside")


def test_a_symlink_below_a_symlink_to_outside_is_refused(machine):
    _link(machine.home / "link", machine.elsewhere)
    (machine.elsewhere / "inner").mkdir()

    _refuses(str(machine.home / "link" / "inner"), saying="outside")


def test_the_refusal_does_not_say_where_a_symlink_points(machine):
    _link(machine.home / "link", machine.elsewhere)

    with pytest.raises(FolderError) as raised:
        checked_folder(str(machine.home / "link"))

    assert str(machine.elsewhere) not in str(raised.value)


# --- not a folder, not there, not a path --------------------------------------------------------------


def test_a_missing_folder_is_refused(machine):
    _refuses(str(machine.home / "nope"), saying="does not exist")


def test_a_file_is_refused(machine):
    (machine.home / "notes.txt").write_text("x")

    _refuses(str(machine.home / "notes.txt"), saying="Not a folder")


def test_a_missing_folder_outside_the_rule_does_not_reveal_whether_it_exists(machine):
    _refuses(str(machine.elsewhere / "nope"), saying="outside")


def test_a_nul_byte_is_refused(machine):
    _refuses(f"{machine.proj}\0/etc", saying="not a folder path")


def test_an_absurdly_long_path_is_refused(machine):
    _refuses(str(machine.proj) + os.sep + "a" * 5000, saying="not a folder path")


# --- the engine's folder, running agents, recent folders ----------------------------------------------


def test_an_engine_folder_outside_home_is_accepted_with_what_is_below_it(machine):
    assert checked_folder(str(machine.engine / "pkg")) == str(machine.engine / "pkg")


def test_a_folder_an_agent_runs_in_is_accepted_with_what_is_below_it(machine, monkeypatch):
    (machine.elsewhere / "sub").mkdir()
    live = [{"identity": "lapis", "project": str(machine.elsewhere)}, {"identity": "ruby", "project": ""}]
    monkeypatch.setattr(HubBridge, "get_agents", lambda self, use_cache=True: live)

    assert checked_folder(str(machine.elsewhere)) == str(machine.elsewhere)
    assert checked_folder(str(machine.elsewhere / "sub")) == str(machine.elsewhere / "sub")


def test_a_folder_a_live_web_session_runs_in_is_accepted(machine, monkeypatch):
    registry = {"s1": SimpleNamespace(workspace=str(machine.elsewhere), alive=True)}
    monkeypatch.setattr("kollabor_engine.server.get_session_registry", lambda: registry)

    assert checked_folder(str(machine.elsewhere)) == str(machine.elsewhere)


def test_a_folder_a_dead_web_session_ran_in_is_not_accepted(machine, monkeypatch):
    registry = {"s1": SimpleNamespace(workspace=str(machine.elsewhere), alive=False)}
    monkeypatch.setattr("kollabor_engine.server.get_session_registry", lambda: registry)

    _refuses(str(machine.elsewhere), saying="outside")


def test_a_recent_session_folder_is_accepted(machine):
    workspaces.remember_workspace(str(machine.elsewhere))

    assert checked_folder(str(machine.elsewhere)) == str(machine.elsewhere)


def test_a_folder_nobody_has_used_is_refused_with_a_way_forward(machine):
    with pytest.raises(FolderError) as raised:
        checked_folder(str(machine.elsewhere))

    assert "outside your home folder" in str(raised.value)
    assert "no kollab agent runs there" in str(raised.value)


def test_a_failing_lookup_of_running_agents_only_narrows_the_rule(machine, monkeypatch):
    def broken(self, use_cache=True):
        raise OSError("presence unreadable")

    monkeypatch.setattr(HubBridge, "get_agents", broken)

    assert checked_folder(str(machine.proj)) == str(machine.proj)
    _refuses(str(machine.elsewhere), saying="outside")


def test_a_folder_in_home_never_asks_which_agents_are_running(machine, monkeypatch):
    def forbidden(self, use_cache=True):
        raise AssertionError("looked up running agents for a folder in home")

    monkeypatch.setattr(HubBridge, "get_agents", forbidden)

    assert checked_folder(str(machine.proj)) == str(machine.proj)
    assert checked_folder(None) == str(machine.engine)


# --- a remembered folder is the folder that was remembered --------------------------------------------
# Every source of roots above records a real path: a recent folder is saved as one, a session runs in the checked
# one, an agent reports its resolved project folder. If that text no longer leads to itself (a symlink now stands
# in the folder's place, or in one of its parents), the folder that was remembered is not the one the pathname
# reaches, so the remembered path grants nothing, not even what it leads to now.


def _swap_for_link(folder, target):
    """Move ``folder`` out of the way and put a symlink to ``target`` at its old pathname."""
    folder.rename(folder.with_name(folder.name + "-moved"))
    _link(folder, target)


def test_a_recent_folder_swapped_for_a_symlink_grants_its_new_target_nothing(machine):
    old, other = machine.root / "old-workspace", machine.root / "other"
    old.mkdir()
    other.mkdir()
    workspaces.remember_workspace(str(old))
    _swap_for_link(old, other)

    _refuses(str(other), saying="outside")
    _refuses(str(old), saying="outside")


def test_a_recent_folder_whose_parent_was_swapped_for_a_symlink_grants_its_new_target_nothing(machine):
    projects, other = machine.root / "projects", machine.root / "other-projects"
    (projects / "app").mkdir(parents=True)
    (other / "app").mkdir(parents=True)
    workspaces.remember_workspace(str(projects / "app"))
    _swap_for_link(projects, other)

    _refuses(str(other / "app"), saying="outside")


def test_a_running_agents_folder_swapped_for_a_symlink_grants_its_new_target_nothing(machine, monkeypatch):
    old, other = machine.root / "agent-folder", machine.root / "other"
    old.mkdir()
    other.mkdir()
    live = [{"identity": "lapis", "project": str(old)}]
    monkeypatch.setattr(HubBridge, "get_agents", lambda self, use_cache=True: live)
    _swap_for_link(old, other)

    _refuses(str(other), saying="outside")


def test_a_live_web_sessions_folder_swapped_for_a_symlink_grants_its_new_target_nothing(machine, monkeypatch):
    old, other = machine.root / "session-folder", machine.root / "other"
    old.mkdir()
    other.mkdir()
    registry = {"s1": SimpleNamespace(workspace=str(old), alive=True)}
    monkeypatch.setattr("kollabor_engine.server.get_session_registry", lambda: registry)
    _swap_for_link(old, other)

    _refuses(str(other), saying="outside")


def test_a_recent_folder_made_again_at_the_same_pathname_still_counts(machine):
    old = machine.root / "old-workspace"
    old.mkdir()
    workspaces.remember_workspace(str(old))
    old.rmdir()
    old.mkdir()

    assert checked_folder(str(old)) == str(old)


def test_a_remembered_folder_written_with_a_trailing_separator_is_the_same_folder(machine, monkeypatch):
    monkeypatch.setattr(workspaces, "_stored", lambda: [str(machine.elsewhere) + os.sep])

    assert checked_folder(str(machine.elsewhere)) == str(machine.elsewhere)


def test_a_remembered_entry_that_cannot_be_a_path_is_skipped_and_the_others_still_count(machine, monkeypatch):
    monkeypatch.setattr(workspaces, "_stored", lambda: ["broken\0entry", str(machine.elsewhere)])

    assert checked_folder(str(machine.elsewhere)) == str(machine.elsewhere)
    _refuses(str(machine.root / "nowhere-known"), saying="outside")


# --- case ---------------------------------------------------------------------------------------------
# macOS and Windows volumes call /Users/me/Dev and /users/me/dev one folder, so a request in either spelling must
# meet the same roots. A volume that tells them apart keeps them apart.


def test_the_volume_decides_whether_a_folder_typed_in_another_case_is_the_same_folder(machine):
    variant = str(machine.home).swapcase() + os.sep + "proj"

    if os.path.isdir(variant):  # this volume ignores case: the same folder, so the same answer
        assert checked_folder(variant) == str(machine.proj)
    else:  # it tells the cases apart: another name, outside every root
        _refuses(variant, saying="outside")


def test_another_case_is_accepted_where_the_volume_ignores_case(machine, monkeypatch):
    monkeypatch.setattr(workspace_folder, "_ignores_case", lambda root: True, raising=False)

    assert checked_folder(str(machine.home).upper() + os.sep + "proj") == str(machine.proj)
    assert checked_folder(str(machine.engine).upper()) == str(machine.engine)


def test_another_case_is_refused_where_the_volume_tells_the_cases_apart(machine, monkeypatch):
    monkeypatch.setattr(workspace_folder, "_ignores_case", lambda root: False, raising=False)

    _refuses(str(machine.home).upper() + os.sep + "proj", saying="outside")


# A volume that ignores case opens /Users/me/PROJ for /Users/me/proj, but a daemon started there reports the spelling
# the volume stores (its working folder), and the engine finds the agent it launched by comparing folder strings
# (HubBridge.get_agent_by_identity): the answer must be the stored spelling, or New Session waits 45 s for a daemon
# it cannot find.


@pytest.fixture
def ignoring_case(monkeypatch):
    """A volume that ignores case, wherever the tests run: two spellings of a path are one folder."""
    monkeypatch.setattr(workspace_folder, "_ignores_case", lambda root: True, raising=False)
    monkeypatch.setattr(os.path, "samefile", lambda a, b: a.casefold() == b.casefold())


def _root_of(folder):
    return os.path.join(str(folder), "")


def test_a_folder_typed_in_another_case_comes_back_as_the_volume_stores_it(machine, monkeypatch):
    typed = machine.home / "PROJ"
    if not typed.is_dir():
        pytest.skip("this volume tells the cases apart")

    with monkeypatch.context() as inside:
        inside.chdir(typed)  # where a daemon started in that folder lives
        stored = os.path.realpath(os.getcwd())

    assert stored == str(machine.proj)
    assert checked_folder(str(typed)) == stored


def test_the_answer_is_what_the_stored_spelling_step_makes_of_it_where_the_volume_ignores_case(machine, monkeypatch):
    seen = []
    monkeypatch.setattr(workspace_folder, "_ignores_case", lambda root: True, raising=False)
    monkeypatch.setattr(
        workspace_folder, "_on_disk", lambda checked, root: seen.append((checked, root)) or "as-stored", raising=False
    )

    assert checked_folder(str(machine.proj)) == "as-stored"
    assert seen == [(str(machine.proj), _root_of(machine.home))]


def test_the_answer_is_left_as_typed_where_the_volume_tells_the_cases_apart(machine, monkeypatch):
    monkeypatch.setattr(workspace_folder, "_ignores_case", lambda root: False, raising=False)
    monkeypatch.setattr(workspace_folder, "_on_disk", lambda *args: pytest.fail("looked up a spelling"), raising=False)

    assert checked_folder(str(machine.proj)) == str(machine.proj)


def test_a_part_below_the_root_is_spelled_the_way_the_folder_stores_it(machine, ignoring_case):
    assert workspace_folder._on_disk(str(machine.home / "PROJ"), _root_of(machine.home)) == str(machine.proj)


def test_every_part_below_the_root_is_spelled_the_way_its_folder_stores_it(machine, ignoring_case):
    (machine.home / "Work" / "App").mkdir(parents=True)

    typed = str(machine.home / "wORK" / "aPP")
    assert workspace_folder._on_disk(typed, _root_of(machine.home)) == str(machine.home / "Work" / "App")


def test_a_part_spelled_as_stored_is_not_looked_up(machine, ignoring_case, monkeypatch):
    asked = []
    monkeypatch.setattr(os.path, "samefile", lambda a, b: asked.append((a, b)) or True)

    assert workspace_folder._on_disk(str(machine.proj), _root_of(machine.home)) == str(machine.proj)
    assert asked == []


def test_a_part_the_volume_does_not_call_the_same_entry_stays_as_typed(machine, monkeypatch):
    def not_there(a, b):  # what samefile does for a name that is no entry: a mount that tells the cases apart
        raise FileNotFoundError(b)

    monkeypatch.setattr(os.path, "samefile", not_there)

    typed = str(machine.home / "PROJ")
    assert workspace_folder._on_disk(typed, _root_of(machine.home)) == typed


def test_a_part_with_no_entry_stays_as_typed(machine, ignoring_case):
    typed = str(machine.home / "nope")
    assert workspace_folder._on_disk(typed, _root_of(machine.home)) == typed


def test_a_folder_that_cannot_be_listed_leaves_what_is_typed(machine, ignoring_case, monkeypatch):
    def refused(path):
        raise PermissionError(path)

    typed = str(machine.home / "PROJ")
    with monkeypatch.context() as inside:
        inside.setattr(os, "listdir", refused)
        assert workspace_folder._on_disk(typed, _root_of(machine.home)) == typed


# --- the HTTP face and the picker's Up button ---------------------------------------------------------


def test_the_http_face_answers_400_with_the_same_words(machine):
    with pytest.raises(HTTPException) as raised:
        folder_or_400(str(machine.elsewhere))

    assert raised.value.status_code == 400
    assert "outside" in raised.value.detail
    assert folder_or_400(str(machine.proj)) == str(machine.proj)


def test_up_is_the_parent_while_the_rule_allows_it(machine):
    assert parent_folder(str(machine.proj)) == str(machine.home)
    assert parent_folder(str(machine.home)) is None
    assert parent_folder(str(machine.engine / "pkg")) == str(machine.engine)
    assert parent_folder(str(machine.engine)) is None
