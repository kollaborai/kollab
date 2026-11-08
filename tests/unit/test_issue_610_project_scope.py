"""Issue #610 review round 4: a save from one project must not replace the key
another project's own config points to.

Vault entries are keyed by a name. A profile saved while a project's
``.kollab/config.json`` already holds profiles is written to the global config
and to that project's config, so the project's reference needs an entry of its
own. Otherwise project B's save silently swaps the key project A runs on.
"""

import json
from pathlib import Path

import pytest

from kollabor_ai.profile_manager import (
    _KEYRING_MIGRATION_TRIED,
    LLMProfile,
    ProfileManager,
)

NAME = "work"
KEY_A = "sk-ant-issue610-projA-" + "a1" * 12
KEY_B = "sk-ant-issue610-projB-" + "b2" * 12
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
    projects = {}
    for label in ("a", "b", "c"):
        project = tmp_path / f"project-{label}"
        (project / ".kollab").mkdir(parents=True)
        if label != "c":  # c has no project profiles: it only sees the global one
            (project / ".kollab" / "config.json").write_text(json.dumps(SEED))
        projects[label] = project
    _KEYRING_MIGRATION_TRIED.clear()
    yield projects
    _KEYRING_MIGRATION_TRIED.clear()


def _save(project: Path, key: str, monkeypatch) -> None:
    monkeypatch.chdir(project)
    profile = LLMProfile.from_dict(NAME, {**FIELDS, "api_key": key})
    pm = ProfileManager.__new__(ProfileManager)
    pm.config = None
    assert pm.save_profile_values_to_config(profile)


def _resolve(project: Path, monkeypatch) -> str:
    """The key this project's merged config resolves for the profile."""
    monkeypatch.chdir(project)
    _KEYRING_MIGRATION_TRIED.clear()
    return ProfileManager().get_profile(NAME).get_api_key()


def test_a_save_in_one_project_keeps_another_projects_key(world, monkeypatch):
    _save(world["a"], KEY_A, monkeypatch)
    _save(world["b"], KEY_B, monkeypatch)

    assert _resolve(world["a"], monkeypatch) == KEY_A
    assert _resolve(world["b"], monkeypatch) == KEY_B


def test_the_global_profile_follows_the_latest_save(world, monkeypatch):
    _save(world["a"], KEY_A, monkeypatch)
    _save(world["b"], KEY_B, monkeypatch)

    assert _resolve(world["c"], monkeypatch) == KEY_B
