"""A skill added or removed from Apps reaches the running session without a restart."""

import asyncio
import json
import time
from types import SimpleNamespace

from kollabor.panels.apps import apply_change
from kollabor_agent import agent_manager as am
from kollabor_agent.agent_manager import SkillLibrary


def _skill(folder, name):
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(f"---\nname: {name}\ndescription: Test skill {name}.\n---\nbody\n")


def _library(skills_dir):
    library = SkillLibrary()
    library.bundled_dir, library.global_dir, library.local_dir = None, skills_dir, None
    library.refresh()
    return library


def test_refresh_finds_a_skill_added_on_disk(tmp_path):
    library = _library(tmp_path)
    assert library.get_skill("late-skill") is None

    _skill(tmp_path / "late-skill", "late-skill")
    library.refresh()

    assert library.get_skill("late-skill") is not None


def test_every_agent_gets_the_skills_the_user_added(tmp_path):
    _skill(tmp_path / "bundled" / "tdd", "tdd")
    _skill(tmp_path / "bundled" / "pdf-tools", "pdf-tools")
    _skill(tmp_path / "home" / "brainstorming", "brainstorming")
    library = SkillLibrary()
    library.bundled_dir, library.global_dir, library.local_dir = tmp_path / "bundled", tmp_path / "home", None
    library.refresh()

    assert set(library.get_skills_for_agent(["tdd"])) == {"tdd", "brainstorming"}
    assert set(library.get_skills_for_agent([])) == {"brainstorming"}


def test_agent_cache_is_ignored_once_the_skill_library_changes(tmp_path):
    skills = tmp_path / "skills"
    skills.mkdir()
    library = _library(skills)
    cache = tmp_path / "agent_metadata.cache"
    agents = {"coder": {"name": "coder"}}
    cache.write_text(
        json.dumps(
            {
                "version": am.CACHE_VERSION,
                "timestamp": int(time.time()),
                "skills_fingerprint": library.fingerprint(),
                "agents": agents,
            }
        )
    )
    assert am._load_cached_agents(cache, library.fingerprint()) == agents

    _skill(skills / "new-skill", "new-skill")
    library.refresh()

    assert am._load_cached_agents(cache, library.fingerprint()) == {}


def test_apply_change_reloads_agents_and_the_prompt():
    calls = []
    ctx = SimpleNamespace(
        _agent_manager=SimpleNamespace(refresh=lambda: calls.append("agents")),
        _llm_service=SimpleNamespace(rebuild_system_prompt=lambda: calls.append("prompt")),
    )

    asyncio.run(apply_change(ctx, {"ok": True, "message": "Added X.", "mcp_changed": False, "skills_changed": True}))
    asyncio.run(apply_change(ctx, {"ok": True, "message": "Added Y.", "mcp_changed": False, "skills_changed": False}))

    assert calls == ["agents", "prompt"]
