"""Issue #610 review round 5: a save keeps a reference only where it belongs.

A ``secret:keyring:<name>`` reference names one vault entry. Writing the same
reference into a project config or into a copied profile makes two places share
that entry, so a later save that rotates one changes the other's key.
"""

import json
from pathlib import Path

import pytest

from kollabor_ai.profile_manager import (
    _KEYRING_MIGRATION_TRIED,
    KEYRING_SENTINEL_PREFIX,
    LLMProfile,
    ProfileManager,
)

NAME = "work"
KEY_GLOBAL = "sk-ant-issue610-global-" + "g1" * 12
KEY_B = "sk-ant-issue610-projB-" + "b2" * 12
KEY_OLD = "sk-ant-issue610-old-" + "o3" * 12
KEY_NEW = "sk-ant-issue610-new-" + "n4" * 12
FIELDS = {"provider": "anthropic", "model": "claude-sonnet-4-6"}
SEED = {"kollabor": {"llm": {"profiles": {"seed": dict(FIELDS)}}}}


@pytest.fixture
def world(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("KOLLAB_NO_KEYRING", "1")
    for name in (
        "KOLLAB_KEY_ENCRYPTION_PASSWORD",
        "KOLLAB_API_KEY",
        f"KOLLAB_{NAME.upper()}_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    projects = {"home": home}
    for label in ("a", "b", "plain"):
        project = tmp_path / f"project-{label}"
        (project / ".kollab").mkdir(parents=True)
        if label != "plain":  # plain has no project profiles
            (project / ".kollab" / "config.json").write_text(json.dumps(SEED))
        projects[label] = project
    _KEYRING_MIGRATION_TRIED.clear()
    yield projects
    _KEYRING_MIGRATION_TRIED.clear()


def _manager(project: Path, monkeypatch) -> ProfileManager:
    monkeypatch.chdir(project)
    _KEYRING_MIGRATION_TRIED.clear()
    return ProfileManager()


def _resolve(project: Path, name: str, monkeypatch) -> str:
    return _manager(project, monkeypatch).get_profile(name).get_api_key()


def _create_global(project: Path, key: str, monkeypatch) -> None:
    """Save the profile from a folder that has no project profiles."""
    monkeypatch.chdir(project)
    profile = LLMProfile.from_dict(NAME, {**FIELDS, "api_key": key})
    pm = ProfileManager.__new__(ProfileManager)
    pm.config = None
    assert pm.save_profile_values_to_config(profile)


def test_a_save_that_keeps_the_key_does_not_link_a_project_to_the_global_entry(
    world, monkeypatch
):
    _create_global(world["plain"], KEY_GLOBAL, monkeypatch)

    # Project A re-saves the profile without touching its key.
    assert _manager(world["a"], monkeypatch).update_profile(
        NAME, model="claude-opus-4-1", save_to_config=True
    )
    # Project B rotates the key; that rewrites the global entry.
    assert _manager(world["b"], monkeypatch).update_profile(
        NAME, api_key=KEY_B, save_to_config=True
    )

    assert _resolve(world["a"], NAME, monkeypatch) == KEY_GLOBAL
    assert _resolve(world["b"], NAME, monkeypatch) == KEY_B


def test_a_copy_does_not_share_the_source_profiles_entry(world, monkeypatch):
    _create_global(world["plain"], KEY_OLD, monkeypatch)

    assert _manager(world["plain"], monkeypatch).duplicate_profile(NAME, "work2")
    assert _manager(world["plain"], monkeypatch).update_profile(
        NAME, api_key=KEY_NEW, save_to_config=True
    )

    assert _resolve(world["plain"], "work2", monkeypatch) == KEY_OLD
    assert _resolve(world["plain"], NAME, monkeypatch) == KEY_NEW


def test_a_save_from_the_home_folder_keeps_the_global_reference(world, monkeypatch):
    _create_global(world["plain"], KEY_GLOBAL, monkeypatch)

    # Working directory is $HOME: the "local" config is the global config.
    assert _manager(world["home"], monkeypatch).update_profile(
        NAME, model="claude-opus-4-1", save_to_config=True
    )

    config = json.loads((world["home"] / ".kollab" / "config.json").read_text())
    stored = config["kollabor"]["llm"]["profiles"][NAME]["api_key"]
    assert stored == f"{KEYRING_SENTINEL_PREFIX}{NAME}"
    assert _resolve(world["plain"], NAME, monkeypatch) == KEY_GLOBAL
