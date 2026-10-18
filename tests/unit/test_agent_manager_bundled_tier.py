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
