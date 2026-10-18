"""/agent and /skills tell a bundled agent from a global one, with nothing seeded."""

from unittest.mock import MagicMock

import pytest

from kollabor.commands.system_commands.handlers.agent import AgentCommandHandler
from kollabor.commands.system_commands.handlers.skills import SkillCommandHandler
from kollabor_agent.agent_manager import AgentManager


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A computer nothing has run on: no ~/.kollab, and a project folder with no .kollab."""
    home, project = tmp_path / "home", tmp_path / "proj"
    home.mkdir()
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project)
    return home / ".kollab" / "agents"


def agent_rows(manager):
    modal = AgentCommandHandler(
        MagicMock(), MagicMock(), manager
    )._get_agents_modal_definition(skip_reload=True)
    return modal, {
        row["agent_name"]: row["name"] for row in modal["sections"][0]["commands"]
    }


def skill_rows(manager, agent):
    manager.set_active_agent(agent)
    modal = SkillCommandHandler(
        MagicMock(), MagicMock(), manager
    )._get_skills_modal_definition(skip_reload=True)
    return {
        row["skill_name"]: row["description"]
        for row in modal["sections"][0]["commands"]
    }


def test_a_bundled_agent_has_its_own_letter_in_the_agent_picker(machine):
    modal, rows = agent_rows(AgentManager())

    assert rows["coder"].startswith("[ B ]")
    assert "B=bundled" in modal["footer"]


def test_a_global_copy_is_still_g(machine):
    (machine / "coder").mkdir(parents=True)
    (machine / "coder" / "system_prompt.md").write_text("mine")

    _, rows = agent_rows(AgentManager())

    assert rows["coder"].startswith("[ G ]")


def test_the_default_skills_of_a_bundled_agent_show_in_the_skills_picker(machine):
    rows = skill_rows(AgentManager(), "kollabor")

    assert rows["kollab-development"].startswith("[bundled] ")
    assert not rows["debugging"].startswith("[")


def test_a_seeded_copy_shows_its_defaults_as_global(machine):
    folder = machine / "kollabor"
    folder.mkdir(parents=True)
    (folder / "system_prompt.md").write_text("mine")
    (folder / "agent.json").write_text(
        '{"skills": ["debugging", "kollab-development"], "default_skills": ["debugging"]}'
    )

    rows = skill_rows(AgentManager(), "kollabor")

    assert rows["debugging"].startswith("[global] ")
    assert not rows["kollab-development"].startswith("[")
