"""The default agent prompt resolves local, then global, then bundled, with nothing seeded."""

from types import SimpleNamespace

import pytest

from kollabor_config import config_utils
from kollabor_config.config_utils import (
    get_bundled_agents_dir,
    get_system_prompt_content,
    get_system_prompt_path,
)


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A computer nothing has run on: no ~/.kollab, and a project folder with no .kollab."""
    home, project = tmp_path / "home", tmp_path / "proj"
    home.mkdir()
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project)
    monkeypatch.delenv("KOLLAB_SYSTEM_PROMPT", raising=False)
    monkeypatch.delenv("KOLLAB_SYSTEM_PROMPT_FILE", raising=False)
    monkeypatch.setattr(config_utils, "_cli_system_prompt_file", None)
    return SimpleNamespace(
        global_default=home / ".kollab" / "agents" / "default",
        local_default=project / ".kollab" / "agents" / "default",
        bundled_prompt=get_bundled_agents_dir() / "default" / "system_prompt.md",
    )


def write_prompt(folder, text, name="system_prompt.md"):
    folder.mkdir(parents=True)
    (folder / name).write_text(text)
    return folder / name


def test_an_empty_home_gets_the_bundled_default_not_the_load_failure(machine):
    assert get_system_prompt_path() == machine.bundled_prompt

    content = get_system_prompt_content()

    assert content == machine.bundled_prompt.read_text(encoding="utf-8")
    assert "SYSTEM PROMPT LOAD FAILURE" not in content
    assert not machine.global_default.exists()  # resolving seeds nothing


def test_global_beats_bundled_and_local_beats_global(machine):
    mine = write_prompt(machine.global_default, "global default")
    assert get_system_prompt_path() == mine
    assert get_system_prompt_content() == "global default"

    here = write_prompt(machine.local_default, "local default")
    assert get_system_prompt_path() == here
    assert get_system_prompt_content() == "local default"


def test_the_prompt_file_variable_still_beats_every_tier(
    machine, tmp_path, monkeypatch
):
    write_prompt(machine.global_default, "global default")
    chosen = tmp_path / "chosen.md"
    chosen.write_text("from the variable")
    monkeypatch.setenv("KOLLAB_SYSTEM_PROMPT_FILE", str(chosen))

    assert get_system_prompt_path() == chosen
    assert get_system_prompt_content() == "from the variable"


def test_a_windows_prompt_is_found_in_the_same_tiers(machine, monkeypatch):
    monkeypatch.setattr(config_utils, "IS_WINDOWS", True)
    assert get_system_prompt_path() == machine.bundled_prompt  # none shipped

    win = write_prompt(machine.global_default, "global", name="system_prompt_win.md")

    assert get_system_prompt_path() == win
