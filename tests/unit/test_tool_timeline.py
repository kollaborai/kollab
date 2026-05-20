"""Golden tests for tool timeline event contract."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from kollabor_agent.mcp_integration import MCPIntegration, MCPRequestTimeoutError
from kollabor_agent.native_tools_handler import NativeToolsHandler
from kollabor_agent.tool_executor import ToolExecutionResult, ToolExecutor
from kollabor_agent.tool_timeline import ToolTimeline, ToolTimelineEvent
from kollabor_tui.message_display_service import MessageDisplayService


def test_tool_timeline_event_shape_is_stable():
    event = ToolTimelineEvent(
        phase="started",
        tool_id="tool-1",
        tool_type="mcp_tool",
        detail="doctor_ping",
        success=None,
        timestamp=123.4,
        metadata={"server": "doctor-mock"},
    )

    assert event.to_dict() == {
        "phase": "started",
        "tool_id": "tool-1",
        "tool_type": "mcp_tool",
        "detail": "doctor_ping",
        "timestamp": 123.4,
        "metadata": {"server": "doctor-mock"},
    }


def test_tool_timeline_records_replayable_sequence():
    timeline = ToolTimeline()

    timeline.record_phase(
        "registered",
        tool_id="doctor_ping",
        tool_type="mcp_tool",
        detail="doctor-mock",
    )
    timeline.record_phase(
        "started",
        tool_id="call-1",
        tool_type="mcp_tool",
        detail="doctor_ping",
    )
    timeline.record_phase(
        "result",
        tool_id="call-1",
        tool_type="mcp_tool",
        detail="ok",
        success=True,
    )

    events = timeline.to_dicts()
    assert [event["phase"] for event in events] == [
        "registered",
        "started",
        "result",
    ]
    assert events[-1]["success"] is True


@pytest.mark.asyncio
async def test_tool_executor_records_xml_timeline_for_permission_and_result():
    event_bus = MagicMock()
    event_bus.emit_with_hooks = AsyncMock(return_value={})
    executor = ToolExecutor(
        mcp_integration=MagicMock(),
        event_bus=event_bus,
    )
    tool_data = {
        "type": "terminal",
        "id": "terminal_0",
        "command": "printf hello",
    }

    with patch.object(
        executor,
        "_execute_terminal_command",
        new=AsyncMock(
            return_value=ToolExecutionResult(
                tool_id="terminal_0",
                tool_type="terminal",
                success=True,
                output="hello\n",
            )
        ),
    ):
        result = await executor.execute_tool(tool_data)

    phases = [event["phase"] for event in result.metadata["timeline"]]
    assert phases[:3] == [
        "started",
        "permission_requested",
        "permission_granted",
    ]
    assert "stdout" in phases
    assert phases[-1] == "result"
    assert result.to_dict()["metadata"]["timeline"] == result.metadata["timeline"]


@pytest.mark.asyncio
async def test_native_tool_execution_returns_timeline_from_executor():
    api_service = MagicMock()
    api_service.get_last_tool_calls.return_value = [
        SimpleNamespace(
            id="call_native_1",
            name="file_read",
            input={"file": "README.md", "limit": 2},
            type="function",
        )
    ]
    mcp_integration = MagicMock()
    mcp_integration.tool_registry = {}
    handler = NativeToolsHandler(
        mcp_integration=mcp_integration,
        profile_manager=MagicMock(),
        api_service=api_service,
        config=MagicMock(),
    )
    event_bus = MagicMock()
    event_bus.emit_with_hooks = AsyncMock(return_value={})
    executor = ToolExecutor(
        mcp_integration=mcp_integration,
        event_bus=event_bus,
    )

    with patch.object(
        executor,
        "_execute_file_operation",
        new=AsyncMock(
            return_value=ToolExecutionResult(
                tool_id="call_native_1",
                tool_type="file_read",
                success=True,
                output="readme contents",
            )
        ),
    ):
        results = await handler.execute_tool_calls(executor)

    assert len(results) == 1
    assert results[0].metadata["timeline"][0]["phase"] == "started"
    assert results[0].metadata["timeline"][0]["tool_id"] == "call_native_1"
    assert results[0].metadata["timeline"][-1]["phase"] == "result"


@pytest.mark.asyncio
async def test_mcp_timeout_and_reconnect_events_are_returned_with_result():
    event_bus = MagicMock()
    event_bus.emit_with_hooks = AsyncMock(return_value={})
    mcp = MCPIntegration(event_bus=event_bus)
    mcp.tool_registry = {
        "doctor_ping": {"server": "doctor", "enabled": True},
        "doctor_echo": {"server": "doctor", "enabled": True},
    }
    mcp.mcp_servers = {"doctor": {"command": "doctor-mcp"}}

    class TimeoutConnection:
        initialized = True

        async def call_tool(self, tool_name, params, timeout=None):
            raise MCPRequestTimeoutError("MCP request timed out after 1 seconds")

    mcp.server_connections = {"doctor": TimeoutConnection()}
    timeout_result = await mcp.call_mcp_tool(
        "doctor_ping", {}, timeout=1, include_timeline=True
    )

    timeout_phases = [event["phase"] for event in timeout_result["_timeline"]]
    assert "mcp_timeout" in timeout_phases

    class ReconnectedConnection:
        initialized = True

        async def call_tool(self, tool_name, params, timeout=None):
            return {"content": [{"type": "text", "text": "pong"}]}

    async def reconnect(server_name, command):
        mcp.server_connections[server_name] = ReconnectedConnection()
        return [{"name": "doctor_echo"}]

    mcp.server_connections = {"doctor": SimpleNamespace(initialized=False)}
    mcp._connect_and_list_tools = AsyncMock(side_effect=reconnect)
    reconnect_result = await mcp.call_mcp_tool(
        "doctor_echo", {}, timeout=1, include_timeline=True
    )

    reconnect_phases = [event["phase"] for event in reconnect_result["_timeline"]]
    assert "mcp_reconnect" in reconnect_phases
    assert "mcp_reconnected" in reconnect_phases
    assert reconnect_result["content"][0]["text"] == "pong"


def test_message_display_service_renders_timeline_lines_with_tool_output():
    captured = []

    class FakeCoordinator:
        is_displaying = False
        message_queue = []
        terminal_renderer = SimpleNamespace(writing_messages=False)

        def display_message_sequence(self, messages):
            captured.extend(messages)

    renderer = SimpleNamespace(message_coordinator=FakeCoordinator(), pipe_mode=False)
    service = MessageDisplayService(renderer)
    result = ToolExecutionResult(
        tool_id="terminal_0",
        tool_type="terminal",
        success=True,
        output="hello",
        metadata={
            "timeline": [
                {
                    "phase": "started",
                    "tool_id": "terminal_0",
                    "tool_type": "terminal",
                    "detail": "terminal: printf hello",
                },
                {
                    "phase": "permission_granted",
                    "tool_id": "terminal_0",
                    "tool_type": "terminal",
                    "detail": "terminal: printf hello",
                    "success": True,
                },
                {
                    "phase": "result",
                    "tool_id": "terminal_0",
                    "tool_type": "terminal",
                    "detail": "success in 0.01s",
                    "success": True,
                },
            ]
        },
    )

    service.display_tool_results(
        [result],
        [{"type": "terminal", "id": "terminal_0", "command": "printf hello"}],
    )

    assert captured
    message_type, content, _kwargs = captured[0]
    assert message_type == "tool"
    assert "timeline:" in content
    assert "started: terminal: printf hello" in content
    assert "permission granted: terminal: printf hello" in content
    assert "hello" in content


def test_tool_result_conversation_format_includes_timeline_for_replay():
    executor = ToolExecutor(
        mcp_integration=MagicMock(),
        event_bus=MagicMock(),
    )
    result = ToolExecutionResult(
        tool_id="terminal_0",
        tool_type="terminal",
        success=True,
        output="hello",
        metadata={
            "timeline": [
                {
                    "phase": "started",
                    "tool_id": "terminal_0",
                    "tool_type": "terminal",
                    "detail": "terminal: printf hello",
                },
                {
                    "phase": "result",
                    "tool_id": "terminal_0",
                    "tool_type": "terminal",
                    "detail": "success in 0.01s",
                    "success": True,
                },
            ]
        },
    )

    formatted = executor.format_result_for_conversation(result)

    assert "[terminal] hello" in formatted
    assert "Timeline:" in formatted
    assert "started: terminal: printf hello" in formatted
