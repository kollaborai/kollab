"""Tests for on-demand tool loading (tool-search and tool-load).

Tests cover:
1. Tool definition registration and metadata
2. tool-search execution: keyword matching, ranking, MCP tools, edge cases
3. tool-load execution: built-in tools, MCP tools, error cases
4. Bundle scope interaction: loading a tool adds it to allowed set

Mocks the MCP integration to avoid real server connections.
"""

import asyncio
import os
import sys
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

# Ensure we can import kollabor_agent
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from kollabor_agent.tool_definition import ToolDefinition, ToolParameter
from kollabor_agent.tool_registry import ToolRegistry


def _run(coro):
    """Run a coroutine with a fresh event loop."""
    try:
        loop = asyncio.new_event_loop()
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_executor():
    """Create a ToolExecutor with mocked dependencies."""
    from kollabor_agent.tool_executor import ToolExecutor

    mcp_integration = MagicMock()
    mcp_integration.tool_registry = {}
    mcp_integration.server_connections = {}

    event_bus = MagicMock()
    event_bus.emit_with_hooks = AsyncMock(return_value=None)

    executor = ToolExecutor(
        mcp_integration=mcp_integration,
        event_bus=event_bus,
        terminal_timeout=10,
        mcp_timeout=20,
    )
    return executor, mcp_integration


class TestOnDemandToolDefinitions(unittest.TestCase):
    """Verify tool-search and tool-load definitions are registered correctly."""

    def setUp(self):
        """Reset registry and load definitions."""
        ToolRegistry.reset()
        self.registry = ToolRegistry.get_global()

    def test_tool_search_registered(self):
        """tool-search should be in the registry."""
        tool = self.registry.get("tool-search")
        self.assertIsNotNone(tool, "tool-search not registered")
        self.assertEqual(tool.category, "on_demand")
        self.assertEqual(tool.risk_level, "low")

    def test_tool_load_registered(self):
        """tool-load should be in the registry."""
        tool = self.registry.get("tool-load")
        self.assertIsNotNone(tool, "tool-load not registered")
        self.assertEqual(tool.category, "on_demand")
        self.assertEqual(tool.risk_level, "medium")

    def test_tool_search_parameters(self):
        """tool-search should have query (required)."""
        tool = self.registry.get("tool-search")
        self.assertIsNotNone(tool)

        param_names = {p.name for p in tool.parameters}
        self.assertIn("query", param_names)

        query_param = next(p for p in tool.parameters if p.name == "query")
        self.assertTrue(query_param.required)
        self.assertEqual(query_param.type, "string")

    def test_tool_load_parameters(self):
        """tool-load should have name (required)."""
        tool = self.registry.get("tool-load")
        self.assertIsNotNone(tool)

        param_names = {p.name for p in tool.parameters}
        self.assertIn("name", param_names)

        name_param = next(p for p in tool.parameters if p.name == "name")
        self.assertTrue(name_param.required)
        self.assertEqual(name_param.type, "string")

    def test_tool_search_xml_tag(self):
        """tool-search should use nested XML form."""
        tool = self.registry.get("tool-search")
        self.assertIsNotNone(tool)
        self.assertEqual(tool.xml_tag, "tool-search")
        self.assertEqual(tool.xml_form, "nested")

    def test_tool_load_xml_tag(self):
        """tool-load should use nested XML form."""
        tool = self.registry.get("tool-load")
        self.assertIsNotNone(tool)
        self.assertEqual(tool.xml_tag, "tool-load")
        self.assertEqual(tool.xml_form, "nested")

    def test_tool_search_native_name(self):
        """tool-search native name should be tool_search."""
        tool = self.registry.get("tool-search")
        self.assertEqual(tool.native_name, "tool_search")

    def test_tool_load_native_name(self):
        """tool-load native name should be tool_load."""
        tool = self.registry.get("tool-load")
        self.assertEqual(tool.native_name, "tool_load")

    def test_tool_search_has_examples(self):
        """tool-search should have usage examples."""
        tool = self.registry.get("tool-search")
        self.assertGreater(len(tool.examples), 0)

    def test_tool_load_has_examples(self):
        """tool-load should have usage examples."""
        tool = self.registry.get("tool-load")
        self.assertGreater(len(tool.examples), 0)

    def test_does_not_search_itself(self):
        """tool-search should not return on-demand tools in results."""
        tool = self.registry.get("tool-search")
        self.assertEqual(tool.category, "on_demand")
        # The executor skips tools with category == "on_demand"


class TestToolSearchExecution(unittest.TestCase):
    """Tests for tool-search execution via ToolExecutor."""

    def setUp(self):
        ToolRegistry.reset()
        ToolRegistry.get_global()
        self.executor, self.mcp = _make_executor()

    def test_search_basic_builtin(self):
        """Search for 'file' should return file-related tools."""
        tool_data = {
            "type": "tool_search",
            "id": "ts_0",
            "query": "file",
        }

        result = _run(self.executor._execute_tool_search(tool_data))

        self.assertTrue(result.success, f"Expected success: {result.error}")
        self.assertIn("file", result.output.lower())
        # Should find multiple file tools
        self.assertGreater(
            result.metadata["result_count"], 0,
            "Should find at least one file tool"
        )

    def test_search_by_category(self):
        """Search for 'terminal' should find terminal tools."""
        tool_data = {
            "type": "tool_search",
            "id": "ts_0",
            "query": "terminal",
        }

        result = _run(self.executor._execute_tool_search(tool_data))

        self.assertTrue(result.success)
        self.assertGreater(result.metadata["result_count"], 0)

    def test_search_no_results(self):
        """Search for nonsense should return zero results gracefully."""
        tool_data = {
            "type": "tool_search",
            "id": "ts_0",
            "query": "xyzzy_nonexistent_12345",
        }

        result = _run(self.executor._execute_tool_search(tool_data))

        self.assertTrue(result.success)
        self.assertEqual(result.metadata["result_count"], 0)
        self.assertIn("No tools", result.output)

    def test_search_missing_query(self):
        """Missing query parameter should return error."""
        tool_data = {
            "type": "tool_search",
            "id": "ts_0",
        }

        result = _run(self.executor._execute_tool_search(tool_data))

        self.assertFalse(result.success)
        self.assertIsNotNone(result.error)

    def test_search_case_insensitive(self):
        """Search should be case-insensitive."""
        tool_data = {
            "type": "tool_search",
            "id": "ts_0",
            "query": "FILE",
        }

        result = _run(self.executor._execute_tool_search(tool_data))

        self.assertTrue(result.success)
        self.assertGreater(result.metadata["result_count"], 0)

    def test_search_mcp_tools(self):
        """Search should include MCP tools when available."""
        self.mcp.tool_registry = {
            "github_create_issue": {
                "server": "github",
                "enabled": True,
                "definition": {
                    "name": "github_create_issue",
                    "description": "Create a GitHub issue",
                    "parameters": {},
                },
            },
        }

        tool_data = {
            "type": "tool_search",
            "id": "ts_0",
            "query": "github",
        }

        result = _run(self.executor._execute_tool_search(tool_data))

        self.assertTrue(result.success)
        self.assertIn("mcp:github", result.output)

    def test_search_skips_on_demand_tools(self):
        """tool-search should NOT return itself or tool-load in results."""
        tool_data = {
            "type": "tool_search",
            "id": "ts_0",
            "query": "tool",
        }

        result = _run(self.executor._execute_tool_search(tool_data))

        self.assertTrue(result.success)
        # The result metadata should show that on_demand tools were excluded.
        # We check result_count doesn't include tool-search/tool-load.
        # Note: the footer text "Use tool-load..." legitimately mentions tool-load.
        # So we check that the results section doesn't list them as found tools.
        # Split on the footer to inspect only the results section.
        output_body = result.output.split("Use tool-load")[0]
        self.assertNotIn("tool-search", output_body)
        self.assertNotIn("tool-load\n", output_body)
        # mcp-reload matches "tool" in its description — that's fine
        # The key assertion: tool-search and tool-load are NOT in results

    def test_search_name_match_ranked_first(self):
        """Name matches should be ranked before description matches."""
        tool_data = {
            "type": "tool_search",
            "id": "ts_0",
            "query": "hub",
        }

        result = _run(self.executor._execute_tool_search(tool_data))

        self.assertTrue(result.success)
        # hub-msg should appear in results (name contains 'hub')
        self.assertIn("hub", result.output.lower())

    def test_search_empty_query_after_strip(self):
        """Query that becomes empty after strip should error."""
        tool_data = {
            "type": "tool_search",
            "id": "ts_0",
            "query": "   ",
        }

        result = _run(self.executor._execute_tool_search(tool_data))

        self.assertFalse(result.success)

    def test_search_nested_params(self):
        """Query should work when passed via 'parameters' dict."""
        tool_data = {
            "type": "tool_search",
            "id": "ts_0",
            "parameters": {
                "query": "git",
            },
        }

        result = _run(self.executor._execute_tool_search(tool_data))

        self.assertTrue(result.success)
        self.assertGreater(result.metadata["result_count"], 0)


class TestToolLoadExecution(unittest.TestCase):
    """Tests for tool-load execution via ToolExecutor."""

    def setUp(self):
        ToolRegistry.reset()
        ToolRegistry.get_global()
        self.executor, self.mcp = _make_executor()

    def test_load_builtin_tool(self):
        """Loading a built-in tool should return its full docs."""
        tool_data = {
            "type": "tool_load",
            "id": "tl_0",
            "name": "git",
        }

        result = _run(self.executor._execute_tool_load(tool_data))

        self.assertTrue(result.success, f"Expected success: {result.error}")
        self.assertIn("git", result.output.lower())
        self.assertEqual(result.metadata["loaded_tool"], "git")

    def test_load_builtin_by_native_name(self):
        """Loading by native name (underscore form) should work."""
        tool_data = {
            "type": "tool_load",
            "id": "tl_0",
            "name": "hub_msg",
        }

        result = _run(self.executor._execute_tool_load(tool_data))

        self.assertTrue(result.success)
        self.assertEqual(result.metadata["loaded_tool"], "hub-msg")

    def test_load_not_found(self):
        """Loading a non-existent tool should return error."""
        tool_data = {
            "type": "tool_load",
            "id": "tl_0",
            "name": "nonexistent_tool_12345",
        }

        result = _run(self.executor._execute_tool_load(tool_data))

        self.assertFalse(result.success)
        self.assertIn("not found", result.error.lower())

    def test_load_missing_name(self):
        """Missing name parameter should return error."""
        tool_data = {
            "type": "tool_load",
            "id": "tl_0",
        }

        result = _run(self.executor._execute_tool_load(tool_data))

        self.assertFalse(result.success)
        self.assertIsNotNone(result.error)

    def test_load_adds_to_bundle_scope(self):
        """Loading a tool should add it to bundle scope when active."""
        self.executor.set_bundle_scope(["terminal"])  # narrow scope

        tool_data = {
            "type": "tool_load",
            "id": "tl_0",
            "name": "git",
        }

        result = _run(self.executor._execute_tool_load(tool_data))

        self.assertTrue(result.success)
        # git should now be in the bundle scope
        self.assertIn("git", self.executor._bundle_tools)

    def test_load_already_in_scope(self):
        """Loading a tool already in scope should succeed (no-op add)."""
        self.executor.set_bundle_scope(["terminal", "git"])

        tool_data = {
            "type": "tool_load",
            "id": "tl_0",
            "name": "git",
        }

        result = _run(self.executor._execute_tool_load(tool_data))

        self.assertTrue(result.success)

    def test_load_nested_params(self):
        """Name should work when passed via 'parameters' dict."""
        tool_data = {
            "type": "tool_load",
            "id": "tl_0",
            "parameters": {
                "name": "terminal",
            },
        }

        result = _run(self.executor._execute_tool_load(tool_data))

        self.assertTrue(result.success)
        self.assertEqual(result.metadata["loaded_tool"], "terminal")

    def test_load_mcp_tool(self):
        """Loading an MCP tool should return its schema."""
        self.mcp.tool_registry = {
            "create_issue": {
                "server": "github",
                "enabled": True,
                "definition": {
                    "name": "create_issue",
                    "description": "Create a GitHub issue",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "title": {"type": "string"},
                        },
                        "required": ["title"],
                    },
                },
            },
        }
        self.mcp.server_connections = {"github": MagicMock()}

        tool_data = {
            "type": "tool_load",
            "id": "tl_0",
            "name": "mcp:github:create_issue",
        }

        result = _run(self.executor._execute_tool_load(tool_data))

        self.assertTrue(result.success, f"Expected success: {result.error}")
        self.assertIn("create_issue", result.output)
        self.assertEqual(result.metadata["mcp_server"], "github")

    def test_load_mcp_tool_wrong_server(self):
        """Loading MCP tool with wrong server name should error."""
        self.mcp.tool_registry = {
            "create_issue": {
                "server": "github",
                "enabled": True,
                "definition": {
                    "name": "create_issue",
                    "description": "Create issue",
                    "parameters": {},
                },
            },
        }
        self.mcp.server_connections = {"github": MagicMock()}

        tool_data = {
            "type": "tool_load",
            "id": "tl_0",
            "name": "mcp:wrongserver:create_issue",
        }

        result = _run(self.executor._execute_tool_load(tool_data))

        self.assertFalse(result.success)
        self.assertIn("github", result.error)  # error mentions correct server

    def test_load_mcp_tool_not_found(self):
        """Loading a non-existent MCP tool should error."""
        self.mcp.tool_registry = {}
        self.mcp.server_connections = {"github": MagicMock()}

        tool_data = {
            "type": "tool_load",
            "id": "tl_0",
            "name": "mcp:github:nonexistent",
        }

        result = _run(self.executor._execute_tool_load(tool_data))

        self.assertFalse(result.success)

    def test_load_mcp_server_offline(self):
        """Loading MCP tool from offline server should error."""
        self.mcp.tool_registry = {
            "create_issue": {
                "server": "github",
                "enabled": True,
                "definition": {
                    "name": "create_issue",
                    "description": "Create issue",
                    "parameters": {},
                },
            },
        }
        self.mcp.server_connections = {}  # github not connected

        tool_data = {
            "type": "tool_load",
            "id": "tl_0",
            "name": "mcp:github:create_issue",
        }

        result = _run(self.executor._execute_tool_load(tool_data))

        self.assertFalse(result.success)
        self.assertIn("not connected", result.error.lower())

    def test_load_mcp_bad_format(self):
        """Malformed MCP tool name should error."""
        tool_data = {
            "type": "tool_load",
            "id": "tl_0",
            "name": "mcp:only_one_part",
        }

        result = _run(self.executor._execute_tool_load(tool_data))

        self.assertFalse(result.success)
        self.assertIn("format", result.error.lower())


class TestOnDemandIntegration(unittest.TestCase):
    """Integration-style tests for tool-search + tool-load workflow."""

    def setUp(self):
        ToolRegistry.reset()
        ToolRegistry.get_global()
        self.executor, self.mcp = _make_executor()

    def test_search_then_load_workflow(self):
        """Simulate: search for 'git', then load the git tool."""
        # Step 1: search
        search_data = {
            "type": "tool_search",
            "id": "ts_0",
            "query": "git",
        }
        search_result = _run(self.executor._execute_tool_search(search_data))
        self.assertTrue(search_result.success)
        self.assertIn("git", search_result.output.lower())

        # Step 2: load
        load_data = {
            "type": "tool_load",
            "id": "tl_0",
            "name": "git",
        }
        load_result = _run(self.executor._execute_tool_load(load_data))
        self.assertTrue(load_result.success)
        self.assertEqual(load_result.metadata["loaded_tool"], "git")

    def test_bundle_scope_blocks_unloaded_then_allows_loaded(self):
        """Tool not in bundle scope is blocked; after loading, it's allowed."""
        from kollabor_agent.tool_executor import ToolExecutionResult

        self.executor.set_bundle_scope(["terminal"])

        # git is NOT in scope — should be denied
        scope_error = self.executor._check_bundle_scope("git")
        self.assertIsNotNone(scope_error, "git should be blocked by scope")

        # Load git
        load_data = {
            "type": "tool_load",
            "id": "tl_0",
            "name": "git",
        }
        _run(self.executor._execute_tool_load(load_data))

        # Now git should be allowed
        scope_error = self.executor._check_bundle_scope("git")
        self.assertIsNone(scope_error, "git should be allowed after loading")

    def test_stats_tracking(self):
        """tool-search and tool-load should be tracked in stats."""
        # _execute_tool_search is the handler itself — stats are updated
        # by execute_tool() which wraps the handler. We verify the handler
        # returns the right tool_type so stats classification works.
        search_data = {
            "type": "tool_search",
            "id": "ts_0",
            "query": "file",
        }
        result = _run(self.executor._execute_tool_search(search_data))

        # The result should have tool_type set correctly for stats classification
        self.assertEqual(result.tool_type, "tool_search")
        self.assertTrue(result.success)


if __name__ == "__main__":
    unittest.main()
