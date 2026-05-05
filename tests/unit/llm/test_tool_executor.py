"""Tests for ToolExecutor component.

Tests terminal command execution, MCP tool calls,
and result formatting functionality.
"""

import asyncio
import os
import sys
import unittest
from dataclasses import dataclass
from typing import Optional
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kollabor_agent.tool_executor import ToolExecutor


# Mock result classes for testing
@dataclass
class MockToolResult:
    """Mock tool execution result."""

    tool_type: str
    tool_id: str
    success: bool
    output: Optional[str] = None
    error: Optional[str] = None
    execution_time: float = 0.0


class TestToolExecutor(unittest.TestCase):
    """Test tool execution functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.mcp_integration = MagicMock()
        self.event_bus = MagicMock()
        self.terminal_timeout = 10
        self.mcp_timeout = 20

        self.executor = ToolExecutor(
            mcp_integration=self.mcp_integration,
            event_bus=self.event_bus,
            terminal_timeout=self.terminal_timeout,
            mcp_timeout=self.mcp_timeout,
        )

    async def test_execute_terminal_command_success(self):
        """Test successful terminal command execution."""
        tool_data = {
            "type": "terminal",
            "id": "terminal_0",
            "command": "echo 'Hello World'",
            "raw": "<terminal>echo 'Hello World'</terminal>",
        }

        with patch("asyncio.create_subprocess_shell") as mock_subprocess:
            # Mock successful process
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (b"Hello World\n", b"")
            mock_process.returncode = 0
            mock_subprocess.return_value = mock_process

            result = await self.executor._execute_terminal_command(tool_data)

            self.assertTrue(result.success)
            self.assertEqual(result.tool_type, "terminal")
            self.assertEqual(result.tool_id, "terminal_0")
            self.assertEqual(result.output.strip(), "Hello World")
            self.assertIsNone(result.error)
            self.assertGreater(result.execution_time, 0)

    async def test_execute_terminal_command_failure(self):
        """Test failed terminal command execution."""
        tool_data = {
            "type": "terminal",
            "id": "terminal_0",
            "command": "nonexistent_command",
            "raw": "<terminal>nonexistent_command</terminal>",
        }

        with patch("asyncio.create_subprocess_shell") as mock_subprocess:
            # Mock failed process
            mock_process = AsyncMock()
            mock_process.communicate.return_value = (b"", b"command not found")
            mock_process.returncode = 1
            mock_subprocess.return_value = mock_process

            result = await self.executor._execute_terminal_command(tool_data)

            self.assertFalse(result.success)
            self.assertEqual(result.tool_type, "terminal")
            self.assertIn("command not found", result.error)

    async def test_execute_terminal_command_timeout(self):
        """Test terminal command timeout handling."""
        tool_data = {
            "type": "terminal",
            "id": "terminal_0",
            "command": "sleep 20",  # Longer than timeout
            "raw": "<terminal>sleep 20</terminal>",
        }

        with patch("asyncio.create_subprocess_shell") as mock_subprocess:
            mock_process = AsyncMock()
            # Simulate timeout
            mock_process.communicate.side_effect = asyncio.TimeoutError()
            mock_subprocess.return_value = mock_process

            result = await self.executor._execute_terminal_command(tool_data)

            self.assertFalse(result.success)
            self.assertIn("timeout", result.error.lower())

    async def test_execute_mcp_tool_success(self):
        """Test successful MCP tool execution."""
        tool_data = {
            "type": "mcp_tool",
            "id": "mcp_tool_0",
            "name": "file_reader",
            "arguments": {"path": "/test/file.txt"},
            "content": "Read file content",
            "raw": '<tool name="file_reader" path="/test/file.txt">Read file content</tool>',
        }

        # Mock MCP integration
        self.mcp_integration.call_tool.return_value = {
            "success": True,
            "result": "File content here",
            "error": None,
        }

        result = await self.executor._execute_mcp_tool(tool_data)

        self.assertTrue(result.success)
        self.assertEqual(result.tool_type, "mcp_tool")
        self.assertEqual(result.tool_id, "mcp_tool_0")
        self.assertEqual(result.output, "File content here")

        # Verify MCP call
        self.mcp_integration.call_tool.assert_called_once_with(
            "file_reader", {"path": "/test/file.txt"}
        )

    async def test_execute_mcp_tool_failure(self):
        """Test failed MCP tool execution."""
        tool_data = {
            "type": "mcp_tool",
            "id": "mcp_tool_0",
            "name": "bad_tool",
            "arguments": {},
            "content": "",
            "raw": '<tool name="bad_tool"></tool>',
        }

        # Mock MCP integration failure
        self.mcp_integration.call_tool.return_value = {
            "success": False,
            "result": None,
            "error": "Tool not found",
        }

        result = await self.executor._execute_mcp_tool(tool_data)

        self.assertFalse(result.success)
        self.assertEqual(result.error, "Tool not found")

    async def test_execute_all_tools_mixed(self):
        """Test executing mixed terminal and MCP tools."""
        tools = [
            {
                "type": "terminal",
                "id": "terminal_0",
                "command": "ls",
                "raw": "<terminal>ls</terminal>",
            },
            {
                "type": "mcp_tool",
                "id": "mcp_tool_0",
                "name": "search",
                "arguments": {"query": "test"},
                "content": "",
                "raw": '<tool name="search" query="test"></tool>',
            },
        ]

        with (
            patch.object(
                self.executor, "_execute_terminal_command", new=AsyncMock()
            ) as mock_terminal,
            patch.object(
                self.executor, "_execute_mcp_tool", new=AsyncMock()
            ) as mock_mcp,
        ):

            # Mock return values
            mock_terminal.return_value = MockToolResult(
                "terminal", "terminal_0", True, "file1.txt\nfile2.txt"
            )
            mock_mcp.return_value = MockToolResult(
                "mcp_tool", "mcp_tool_0", True, "Search results"
            )

            results = await self.executor.execute_all_tools(tools)

            self.assertEqual(len(results), 2)
            self.assertEqual(results[0].tool_type, "terminal")
            self.assertEqual(results[1].tool_type, "mcp_tool")

            mock_terminal.assert_called_once()
            mock_mcp.assert_called_once()

    def test_format_result_for_conversation(self):
        """Test formatting tool results for conversation context."""
        # Terminal result
        terminal_result = MockToolResult(
            "terminal",
            "terminal_0",
            True,
            "total 8\ndrwxr-xr-x 2 user user 4096 Jan 1 12:00 test",
        )

        formatted = self.executor.format_result_for_conversation(terminal_result)

        self.assertIn("[terminal]", formatted)
        self.assertIn("total 8", formatted)

        # MCP tool result
        mcp_result = MockToolResult(
            "mcp_tool", "mcp_tool_0", True, "Found 3 matching files"
        )

        formatted = self.executor.format_result_for_conversation(mcp_result)

        self.assertIn("[mcp_tool]", formatted)
        self.assertIn("Found 3 matching files", formatted)

        # Error result
        error_result = MockToolResult(
            "terminal", "terminal_0", False, None, "Permission denied"
        )

        formatted = self.executor.format_result_for_conversation(error_result)

        self.assertIn("ERROR:", formatted)
        self.assertIn("Permission denied", formatted)

    def test_get_execution_stats(self):
        """Test execution statistics tracking."""
        # Simulate some executions
        self.executor.stats = {
            "total_executions": 10,
            "terminal_executions": 6,
            "mcp_executions": 4,
            "successful_executions": 8,
            "failed_executions": 2,
            "total_execution_time": 15.5,
        }

        stats = self.executor.get_execution_stats()

        self.assertEqual(stats["total_executions"], 10)
        self.assertEqual(stats["terminal_executions"], 6)
        self.assertEqual(stats["mcp_executions"], 4)
        self.assertEqual(stats["successful_executions"], 8)
        self.assertEqual(stats["failed_executions"], 2)
        self.assertEqual(stats["success_rate"], 0.8)  # 8/10
        self.assertEqual(stats["average_time"], 1.55)  # 15.5/10

    async def test_tool_execution_with_cancellation(self):
        """Test tool execution cancellation via event bus."""
        tool_data = {
            "type": "terminal",
            "id": "terminal_0",
            "command": "sleep 10",
            "raw": "<terminal>sleep 10</terminal>",
        }

        with patch("asyncio.create_subprocess_shell") as mock_subprocess:
            mock_process = AsyncMock()
            mock_process.communicate = AsyncMock()

            # Simulate cancellation during execution
            async def mock_communicate():
                await asyncio.sleep(0.1)  # Brief delay
                raise asyncio.CancelledError("Execution cancelled")

            mock_process.communicate.side_effect = mock_communicate
            mock_subprocess.return_value = mock_process

            result = await self.executor._execute_terminal_command(tool_data)

            # Should handle cancellation gracefully
            self.assertFalse(result.success)
            self.assertIn("cancelled", result.error.lower())

    def test_result_summary_formatting(self):
        """Test result summary generation for display."""
        # Test various result types
        results = [
            MockToolResult("terminal", "term1", True, "output1"),
            MockToolResult("mcp_tool", "mcp1", True, "output2"),
            MockToolResult("terminal", "term2", False, None, "error1"),
        ]

        summaries = []
        for result in results:
            if result.success:
                summaries.append(f"✓ {result.tool_type} ({result.tool_id})")
            else:
                summaries.append(
                    f"✗ {result.tool_type} ({result.tool_id}): {result.error}"
                )

        self.assertEqual(len(summaries), 3)
        self.assertIn("✓ terminal (term1)", summaries[0])
        self.assertIn("✓ mcp_tool (mcp1)", summaries[1])
        self.assertIn("✗ terminal (term2): error1", summaries[2])

    async def test_concurrent_tool_execution(self):
        """Test concurrent execution of multiple tools."""
        tools = [
            {"type": "terminal", "id": f"terminal_{i}", "command": f"echo {i}"}
            for i in range(5)
        ]

        with patch.object(self.executor, "_execute_terminal_command") as mock_exec:
            # Mock delayed execution
            async def mock_execution(tool_data):
                await asyncio.sleep(0.1)  # Simulate execution time
                tool_id = tool_data["id"]
                return MockToolResult("terminal", tool_id, True, f"output_{tool_id}")

            mock_exec.side_effect = mock_execution

            start_time = asyncio.get_event_loop().time()
            results = await self.executor.execute_all_tools(tools)
            end_time = asyncio.get_event_loop().time()

            # Should complete in roughly 0.1 seconds (concurrent) not 0.5 seconds (sequential)
            execution_time = end_time - start_time
            self.assertLess(execution_time, 0.3)  # Allow some margin

            self.assertEqual(len(results), 5)
            for i, result in enumerate(results):
                self.assertTrue(result.success)
                self.assertEqual(result.tool_id, f"terminal_{i}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
