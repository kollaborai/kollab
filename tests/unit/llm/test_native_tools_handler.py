"""Tests for native tool-call routing in the TUI LLM path."""

from types import SimpleNamespace

import pytest

from kollabor_agent.native_tools_handler import NativeToolsHandler


class FakeApiService:
    def __init__(self, tool_calls):
        self._tool_calls = tool_calls

    def get_last_tool_calls(self):
        return self._tool_calls


class FakeProfileManager:
    def get_active_profile(self):
        return SimpleNamespace(name="test", get_supports_tools=lambda: True)


class FakeConfig:
    def get(self, key, default=None):
        return default


class CapturingToolExecutor:
    def __init__(self):
        self.calls = []
        self.plugin_handlers = {"state_update": object()}

    async def execute_tool(self, tool_data):
        self.calls.append(tool_data)
        return SimpleNamespace(success=True, output="ok", error="")


@pytest.mark.asyncio
async def test_plugin_native_tool_does_not_fall_back_to_mcp():
    api_service = FakeApiService(
        [
            SimpleNamespace(
                id="call_state",
                name="state_update",
                input={"state": "done"},
            )
        ]
    )
    handler = NativeToolsHandler(
        mcp_integration=SimpleNamespace(tool_registry={}),
        profile_manager=FakeProfileManager(),
        api_service=api_service,
        config=FakeConfig(),
    )
    executor = CapturingToolExecutor()

    await handler.execute_tool_calls(executor)

    assert executor.calls
    tool_call = executor.calls[0]
    assert tool_call["type"] == "state_update"
    assert tool_call["name"] == "state_update"
    assert tool_call["state"] == "done"
    assert tool_call["arguments"] == {"state": "done"}


@pytest.mark.asyncio
async def test_registered_mcp_native_tool_remains_mcp_tool():
    api_service = FakeApiService(
        [
            SimpleNamespace(
                id="call_browser",
                name="browser_get_page",
                input={"tab": "active"},
            )
        ]
    )
    handler = NativeToolsHandler(
        mcp_integration=SimpleNamespace(
            tool_registry={"browser_get_page": {"server": "browser"}}
        ),
        profile_manager=FakeProfileManager(),
        api_service=api_service,
        config=FakeConfig(),
    )
    executor = CapturingToolExecutor()

    await handler.execute_tool_calls(executor)

    assert executor.calls
    tool_call = executor.calls[0]
    assert tool_call["type"] == "mcp_tool"
    assert tool_call["name"] == "browser_get_page"
    assert tool_call["arguments"] == {"tab": "active"}
