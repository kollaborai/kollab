"""A persona's skills attach to the running agent (docs/specs/rosters-and-teams.md, "What a persona does").

``attach_persona_skills`` is the one place they are applied: the hub calls it once the bundle is
settled, and every launch (``--as``, ``POST /sessions``, a team launch, ``hub_spawn``) ends in that
process. Live proof of the three launch paths: tests/tmux/specs/hub_persona_skills.json (``--as``)
and the engine routes, which only pass ``--as <ref>`` on.
"""

import asyncio
from types import SimpleNamespace

from plugins.hub.rosters import Persona, attach_persona_skills


class FakeAgent:
    def __init__(self, declared=(), active=()):
        self.skills = {name: SimpleNamespace(name=name, content=f"{name} body") for name in declared}
        self.active_skills = list(active)

    def get_skill(self, name):
        return self.skills.get(name)


class FakeManager:
    def __init__(self, agent, library=()):
        self.agent = agent
        self.skill_library = SimpleNamespace(
            get_skill=lambda name: SimpleNamespace(name=name, content=f"{name} body") if name in library else None
        )

    def get_active_agent(self):
        return self.agent


class FakeState:
    """activate_skill as LocalStateService has it: only a skill the agent holds can be activated."""

    def __init__(self, agent, broken=()):
        self.agent = agent
        self.broken = broken
        self.activated = []

    async def activate_skill(self, name):
        if name in self.broken:
            raise ValueError("inject failed")
        assert name in self.agent.skills, "activate_skill needs the skill on the agent"
        self.agent.active_skills.append(name)
        self.activated.append(name)


def run(persona, agent, library=(), broken=()):
    state = FakeState(agent, broken)
    problems = asyncio.run(attach_persona_skills(persona, FakeManager(agent, library), state))
    return problems, state.activated


def test_declared_and_library_skills_both_attach():
    agent = FakeAgent(declared=["security-review"])
    problems, activated = run(Persona(name="maya", skills=["security-review", "tdd"]), agent, library=["tdd"])
    assert problems == [] and activated == ["security-review", "tdd"]
    assert "tdd" in agent.skills  # the library skill now sits on the agent


def test_a_skill_that_will_not_attach_is_reported_and_the_rest_still_attach():
    agent = FakeAgent(declared=["a", "b", "c"])
    problems, activated = run(Persona(name="maya", skills=["a", "nope", "b", "c"]), agent, broken=["b"])
    assert activated == ["a", "c"]
    assert problems == ["nope: not in the skill library", "b: inject failed"]


def test_a_skill_already_active_is_left_alone():
    agent = FakeAgent(declared=["a", "b"], active=["a"])  # `--skill a` got there first
    problems, activated = run(Persona(name="maya", skills=["a", "b"]), agent)
    assert problems == [] and activated == ["b"]


def test_no_agent_reports_every_skill():
    persona = Persona(name="maya", skills=["a", "b"])
    problems = asyncio.run(attach_persona_skills(persona, FakeManager(None), None))
    assert problems == ["a: no agent to attach it to", "b: no agent to attach it to"]


def test_a_persona_without_skills_touches_nothing():
    agent = FakeAgent()
    assert run(Persona(name="lapis"), agent) == ([], [])
