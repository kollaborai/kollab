"""A cancelled terminal call stops the rest of its tool batch."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from kollabor_agent.tool_executor import ToolExecutionResult, ToolExecutor
from kollabor_events import EventBus


@pytest.mark.asyncio
async def test_cancel_running_tool_prevents_later_calls_in_the_same_batch():
    executor = ToolExecutor(mcp_integration=None, event_bus=EventBus())
    first_call_started = asyncio.Event()
    finish_first_call = asyncio.Event()
    executed_ids = []

    async def execute_terminal(tool_data):
        executed_ids.append(tool_data["id"])
        if tool_data["id"] == "call-1":
            first_call_started.set()
            await finish_first_call.wait()
        return ToolExecutionResult(
            tool_id=tool_data["id"], tool_type="terminal", success=True, output="done"
        )

    executor._execute_terminal_command = AsyncMock(side_effect=execute_terminal)
    batch = asyncio.create_task(
        executor.execute_all_tools(
            [
                {"type": "terminal", "id": "call-1", "command": "sleep 3; echo A"},
                {"type": "terminal", "id": "call-2", "command": "echo B"},
            ]
        )
    )

    await first_call_started.wait()
    await executor.cancel_running_tool()
    finish_first_call.set()
    results = await batch

    assert executed_ids == ["call-1"]
    assert results[1].metadata["cancelled"] is True
