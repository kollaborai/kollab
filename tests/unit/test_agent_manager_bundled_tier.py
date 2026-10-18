"""AgentManager resolves an agent local, then global, then bundled, with nothing seeded."""

from types import SimpleNamespace

import pytest

from kollabor_agent.agent_manager import AgentManager


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A computer nothing has run on: no ~/.kollab, and a project folder with no .kollab."""
    home, project = tmp_path / "home", tmp_path / "proj"
    home.mkdir()
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project)
    return SimpleNamespace(
        global_agents=home / ".kollab" / "agents",
        local_agents=project / ".kollab" / "agents",
    )


def write_agent(agents_dir, name):
    folder = agents_dir / name
    folder.mkdir(parents=True)
    (folder / "system_prompt.md").write_text(
        f"{name} from {agents_dir.parent.parent.name}"
    )
    return folder


def test_a_bundled_agent_resolves_on_an_empty_home(machine):
    manager = AgentManager()

    folder, tier = manager.find_agent_dir("coder")

    assert (
        tier == "bundled"
        and folder.name == "coder"
        and folder.parent.parts[-2:] == ("bundles", "agents")
    )
    assert {"coder", "research", "default"} <= set(manager.get_agent_names())
    assert manager.get_agent("coder").source == "bundled"
    assert not machine.global_agents.exists()  # resolving seeds nothing


def test_global_beats_bundled_and_local_beats_global(machine):
    assert AgentManager().find_agent_dir("coder")[1] == "bundled"

    mine = write_agent(machine.global_agents, "coder")
    assert AgentManager().find_agent_dir("coder") == (mine, "global")

    here = write_agent(machine.local_agents, "coder")
    manager = AgentManager()
    assert manager.find_agent_dir("coder") == (here, "local")
    assert manager.get_agent("coder").source == "local"


def test_an_agent_only_the_user_has_still_resolves(machine):
    mine = write_agent(machine.global_agents, "my-own")

    manager = AgentManager()

    assert manager.find_agent_dir("my-own") == (mine, "global")
    assert "my-own" in manager.get_agent_names()


def test_an_unknown_agent_resolves_nowhere(machine):
    assert AgentManager().find_agent_dir("no-such-agent") is None


def test_a_folder_with_no_system_prompt_does_not_hide_the_agent_below_it(machine):
    # a default-skill toggle leaves a lone agent.json in the tier above
    lone = machine.global_agents / "coder"
    lone.mkdir(parents=True)
    (lone / "agent.json").write_text('{"default_skills": ["debugging"]}')

    manager = AgentManager()

    assert manager.find_agent_dir("coder")[1] == "bundled"
    assert manager.get_agent("coder").source == "bundled"


def test_reloading_an_agent_reads_the_tier_it_resolves_to(machine):
    mine = write_agent(machine.global_agents, "coder")
    manager = AgentManager()
    assert manager.get_agent("coder").description == ""

    (mine / "agent.json").write_text('{"description": "edited"}')
    manager._reload_agent("coder")

    assert manager.get_agent("coder").description == "edited"
    assert manager.get_agent("coder").source == "global"
