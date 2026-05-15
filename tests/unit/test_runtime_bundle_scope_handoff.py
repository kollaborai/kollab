"""Regression tests for agent bundle tool scope handoff."""

from pathlib import Path
from unittest.mock import MagicMock

from kollabor_agent.agent_manager import Agent
from kollabor_agent.mcp_integration import MCPIntegration
from kollabor_agent.runtime import AgentRuntime
from kollabor_ai.system_prompt_builder import SystemPromptBuilder


def _research_runtime() -> AgentRuntime:
    agent = Agent.from_directory(Path("bundles/agents/research"))
    assert agent is not None
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


def test_runtime_preserves_agent_json_tools_for_scope_handoff():
    runtime = _research_runtime()

    assert "file-read" in runtime.tools
    assert "file-edit" not in runtime.tools
    assert runtime.config["tools"] == runtime.tools

    roundtrip = AgentRuntime.from_dict(runtime.to_dict())
    assert roundtrip.tools == runtime.tools


def test_prompt_registry_uses_runtime_bundle_tools():
    runtime = _research_runtime()
    builder = SystemPromptBuilder(
        config=_registry_enabled_config(),
        agent_manager=_agent_manager_with(runtime),
    )

    tool_reference = builder._get_registry_tool_reference()

    assert tool_reference is not None
    assert "<read>" in tool_reference
    assert "<edit>" not in tool_reference
    assert "<hub_spawn>" not in tool_reference


def test_native_tool_schemas_use_runtime_bundle_tools():
    runtime = _research_runtime()
    mcp = MCPIntegration.__new__(MCPIntegration)
    mcp.config = _registry_enabled_config()
    mcp._agent_manager = _agent_manager_with(runtime)

    tools = mcp._get_registry_tools()
    names = {tool["name"] for tool in tools or []}

    assert "file_read" in names
    assert "file_edit" not in names
    assert "hub_spawn" not in names
