"""Tests for bundle scope enforcement in tool execution."""

import asyncio

from kollabor_agent.tool_executor import ToolExecutionResult, ToolExecutor
from kollabor_ai.response_parser import ResponseParser


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


class _FakeEventBus:
    async def emit_with_hooks(self, *args, **kwargs):
        return {}


class _FakeMCPIntegration:
    tool_registry = {
        "allowed_tool": {"server": "demo-server", "enabled": True},
    }

    async def call_mcp_tool(self, tool_name, arguments, timeout=None):
        return {"output": f"called {tool_name}"}


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

    def test_git_native_tool_is_allowed_without_granting_terminal(self, tmp_path):
        """A git grant permits native Git inspection without terminal access."""
        import subprocess
        from unittest.mock import AsyncMock, MagicMock

        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        executor = _make_executor()
        executor.workspace = tmp_path
        executor.event_bus = MagicMock()
        executor.event_bus.emit_with_hooks = AsyncMock(return_value=None)
        executor.set_bundle_scope(["file-read", "file-grep", "git"])

        result = _run_async(
            executor.execute_tool(
                {
                    "id": "test-git-native",
                    "type": "terminal",
                    "name": "git",
                    "command": "git status --short",
                    "cwd": str(tmp_path),
                }
            )
        )

        assert result.success, result.error
        assert result.metadata.get("scope_denied") is None
        assert result.tool_type == "terminal_read"

    def test_documented_xml_git_call_is_granted_under_git_only_bundle(self, tmp_path):
        """The documented XML terminal form has no native Git name field."""
        import subprocess
        from unittest.mock import AsyncMock, MagicMock

        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        parser = ResponseParser()
        parsed_response = parser.parse_response(
            "<terminal>git status --short</terminal>"
        )
        tool_data, = parser.get_all_tools(parsed_response)

        assert tool_data["type"] == "terminal"
        assert tool_data.get("name") is None

        executor = _make_executor()
        executor.workspace = tmp_path
        executor.event_bus = MagicMock()
        executor.event_bus.emit_with_hooks = AsyncMock(return_value=None)
        executor.set_bundle_scope(["file-read", "file-grep", "git"])

        result = _run_async(executor.execute_tool(tool_data))

        assert result.success, result.error
        assert result.metadata.get("scope_denied") is None
        assert result.tool_type == "terminal_read"

    def test_git_scope_rejects_terminal_and_shell_composed_commands(self, tmp_path):
        """A git grant does not admit ordinary or shell-composed commands."""
        executor = _make_executor()
        executor.set_bundle_scope(["git"])
        marker = tmp_path / "compound-command-ran"

        ordinary = _run_async(
            executor.execute_tool(
                {"id": "ordinary-terminal", "type": "terminal", "command": "pwd"}
            )
        )
        compound = _run_async(
            executor.execute_tool(
                {
                    "id": "compound-git",
                    "type": "terminal",
                    "name": "git",
                    "command": f"git status --short && touch {marker}",
                }
            )
        )

        assert ordinary.metadata.get("scope_denied") is True
        assert compound.metadata.get("scope_denied") is True
        assert not marker.exists()

    def test_git_scope_cannot_run_repository_hooks(self, tmp_path):
        """A Git-only grant cannot turn a Git commit hook into shell access."""
        import subprocess
        from unittest.mock import AsyncMock, MagicMock

        subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
        subprocess.run(
            ["git", "-C", str(tmp_path), "config", "user.name", "Scope Test"],
            check=True,
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(tmp_path),
                "config",
                "user.email",
                "scope-test@example.invalid",
            ],
            check=True,
        )
        (tmp_path / "tracked.txt").write_text("staged\n")
        subprocess.run(
            ["git", "-C", str(tmp_path), "add", "tracked.txt"], check=True
        )
        marker = tmp_path / "hook-ran"
        hook = tmp_path / ".git" / "hooks" / "pre-commit"
        hook.write_text(f"#!/bin/sh\nprintf ran > '{marker}'\n")
        hook.chmod(0o755)

        executor = _make_executor()
        executor.workspace = tmp_path
        executor.event_bus = MagicMock()
        executor.event_bus.emit_with_hooks = AsyncMock(return_value=None)
        executor.set_bundle_scope(["file-read", "file-grep", "git"])

        result = _run_async(
            executor.execute_tool(
                {
                    "id": "git-commit-hook",
                    "type": "terminal",
                    "name": "git",
                    "command": "git commit -m test",
                    "cwd": str(tmp_path),
                }
            )
        )

        assert result.success is False
        assert result.metadata.get("scope_denied") is None
        assert "read-only" in result.error.lower()
        assert not marker.exists()

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

        research_tools = [
            "terminal", "file-read", "file-grep",
            "hub-msg", "hub-status", "hub-agents",
            "scratchpad", "scratchpad-append", "scratchpad-get", "scratchpad-clear",
            "task-checkpoint", "task-complete",
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

        from kollabor_agent.tool_registry import ToolRegistry
        ToolRegistry._instance = None

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
        executor.set_bundle_scope(["file-read"])  # No terminal access
        return executor

    def test_terminal_status_requires_terminal_scope(self):
        executor = self._make_scoped_executor()
        error = executor._check_bundle_scope("terminal_status")
        assert error is not None
        assert "terminal" in error

    def test_terminal_output_requires_terminal_scope(self):
        executor = self._make_scoped_executor()
        error = executor._check_bundle_scope("terminal_output")
        assert error is not None
        assert "terminal" in error

    def test_terminal_send_requires_an_explicit_bundle_grant(self):
        executor = self._make_scoped_executor()
        assert executor._check_bundle_scope("terminal_send") is not None

        executor.set_bundle_scope(["terminal", "terminal-send"])
        assert executor._check_bundle_scope("terminal_send") is None

    def test_terminal_kill_requires_terminal_scope(self):
        executor = self._make_scoped_executor()
        error = executor._check_bundle_scope("terminal_kill")
        assert error is not None
        assert "terminal" in error

    def test_scoped_agent_cannot_access_operator_started_session(self):
        async def run_attempt(tool_type):
            executor = self._make_scoped_executor()
            dispatched = False

            class EventBus:
                async def emit_with_hooks(self, *args, **kwargs):
                    return {}

            executor.event_bus = EventBus()

            async def terminal_dispatch(tool_data):
                nonlocal dispatched
                dispatched = True
                return ToolExecutionResult(
                    tool_id=tool_data["id"],
                    tool_type=tool_data["type"],
                    success=True,
                )

            executor._execute_terminal_command = terminal_dispatch
            result = await executor._execute_tool_inner(
                {
                    "id": f"operator-session-{tool_type}",
                    "type": tool_type,
                    "name": tool_type,
                    "session_id": "operator-session",
                    "session_name": "operator-session",
                }
            )

            assert result.success is False
            assert result.metadata["scope_denied"] is True
            assert "terminal" in result.error
            assert dispatched is False

        for tool_type in ("terminal_status", "terminal_output", "terminal_kill"):
            _run_async(run_attempt(tool_type))

    def test_mcp_tool_is_denied_without_mcp_grant(self):
        executor = self._make_scoped_executor()
        error = executor._check_bundle_scope("mcp_tool")
        assert error is not None
        assert "mcp-tool" in error

    def test_mcp_tool_is_allowed_with_explicit_mcp_grant(self):
        executor = self._make_scoped_executor()
        executor.set_bundle_scope(["terminal", "mcp-tool"])
        assert executor._check_bundle_scope("mcp_tool") is None

    def test_mcp_tool_is_allowed_by_concrete_tool_name(self):
        executor = self._make_scoped_executor()
        executor.set_bundle_scope(["terminal", "allowed_tool"])
        assert (
            executor._check_bundle_scope("mcp_tool", tool_name="allowed_tool") is None
        )

    def test_mcp_tool_is_allowed_by_server_qualified_name(self):
        executor = ToolExecutor(
            mcp_integration=_FakeMCPIntegration(),
            event_bus=None,
            terminal_timeout=30,
            mcp_timeout=30,
        )
        executor.set_bundle_scope(["mcp:demo-server:allowed_tool"])
        assert (
            executor._check_bundle_scope("mcp_tool", tool_name="allowed_tool") is None
        )

    def test_execute_tool_uses_concrete_mcp_scope(self):
        executor = ToolExecutor(
            mcp_integration=_FakeMCPIntegration(),
            event_bus=_FakeEventBus(),
            terminal_timeout=30,
            mcp_timeout=30,
        )
        executor.set_bundle_scope(["allowed_tool"])

        result = _run_async(
            executor.execute_tool(
                {
                    "id": "mcp-1",
                    "type": "mcp_tool",
                    "name": "allowed_tool",
                    "arguments": {},
                }
            )
        )

        assert result.success is True
        assert result.output == "called allowed_tool"
        assert result.metadata.get("scope_denied") is None

    def test_execute_tool_uses_server_qualified_mcp_scope(self):
        executor = ToolExecutor(
            mcp_integration=_FakeMCPIntegration(),
            event_bus=_FakeEventBus(),
            terminal_timeout=30,
            mcp_timeout=30,
        )
        executor.set_bundle_scope(["mcp:demo-server:allowed_tool"])

        result = _run_async(
            executor.execute_tool(
                {
                    "id": "mcp-2",
                    "type": "mcp_tool",
                    "name": "allowed_tool",
                    "arguments": {},
                }
            )
        )

        assert result.success is True
        assert result.output == "called allowed_tool"
        assert result.metadata.get("scope_denied") is None

    def test_mcp_tool_rejects_unlisted_concrete_tool_name(self):
        executor = self._make_scoped_executor()
        executor.set_bundle_scope(["terminal", "other_tool"])
        error = executor._check_bundle_scope("mcp_tool", tool_name="allowed_tool")
        assert error is not None
        assert "mcp-tool" in error

    def test_mcp_tool_is_allowed_with_explicit_tool_name_grant(self):
        executor = self._make_scoped_executor()
        executor.set_bundle_scope(["terminal", "create_issue"])
        assert executor._check_bundle_scope("mcp_tool", "create_issue") is None

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

        executor = ToolExecutor(
            mcp_integration=None,
            event_bus=None,
            terminal_timeout=30,
            mcp_timeout=30,
        )
        executor.set_bundle_scope(["scratchpad-append"])

        assert executor._check_bundle_scope("scratchpad_append") is None

    def test_removed_wait_for_user_is_not_in_bundle_scope(self):
        """wait_for_user is no longer a registered runtime tool."""
        from kollabor_agent.tool_registry import ToolRegistry
        ToolRegistry._instance = None

        executor = ToolExecutor(
            mcp_integration=None,
            event_bus=None,
            terminal_timeout=30,
            mcp_timeout=30,
        )
        executor.set_bundle_scope(["terminal"])
        registry = ToolRegistry.get_global()

        assert registry.get("wait-for-user") is None
        assert registry.get_by_xml_tag("wait_for_user") is None
        assert executor._check_bundle_scope("wait_for_user") is not None

class TestWildcardScope:
    """Test ["*"] wildcard support for allow-all tool scope."""

    def test_wildcard_mixed_with_names_allows_all_tools(self):
        """A wildcard makes additional listed names redundant for scope checks."""
        from kollabor_agent.tool_executor import ToolExecutor

        executor = ToolExecutor(
            mcp_integration=None,
            event_bus=None,
            terminal_timeout=30,
            mcp_timeout=30,
        )
        executor.set_bundle_scope(["*", "file-read"])

        assert executor._bundle_tools is None
        assert executor._check_bundle_scope("terminal") is None

    def test_wildcard_in_set_bundle_scope_allows_all(self):
        """["*"] passed to set_bundle_scope should be treated as None (all allowed)."""
        from kollabor_agent.tool_executor import ToolExecutor

        executor = ToolExecutor(
            mcp_integration=None,
            event_bus=None,
            terminal_timeout=30,
            mcp_timeout=30,
        )
        executor.set_bundle_scope(["*"])

        # Should be treated as None — all tools allowed
        assert executor._bundle_tools is None
        # Any tool should pass scope check
        assert executor._check_bundle_scope("file-read") is None
        assert executor._check_bundle_scope("terminal") is None
        assert executor._check_bundle_scope("web-search") is None

    def test_get_agent_tool_scope_wildcard_returns_none(self):
        """get_agent_tool_scope should return None for ["*"] wildcard."""
        from kollabor_agent.runtime import get_agent_tool_scope

        class FakeAgent:
            tools = ["*"]

        result = get_agent_tool_scope(FakeAgent())
        assert result is None

    def test_get_agent_tool_scope_wildcard_in_config(self):
        """get_agent_tool_scope should return None for ["*"] in agent config."""
        from kollabor_agent.runtime import get_agent_tool_scope

        class FakeConfig:
            def get(self, key):
                if key == "tools":
                    return ["*"]
                return None

        class FakeAgent:
            tools = None
            config = FakeConfig()

        result = get_agent_tool_scope(FakeAgent())
        assert result is None

    def test_get_agent_tool_scope_normal_list_still_works(self):
        """Normal tool lists should still be returned as-is."""
        from kollabor_agent.runtime import get_agent_tool_scope

        class FakeAgent:
            tools = ["file-read", "terminal"]

        result = get_agent_tool_scope(FakeAgent())
        assert result == ["file-read", "terminal"]
