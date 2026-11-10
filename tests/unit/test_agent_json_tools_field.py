"""Regression tests for issue #893: agent.json "tools" semantics.

- missing "tools" field  -> every tool allowed (scope None)
- "tools": []            -> no tools allowed (scope [])
- agent.json that does not parse, or whose "tools" is not a list of names
  -> the agent does not load (never runs unrestricted)
"""

import json
from pathlib import Path

from kollabor_agent.agent_manager import Agent
from kollabor_agent.runtime import AgentRuntime, get_agent_tool_scope


def _make_agent_dir(root: Path, name: str, agent_json: str | None) -> Path:
    agent_dir = root / name
    agent_dir.mkdir(parents=True)
    (agent_dir / "system_prompt.md").write_text("You are a test agent.", encoding="utf-8")
    if agent_json is not None:
        (agent_dir / "agent.json").write_text(agent_json, encoding="utf-8")
    return agent_dir


def test_missing_tools_field_allows_every_tool(tmp_path):
    agent_dir = _make_agent_dir(tmp_path, "no-tools", json.dumps({"description": "x"}))

    agent = Agent.from_directory(agent_dir, source="local")

    assert agent is not None
    assert get_agent_tool_scope(agent) is None


def test_empty_tools_list_grants_no_tools(tmp_path):
    agent_dir = _make_agent_dir(tmp_path, "empty-tools", json.dumps({"tools": []}))

    agent = Agent.from_directory(agent_dir, source="local")

    assert agent is not None
    assert get_agent_tool_scope(agent) == []


def test_empty_tools_list_survives_runtime_handoff(tmp_path):
    agent_dir = _make_agent_dir(tmp_path, "empty-runtime", json.dumps({"tools": []}))

    runtime = AgentRuntime.from_agent(Agent.from_directory(agent_dir, source="local"))

    assert get_agent_tool_scope(runtime) == []


def test_missing_tools_field_survives_runtime_handoff(tmp_path):
    agent_dir = _make_agent_dir(tmp_path, "missing-runtime", json.dumps({}))

    runtime = AgentRuntime.from_agent(Agent.from_directory(agent_dir, source="local"))

    assert get_agent_tool_scope(runtime) is None


def test_unparsable_agent_json_does_not_load(tmp_path):
    agent_dir = _make_agent_dir(tmp_path, "bad-json", '{"tools": ["file-read"')

    assert Agent.from_directory(agent_dir, source="local") is None


def test_non_list_tools_field_does_not_load(tmp_path):
    agent_dir = _make_agent_dir(tmp_path, "string-tools", json.dumps({"tools": "file-read"}))

    assert Agent.from_directory(agent_dir, source="local") is None
