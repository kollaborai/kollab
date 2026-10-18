"""The System Prompts page reads agents local, then global, then bundled, on any install."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from kollabor_engine.routes import prompts

from kollabor_ai.prompt_overrides import PromptOverrideStore
from kollabor_config import config_utils


@pytest.fixture
def machine(tmp_path, monkeypatch):
    """A computer nothing has run on, and the routes wired as the engine wires them."""
    home, project = tmp_path / "home", tmp_path / "proj"
    home.mkdir()
    project.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(project)
    monkeypatch.delenv("KOLLAB_SYSTEM_PROMPT", raising=False)
    monkeypatch.delenv("KOLLAB_SYSTEM_PROMPT_FILE", raising=False)
    monkeypatch.setattr(config_utils, "_cli_system_prompt_file", None)
    monkeypatch.setattr(prompts, "_walker", None)
    monkeypatch.setattr(
        prompts, "_store", PromptOverrideStore(root=tmp_path / "overrides")
    )
    app = FastAPI()
    app.include_router(prompts.router)
    return SimpleNamespace(
        client=TestClient(app),
        global_agents=home / ".kollab" / "agents",
        local_agents=project / ".kollab" / "agents",
    )


def write_agent(agents_dir, name, prompt):
    folder = agents_dir / name
    (folder / "sections").mkdir(parents=True)
    (folder / "system_prompt.md").write_text(prompt)
    (folder / "sections" / "00-mine.md").write_text("my own section\n")
    return folder


def preview(machine, bundle):
    response = machine.client.get(f"/prompts/{bundle}/preview")
    assert response.status_code == 200, response.text
    return response.json()["prompt"]


def test_the_default_preview_on_an_empty_home_is_the_real_prompt(machine):
    prompt = preview(machine, "default")

    assert "SYSTEM PROMPT LOAD FAILURE" not in prompt
    assert "## Available Agents" in prompt and "**coder**" in prompt
    assert not machine.global_agents.exists()  # previewing seeds nothing


def test_a_bundle_preview_lists_the_available_agents(machine):
    prompt = preview(machine, "coder")

    assert "[No agent manager available]" not in prompt
    assert "## Available Agents" in prompt
    assert all(f"**{name}**" in prompt for name in ("coder", "research", "default"))


def test_a_preview_survives_the_agent_cache_the_first_request_wrote(machine):
    preview(machine, "coder")

    # the cache hands agents out without their prompt text
    assert "## Available Agents" in preview(machine, "coder")
    assert machine.client.get("/prompts/coder/sections").status_code == 200


def test_an_agent_only_the_user_has_lists_sections_and_previews(machine):
    write_agent(
        machine.global_agents,
        "my-own",
        '<trender type="include" path="sections/00-mine.md" />',
    )

    sections = machine.client.get("/prompts/my-own/sections").json()["sections"]

    assert [section["key"] for section in sections] == ["00-mine"]
    assert "my own section" in preview(machine, "my-own")


def test_local_beats_global_beats_bundled_on_the_page(machine):
    assert "GLOBAL CODER" not in preview(machine, "coder")

    write_agent(machine.global_agents, "coder", "GLOBAL CODER")
    assert "GLOBAL CODER" in preview(machine, "coder")

    write_agent(machine.local_agents, "coder", "LOCAL CODER")
    prompt = preview(machine, "coder")
    assert "LOCAL CODER" in prompt and "GLOBAL CODER" not in prompt


def test_an_unknown_bundle_is_still_not_found(machine):
    assert machine.client.get("/prompts/no-such-agent/sections").status_code == 404
    assert machine.client.get("/prompts/no-such-agent/preview").status_code == 404
