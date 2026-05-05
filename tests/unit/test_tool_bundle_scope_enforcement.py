"""Tests for bundle scope enforcement in tool execution."""

import pytest
import asyncio

from kollabor_agent.tool_executor import ToolExecutor, ToolExecutionResult


def _run_async(coro):
    """Run a coroutine, handling event loop teardown from other tests."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("loop closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _make_executor() -> ToolExecutor:
    """Create a minimal ToolExecutor for testing."""
    return ToolExecutor(
        mcp_integration=None,
        event_bus=None,
        terminal_timeout=30,
        mcp_timeout=30,
    )


class TestBundleScopeEnforcement:
    """Tests for bundle scope enforcement in ToolExecutor.execute_tool."""

    def test_no_scope_allows_all_tools(self):
        """When no bundle scope is set, all tools are allowed."""
        executor = _make_executor()
        # No set_bundle_scope called — should not reject
        error = executor._check_bundle_scope("terminal")
        assert error is None
        error = executor._check_bundle_scope("file_read")
        assert error is None
        error = executor._check_bundle_scope("hub_msg")
        assert error is None

    def test_scope_allows_listed_tools(self):
        """When scope is set, only listed tools are allowed."""
        executor = _make_executor()
        executor.set_bundle_scope(["terminal", "file-read", "hub-msg"])

        error = executor._check_bundle_scope("terminal")
        assert error is None

        error = executor._check_bundle_scope("file_read")
        assert error is None  # underscore maps to hyphen

        error = executor._check_bundle_scope("hub_msg")
        assert error is None  # underscore maps to hyphen

    def test_scope_rejects_unlisted_tools(self):
        """Tools not in the scope are rejected with a descriptive error."""
        executor = _make_executor()
        executor.set_bundle_scope(["terminal", "file-read"])

        error = executor._check_bundle_scope("hub_msg")
        assert error is not None
        assert "hub-msg" in error
        assert "terminal" in error
        assert "file-read" in error

    def test_scope_rejects_file_edit_when_read_only(self):
        """A read-only bundle cannot use file_edit."""
        executor = _make_executor()
        executor.set_bundle_scope(["file-read", "file-grep", "terminal"])

        error = executor._check_bundle_scope("file_edit")
        assert error is not None
        assert "file-edit" in error

    def test_scope_rejects_hub_spawn_for_non_coordinator(self):
        """A non-coordinator agent cannot use hub_spawn."""
        executor = _make_executor()
        executor.set_bundle_scope(["hub-msg", "hub-status", "terminal"])

        error = executor._check_bundle_scope("hub_spawn")
        assert error is not None
        assert "hub-spawn" in error

    def test_clear_scope_allows_all(self):
        """After clearing scope, all tools are allowed again."""
        executor = _make_executor()
        executor.set_bundle_scope(["terminal"])
        error = executor._check_bundle_scope("hub_msg")
        assert error is not None

        executor.clear_bundle_scope()
        error = executor._check_bundle_scope("hub_msg")
        assert error is None

    def test_scope_enforcement_toggle(self):
        """When enforcement is disabled, scope is not checked."""
        executor = _make_executor()
        executor.set_bundle_scope(["terminal"])
        executor._enforce_bundle_scope = False

        error = executor._check_bundle_scope("hub_msg")
        assert error is None

    def test_tool_type_to_registry_name(self):
        """Verify underscore-to-hyphen conversion."""
        executor = _make_executor()

        assert executor._tool_type_to_registry_name("file_read") == "file-read"
        assert executor._tool_type_to_registry_name("file_edit") == "file-edit"
        assert executor._tool_type_to_registry_name("hub_msg") == "hub-msg"
        assert executor._tool_type_to_registry_name("terminal") == "terminal"
        assert executor._tool_type_to_registry_name("scratchpad") == "scratchpad"

    def test_execute_tool_rejects_out_of_scope(self):
        """execute_tool returns error for out-of-scope tools."""
        executor = _make_executor()
        executor.set_bundle_scope(["file-read"])

        tool_data = {"id": "test-1", "type": "terminal", "command": "ls"}
        result = _run_async(executor.execute_tool(tool_data))

        assert result.success is False
        assert "terminal" in result.error
        assert result.metadata.get("scope_denied") is True

    def test_execute_tool_allows_in_scope(self):
        """execute_tool passes through for in-scope tools.

        The tool will still fail (no MCP, no event bus) but it should
        NOT be rejected by scope check.
        """
        executor = _make_executor()
        executor.set_bundle_scope(["terminal"])

        tool_data = {"id": "test-1", "type": "terminal", "command": "ls"}
        result = _run_async(executor.execute_tool(tool_data))

        # Should not be scope_denied — it will fail for other reasons
        # (no shell executor configured) but the scope check passed
        assert result.metadata.get("scope_denied") is None

    def test_execute_tool_allows_all_when_no_scope(self):
        """execute_tool allows all tools when no scope is set."""
        executor = _make_executor()

        tool_data = {"id": "test-1", "type": "hub_msg", "to": "lapis", "message": "hi"}
        # This will route to plugin handler (not found) and fail,
        # but should NOT be rejected by scope check
        result = _run_async(executor.execute_tool(tool_data))
        assert result.metadata.get("scope_denied") is None

    def test_empty_scope_blocks_everything(self):
        """An empty scope list blocks all tools."""
        executor = _make_executor()
        executor.set_bundle_scope([])

        error = executor._check_bundle_scope("terminal")
        assert error is not None

        error = executor._check_bundle_scope("file_read")
        assert error is not None


class TestBundleScopeIntegration:
    """Integration tests verifying scope works with registry data."""

    def test_research_bundle_scope(self):
        """Research bundle: read-only tools only."""
        from kollabor_agent.tool_registry import ToolRegistry
        ToolRegistry._instance = None
        from kollabor_agent.tool_definitions import (
            file_ops, terminal, git, hub, scratchpad, wait, context, task
        )

        research_tools = [
            "terminal", "file-read", "file-grep",
            "hub-msg", "hub-status", "hub-agents",
            "scratchpad", "scratchpad-append", "scratchpad-get", "scratchpad-clear",
            "task-checkpoint", "task-complete",
            "wait-for-user",
            "curate", "context-query", "evict",
        ]

        executor = _make_executor()
        executor.set_bundle_scope(research_tools)

        # Should allow read ops
        assert executor._check_bundle_scope("file_read") is None
        assert executor._check_bundle_scope("file_grep") is None
        assert executor._check_bundle_scope("terminal") is None

        # Should deny write ops
        assert executor._check_bundle_scope("file_edit") is not None
        assert executor._check_bundle_scope("file_create") is not None
        assert executor._check_bundle_scope("file_delete") is not None

        # Should deny hub management
        assert executor._check_bundle_scope("hub_spawn") is not None
        assert executor._check_bundle_scope("hub_stop") is not None

    def test_kollabor_bundle_has_full_access(self):
        """Kollab/koordinator bundles have all tools."""
        import json
        import os

        from kollabor_agent.tool_registry import ToolRegistry
        ToolRegistry._instance = None
        from kollabor_agent.tool_definitions import (
            file_ops, terminal, git, hub, scratchpad, wait, context, task
        )

        with open("bundles/agents/kollabor/agent.json") as f:
            data = json.load(f)
        kollabor_tools = data["tools"]

        executor = _make_executor()
        executor.set_bundle_scope(kollabor_tools)

        registry = ToolRegistry.get_global()
        for tool in registry.list():
            # Map registry name to tool_type (hyphen -> underscore)
            tool_type = tool.name.replace("-", "_")
            error = executor._check_bundle_scope(tool_type)
            assert error is None, f"kollabor should have access to {tool.name} ({tool_type})"


class TestInternalTypeBypass:
    """Internal dispatch types should bypass scope checks."""

    def _make_scoped_executor(self):
        executor = ToolExecutor(
            mcp_integration=None,
            event_bus=None,
            terminal_timeout=30,
            mcp_timeout=30,
        )
        executor.set_bundle_scope(["terminal"])  # Only terminal, not sub-types
        return executor

    def test_terminal_status_bypasses_scope(self):
        executor = self._make_scoped_executor()
        assert executor._check_bundle_scope("terminal_status") is None

    def test_terminal_output_bypasses_scope(self):
        executor = self._make_scoped_executor()
        assert executor._check_bundle_scope("terminal_output") is None

    def test_terminal_kill_bypasses_scope(self):
        executor = self._make_scoped_executor()
        assert executor._check_bundle_scope("terminal_kill") is None

    def test_mcp_tool_bypasses_scope(self):
        executor = self._make_scoped_executor()
        assert executor._check_bundle_scope("mcp_tool") is None

    def test_malformed_file_op_bypasses_scope(self):
        executor = self._make_scoped_executor()
        assert executor._check_bundle_scope("malformed_file_op") is None

    def test_malformed_tool_bypasses_scope(self):
        executor = self._make_scoped_executor()
        assert executor._check_bundle_scope("malformed_tool") is None

    def test_unknown_bypasses_scope(self):
        executor = self._make_scoped_executor()
        assert executor._check_bundle_scope("unknown") is None


class TestNativeNameReverseLookup:
    """Verify tool_type -> registry name uses native_name reverse lookup."""

    def test_file_mkdir_maps_to_directory(self):
        """file_mkdir (native) should map to 'directory' (registry name)."""
        from kollabor_agent.tool_registry import ToolRegistry
        ToolRegistry._instance = None
        from kollabor_agent.tool_definitions import (
            file_ops, terminal, git, hub, scratchpad, wait, context, task
        )

        executor = ToolExecutor(
            mcp_integration=None,
            event_bus=None,
            terminal_timeout=30,
            mcp_timeout=30,
        )
        executor.set_bundle_scope(["directory", "terminal"])

        # file_mkdir is the native/dispatch name, directory is the registry name
        assert executor._check_bundle_scope("file_mkdir") is None

    def test_file_rmdir_maps_to_directory_remove(self):
        """file_rmdir (native) should map to 'directory-remove' (registry name)."""
        from kollabor_agent.tool_registry import ToolRegistry
        ToolRegistry._instance = None
        from kollabor_agent.tool_definitions import (
            file_ops, terminal, git, hub, scratchpad, wait, context, task
        )

        executor = ToolExecutor(
            mcp_integration=None,
            event_bus=None,
            terminal_timeout=30,
            mcp_timeout=30,
        )
        executor.set_bundle_scope(["directory-remove"])

        assert executor._check_bundle_scope("file_rmdir") is None

    def test_file_mkdir_blocked_when_not_in_bundle(self):
        """file_mkdir blocked when 'directory' not in bundle tools."""
        from kollabor_agent.tool_registry import ToolRegistry
        ToolRegistry._instance = None
        from kollabor_agent.tool_definitions import (
            file_ops, terminal, git, hub, scratchpad, wait, context, task
        )

        executor = ToolExecutor(
            mcp_integration=None,
            event_bus=None,
            terminal_timeout=30,
            mcp_timeout=30,
        )
        executor.set_bundle_scope(["terminal"])

        error = executor._check_bundle_scope("file_mkdir")
        assert error is not None
        assert "directory" in error


class TestXmlTagReverseLookup:
    """Verify plugin-registered tag names map to registry names via xml_tag."""

    def test_hub_msg_maps_to_hub_msg(self):
        """hub_msg (xml_tag) maps to hub-msg (registry name)."""
        from kollabor_agent.tool_registry import ToolRegistry
        ToolRegistry._instance = None
        from kollabor_agent.tool_definitions import (
            file_ops, terminal, git, hub, scratchpad, wait, context, task
        )

        executor = ToolExecutor(
            mcp_integration=None,
            event_bus=None,
            terminal_timeout=30,
            mcp_timeout=30,
        )
        executor.set_bundle_scope(["hub-msg"])

        assert executor._check_bundle_scope("hub_msg") is None

    def test_scratchpad_append_maps_correctly(self):
        """scratchpad_append (xml_tag) maps to scratchpad-append (registry)."""
        from kollabor_agent.tool_registry import ToolRegistry
        ToolRegistry._instance = None
        from kollabor_agent.tool_definitions import (
            file_ops, terminal, git, hub, scratchpad, wait, context, task
        )

        executor = ToolExecutor(
            mcp_integration=None,
            event_bus=None,
            terminal_timeout=30,
            mcp_timeout=30,
        )
        executor.set_bundle_scope(["scratchpad-append"])

        assert executor._check_bundle_scope("scratchpad_append") is None

    def test_wait_for_user_maps_correctly(self):
        """wait_for_user (xml_tag) maps to wait-for-user (registry)."""
        from kollabor_agent.tool_registry import ToolRegistry
        ToolRegistry._instance = None
        from kollabor_agent.tool_definitions import (
            file_ops, terminal, git, hub, scratchpad, wait, context, task
        )

        executor = ToolExecutor(
            mcp_integration=None,
            event_bus=None,
            terminal_timeout=30,
            mcp_timeout=30,
        )
        executor.set_bundle_scope(["wait-for-user"])

        assert executor._check_bundle_scope("wait_for_user") is None
