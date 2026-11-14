import logging

from kollabor_agent.runtime import get_agent_tool_scope
from kollabor_agent.tool_registry import ToolDefinition, ToolRegistry


def test_registry_wildcard_mixed_with_names_returns_all_registered_tools():
    registry = ToolRegistry()
    registry.register(ToolDefinition(name="file-read", description="Read files"))
    registry.register(ToolDefinition(name="terminal", description="Run commands"))

    tools = registry.get_for_bundle(["*", "file-read"])

    assert {tool.name for tool in tools} == {"file-read", "terminal"}


def test_agent_tool_scope_mixed_wildcard_warns_and_allows_all(caplog):
    class Agent:
        tools = ["*", "file-read"]

    with caplog.at_level(logging.WARNING, logger="kollabor_agent.runtime"):
        scope = get_agent_tool_scope(Agent())

    assert scope is None
    assert "redundant tool names: file-read" in caplog.text


def test_agent_config_mixed_wildcard_warns_and_allows_all(caplog):
    class Config:
        @staticmethod
        def get(key):
            return ["*", "file-read"] if key == "tools" else None

    class Agent:
        tools = None
        config = Config()

    with caplog.at_level(logging.WARNING, logger="kollabor_agent.runtime"):
        scope = get_agent_tool_scope(Agent())

    assert scope is None
    assert "redundant tool names: file-read" in caplog.text
