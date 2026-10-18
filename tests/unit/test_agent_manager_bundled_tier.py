"""AgentManager resolves an agent local, then global, then bundled, with nothing seeded."""

from types import SimpleNamespace

import pytest

from kollabor_agent.agent_manager import Agent, AgentManager
from kollabor_config.config_utils import get_bundled_agents_dir


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


def test_the_local_tier_is_the_given_folders_not_the_cwd(machine, tmp_path):
    # the engine runs in one folder and starts chats in others
    elsewhere = tmp_path / "elsewhere"
    there = write_agent(elsewhere / ".kollab" / "agents", "coder")
    here = write_agent(machine.local_agents, "coder")
    only_there = write_agent(elsewhere / ".kollab" / "agents", "only-there")

    assert AgentManager().find_agent_dir("coder") == (here, "local")
    assert AgentManager(project_path=elsewhere).find_agent_dir("coder") == (
        there,
        "local",
    )
    assert AgentManager().find_agent_dir("only-there") is None
    assert AgentManager(project_path=elsewhere).find_agent_dir("only-there") == (
        only_there,
        "local",
    )


def test_a_given_folder_without_local_agents_still_resolves_global_then_bundled(
    machine, tmp_path
):
    write_agent(machine.local_agents, "coder")  # the cwd's own must not leak in
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    assert AgentManager(project_path=elsewhere).find_agent_dir("coder")[1] == "bundled"

    mine = write_agent(machine.global_agents, "coder")
    assert AgentManager(project_path=elsewhere).find_agent_dir("coder") == (
        mine,
        "global",
    )


def test_the_skill_library_and_new_agents_follow_the_given_folder(machine, tmp_path):
    elsewhere = tmp_path / "elsewhere"
    skill = elsewhere / ".kollab" / "skills" / "only-there-skill"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: only-there-skill\ndescription: d\n---\nbody"
    )

    manager = AgentManager(project_path=elsewhere)

    assert "only-there-skill" in manager.skill_library.list_names()
    assert "only-there-skill" not in AgentManager().skill_library.list_names()
    assert manager.create_agent("fresh", "a new one") is not None
    assert (elsewhere / ".kollab" / "agents" / "fresh").is_dir()
    assert not (machine.local_agents / "fresh").exists()


@pytest.fixture
def hub_protocol(machine, monkeypatch):
    """The coder agent, a renderer that returns the doc as is, and the hub switched on."""
    monkeypatch.setenv("KOLLAB_HUB_ENABLED", "1")
    for key in ("KOLLAB_HUB_SOLO", "KOLLAB_HUB_DISABLED", "KOLLAB_NO_HUB"):
        monkeypatch.delenv(key, raising=False)
    folder, tier = AgentManager().find_agent_dir("coder")
    agent = Agent.from_directory(folder, source=tier)
    renderer = SimpleNamespace(render=lambda text: text, prompt_bundle="coder")
    bundled_docs = sorted((get_bundled_agents_dir() / "system").glob("hub-*.md"))
    return SimpleNamespace(
        load=lambda project_path=None: agent._load_hub_protocol(renderer, project_path),
        bundled=bundled_docs[0].read_text(encoding="utf-8"),
    )


def write_hub_doc(agents_dir, text):
    (agents_dir / "system").mkdir(parents=True)
    (agents_dir / "system" / "hub-collaboration.md").write_text(text)


def test_the_hub_protocol_is_the_bundled_doc_on_an_empty_home(machine, hub_protocol):
    assert hub_protocol.bundled  # the repo ships one
    assert hub_protocol.load() == hub_protocol.bundled
    assert not machine.global_agents.exists()  # reading it seeds nothing


def test_the_hub_protocol_local_beats_global_beats_bundled(
    machine, hub_protocol, tmp_path
):
    write_hub_doc(machine.global_agents, "global doc")
    assert hub_protocol.load() == "global doc"

    write_hub_doc(machine.local_agents, "local doc")
    assert hub_protocol.load() == "local doc"

    # a chat in another folder gets that folder's doc, not the cwd's
    elsewhere = tmp_path / "elsewhere"
    write_hub_doc(elsewhere / ".kollab" / "agents", "elsewhere doc")
    assert hub_protocol.load(elsewhere) == "elsewhere doc"
    bare = tmp_path / "bare"
    bare.mkdir()
    assert hub_protocol.load(bare) == "global doc"


def test_the_hub_protocol_stays_off_without_the_hub(machine, hub_protocol, monkeypatch):
    monkeypatch.delenv("KOLLAB_HUB_ENABLED")

    assert hub_protocol.load() == ""
