"""Regression tests for agent bundle tool scope handoff."""

import json
from pathlib import Path
from unittest.mock import MagicMock

from kollabor_agent.agent_manager import Agent
from kollabor_agent.mcp_integration import MCPIntegration
from kollabor_agent.runtime import AgentRuntime
from kollabor_ai.system_prompt_builder import SystemPromptBuilder

_EXPLICIT_COMMUNITY_AGENTS = frozenset(
    {"community", "maintainer", "pr-shepherd", "release-planner", "triager"}
)
_EXPLICIT_RESEARCH_TOOLS = [
    "terminal",
    "file-read",
    "file-grep",
    "hub-msg",
    "hub-status",
    "hub-agents",
    "scratchpad",
    "scratchpad-append",
    "scratchpad-get",
    "scratchpad-clear",
    "task-checkpoint",
    "task-complete",
    "curate",
    "context-query",
    "evict",
    "compact",
]


def _restricted_research_runtime() -> AgentRuntime:
    """Build a restricted runtime to keep explicit-scope coverage independent."""
    agent = Agent(
        name="research",
        directory=Path("bundles/agents/research"),
        system_prompt="",
        tools=list(_EXPLICIT_RESEARCH_TOOLS),
    )
    return AgentRuntime.from_agent(agent)


def _registry_enabled_config():
    config = MagicMock()
    config.get = lambda key, default=None: (
        True if key == "kollabor.tool_registry.use_registry" else default
    )
    return config


def _agent_manager_with(active_agent):
    agent_manager = MagicMock()
    agent_manager.get_active_agent.return_value = active_agent
    return agent_manager


def test_runtime_preserves_explicit_tools_for_scope_handoff():
    runtime = _restricted_research_runtime()

    assert "file-read" in runtime.tools
    assert "file-edit" not in runtime.tools
    assert runtime.config["tools"] == runtime.tools

    roundtrip = AgentRuntime.from_dict(runtime.to_dict())
    assert roundtrip.tools == runtime.tools


def test_prompt_registry_uses_runtime_bundle_tools():
    runtime = _restricted_research_runtime()
    builder = SystemPromptBuilder(
        config=_registry_enabled_config(),
        agent_manager=_agent_manager_with(runtime),
    )

    tool_reference = builder._get_registry_tool_reference()

    assert tool_reference is not None
    assert "<read>" in tool_reference
    assert "<edit>" not in tool_reference
    assert "<hub_spawn>" not in tool_reference


def test_prompt_registry_skips_reference_for_native_tool_provider():
    profile = MagicMock()
    profile.get_supports_tools.return_value = True
    profile_manager = MagicMock(spec=["get_active_profile"])
    profile_manager.get_active_profile.return_value = profile
    builder = SystemPromptBuilder(
        config=_registry_enabled_config(),
        profile_manager=profile_manager,
    )

    assert builder._get_registry_tool_reference() is None


def test_prompt_registry_keeps_reference_for_xml_tool_provider():
    profile = MagicMock()
    profile.get_supports_tools.return_value = False
    profile_manager = MagicMock(spec=["get_active_profile"])
    profile_manager.get_active_profile.return_value = profile
    builder = SystemPromptBuilder(
        config=_registry_enabled_config(),
        profile_manager=profile_manager,
    )

    tool_reference = builder._get_registry_tool_reference()

    assert tool_reference is not None
    assert "# Tool Reference" in tool_reference


def test_prompt_registry_logs_missing_active_profile_method(caplog):
    caplog.set_level("DEBUG")
    builder = SystemPromptBuilder(
        config=_registry_enabled_config(),
        profile_manager=MagicMock(spec=[]),
    )

    builder._get_registry_tool_reference()

    assert "Unable to check active profile tool support" in caplog.text


def test_native_tool_schemas_use_runtime_bundle_tools():
    runtime = _restricted_research_runtime()
    mcp = MCPIntegration.__new__(MCPIntegration)
    mcp.config = _registry_enabled_config()
    mcp._agent_manager = _agent_manager_with(runtime)

    tools = mcp._get_registry_tools()
    names = {tool["name"] for tool in tools or []}

    assert "file_read" in names
    assert "file_edit" not in names
    assert "hub_spawn" not in names


def test_explicit_bundle_scope_hides_external_mcp_tools():
    runtime = _restricted_research_runtime()
    mcp = MCPIntegration.__new__(MCPIntegration)
    mcp.event_bus = MagicMock(config={})
    mcp.tool_registry = {
        "repo_search": {
            "server": "github",
            "enabled": True,
            "definition": {"description": "Search a repository"},
        }
    }
    mcp._agent_manager = _agent_manager_with(runtime)
    mcp.config = _registry_enabled_config()

    tools = mcp.get_tool_definitions_for_api()
    names = {tool["name"] for tool in tools}

    assert "repo_search" not in names


def test_research_profile_is_explicitly_read_only_and_others_are_wildcard_scoped():
    profiles = sorted(Path("bundles/agents").glob("*/agent.json"))

    assert profiles
    for path in profiles:
        config = json.loads(path.read_text(encoding="utf-8"))
        if path.parent.name == "research":
            assert config["tools"] == _EXPLICIT_RESEARCH_TOOLS, path
        elif path.parent.name in _EXPLICIT_COMMUNITY_AGENTS:
            tools = config["tools"]
            assert tools != ["*"], path
            assert not {"git", "file-edit", "file-delete", "hub-spawn"} & set(tools), path
        else:
            assert config["tools"] == ["*"], path


def test_wildcard_runtime_reaches_prompt_and_native_schema_for_coder():
    agent = Agent.from_directory(Path("bundles/agents/coder"))
    assert agent is not None
    runtime = AgentRuntime.from_agent(agent)

    builder = SystemPromptBuilder(
        config=_registry_enabled_config(),
        agent_manager=_agent_manager_with(runtime),
    )
    tool_reference = builder._get_registry_tool_reference()
    assert "<edit>" in tool_reference
    assert "<hub_spawn>" in tool_reference

    mcp = MCPIntegration.__new__(MCPIntegration)
    mcp.config = _registry_enabled_config()
    mcp._agent_manager = _agent_manager_with(runtime)
    names = {tool["name"] for tool in mcp._get_registry_tools() or []}
    assert "file_edit" in names
    assert "hub_spawn" in names
    assert "terminal" in names
    assert "git" not in names
