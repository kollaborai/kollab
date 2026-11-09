"""The folder rule itself (#980, docs/specs/engine-folder-rule.md).

Traversal, symlinks, non-directories and missing paths.
"""

import os
from types import SimpleNamespace

import kollabor_engine.routes.workspaces as workspaces  # type: ignore[import-not-found]
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

