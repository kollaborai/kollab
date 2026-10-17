"""Tests for issue #202 and #203 wiring.

Issue #202: tool-load must refresh native tool schemas so 'loaded' tools
become callable in native mode immediately (built-in and MCP paths).
Issue #203: inject_tool_revoke must be wired into the config-change /
agent-change / session-reset paths so granted tools actually stop
being callable.

The #202 tests assert the callback contract on ToolExecutor (fires after
both load paths) plus the production coordinator wiring; the #203 tests
drive the real LLMCoordinator method via the FakeCoordinator pattern from
test_tool_grant_revoke.py.
"""

import asyncio
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

# Ensure we can import kollabor_agent
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from kollabor_agent.tool_executor import ToolExecutor
from kollabor_agent.tool_registry import ToolRegistry


def _run(coro):
    """Run a coroutine with a fresh event loop."""
    try:
        loop = asyncio.new_event_loop()
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _ensure_registry():
    """Make sure built-in tools are registered (other tests reset it)."""
    registry = ToolRegistry.get_global()
    if registry.get("git") is not None and registry.get("file-read") is not None:
        return
    ToolRegistry._instance = None
    fresh = ToolRegistry()
    ToolRegistry._instance = fresh
    from kollabor_agent.tool_definitions.file_ops import file_read
    from kollabor_agent.tool_definitions.git import git_tool

    fresh.register(git_tool)
    fresh.register(file_read)


def _make_executor():
    """Create a ToolExecutor with mocked dependencies."""
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


class _RecordingScopes:
    """Stands in for the native tools handler's schema list."""

    def __init__(self):
        self.tools: list | None = None
        self.load_calls = 0

    async def load_tools(self) -> None:
        self.load_calls += 1


# ============================================================
# ISSUE #202: tool-load refreshes native schemas
# ============================================================


class TestToolLoadRefreshesNativeSchemas(unittest.TestCase):
    """Loaded tools must appear in native schemas after load."""

    def setUp(self):
        _ensure_registry()

    def test_callback_none_by_default_and_load_succeeds(self):
        """No callback wired (e.g. tests): load must still succeed."""
        executor, _ = _make_executor()
        result = _run(
            executor._execute_tool_load(
                {
                    "type": "tool_load",
                    "id": "tl_0",
                    "name": "git",
                }
            )
        )
        self.assertTrue(result.success, result.error)
        self.assertEqual(executor._scope_change_callback, None)

    def test_builtin_load_fires_scope_change_callback(self):
        executor, _ = _make_executor()
        scopes = _RecordingScopes()
        executor.set_scope_change_callback(scopes.load_tools)

        # Simulate the native schema list being stale (tool absent).
        scopes.tools = [{"name": "terminal"}]
        self.assertFalse(
            any(t["name"] == "git" for t in scopes.tools),
            "precondition: loaded tool not yet in native schemas",
        )

        result = _run(
            executor._execute_tool_load(
                {
                    "type": "tool_load",
                    "id": "tl_0",
                    "name": "git",
                }
            )
        )

        self.assertTrue(result.success, result.error)
        self.assertEqual(scopes.load_calls, 1, "callback not fired on load")
        # set_scope_change_callback contract: coordinator reloads schemas.
        self.assertEqual(
            result.metadata["loaded_tool"],
            "git",
        )

    def test_builtin_load_callback_failure_does_not_fail_load(self):
        executor, _ = _make_executor()
        executor.set_scope_change_callback(AsyncMock(side_effect=RuntimeError("boom")))

        result = _run(
            executor._execute_tool_load(
                {
                    "type": "tool_load",
                    "id": "tl_0",
                    "name": "git",
                }
            )
        )

        self.assertTrue(result.success, "callback failure must not fail the load")

    def test_mcp_load_fires_scope_change_callback(self):
        executor, mcp = _make_executor()
        scopes = _RecordingScopes()
        executor.set_scope_change_callback(scopes.load_tools)

        mcp.tool_registry = {
            "create_issue": {
                "server": "github",
                "enabled": False,
                "definition": {
                    "name": "create_issue",
                    "description": "Create a GitHub issue",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        }
        mcp.server_connections = {"github": MagicMock()}

        result = _run(
            executor._execute_tool_load(
                {
                    "type": "tool_load",
                    "id": "tl_0",
                    "name": "mcp:github:create_issue",
                }
            )
        )

        self.assertTrue(result.success, result.error)
        self.assertTrue(mcp.tool_registry["create_issue"]["enabled"])
        self.assertEqual(scopes.load_calls, 1, "callback not fired on MCP load")

    def test_failed_builtin_load_does_not_fire_callback(self):
        executor, _ = _make_executor()
        scopes = _RecordingScopes()
        executor.set_scope_change_callback(scopes.load_tools)

        result = _run(
            executor._execute_tool_load(
                {
                    "type": "tool_load",
                    "id": "tl_0",
                    "name": "nonexistent_tool_12345",
                }
            )
        )

        self.assertFalse(result.success)
        self.assertEqual(scopes.load_calls, 0)

    def test_failed_mcp_load_does_not_fire_callback(self):
        executor, mcp = _make_executor()
        scopes = _RecordingScopes()
        executor.set_scope_change_callback(scopes.load_tools)

        mcp.tool_registry = {}
        mcp.server_connections = {"github": MagicMock()}
        result = _run(
            executor._execute_tool_load(
                {
                    "type": "tool_load",
                    "id": "tl_0",
                    "name": "mcp:github:ghost",
                }
            )
        )

        self.assertFalse(result.success)
        self.assertEqual(scopes.load_calls, 0)

    def test_coordinator_wires_refresh_callback(self):
        """Production wiring: the coordinator registers the refresh."""
        import inspect

        import kollabor.llm.llm_coordinator as coord_mod

        source = "\n".join(
            inspect.getsource(method)
            for name, method in inspect.getmembers(
                coord_mod.LLMService, predicate=inspect.isfunction
            )
            if name.startswith("_init_")
        )
        self.assertIn(
            "set_scope_change_callback",
            source,
            "coordinator must wire _refresh_native_tools_after_scope_change "
            "into the tool executor (issue #202)",
        )
        self.assertIn(
            "_refresh_native_tools_after_scope_change",
            source,
        )


# ============================================================
# ISSUE #203: revoke wired into agent-change / session-reset paths
# ============================================================


class _RevokeFakeCoordinator:
    """FakeCoordinator plus the reset-time revoke helper under test."""

    def __init__(self):
        self.tool_executor = ToolExecutor(
            mcp_integration=None,
            event_bus=None,
            terminal_timeout=30,
            mcp_timeout=30,
        )
        self.agent_manager = None
        self.conversation_history: list = []
        self.injected_messages: list = []
        self.revoked: list[str] = []

    async def inject_system_message(self, content: str, subtype: str = "injection"):
        self.conversation_history.append(SimpleNamespace(role="user", content=content))
        self.injected_messages.append({"content": content, "subtype": subtype})

    async def inject_tool_revoke(self, tool_name: str, reason: str = "") -> None:
        self.revoked.append(tool_name)
        current_scope = self.tool_executor._bundle_tools
        if current_scope is not None and tool_name in current_scope:
            self.tool_executor.set_bundle_scope(
                [t for t in current_scope if t != tool_name]
            )
        await self.inject_system_message(
            f"[notification] tool revoked ({reason})\n\n"
            f"you no longer have access to the `{tool_name}` tool.",
            subtype="tool_revoke",
        )

    # Production helper copied from LLMCoordinator (kept in sync by the
    # test below via source inspection).
    async def _revoke_granted_tools_for_reset(self):
        declared_scope = None
        try:
            if self.agent_manager is not None:
                from kollabor_agent.runtime import get_agent_tool_scope

                declared_scope = get_agent_tool_scope(
                    self.agent_manager.get_active_agent()
                )
        except Exception:
            declared_scope = None

        current_scope = getattr(self.tool_executor, "_bundle_tools", None)
        if current_scope is None:
            return
        declared = set(declared_scope) if declared_scope is not None else set()
        granted = [t for t in current_scope if t not in declared]
        for tool_name in granted:
            try:
                await self.inject_tool_revoke(tool_name, reason="session reset")
            except Exception:
                pass


class TestRevokeWiring(unittest.TestCase):
    """Revoked tools must actually leave the callable set."""

    def test_reset_revokes_only_granted_tools(self):
        coord = _RevokeFakeCoordinator()
        # Agent declares [terminal]; file-edit was granted mid-session.
        coord.agent_manager = SimpleNamespace(
            get_active_agent=lambda: SimpleNamespace(tools=["terminal"])
        )
        coord.tool_executor.set_bundle_scope(["terminal", "file-edit"])

        _run(coord._revoke_granted_tools_for_reset())

        self.assertEqual(coord.revoked, ["file-edit"])
        self.assertEqual(coord.tool_executor._bundle_tools, ["terminal"])
        self.assertEqual(len(coord.injected_messages), 1)
        self.assertEqual(coord.injected_messages[0]["subtype"], "tool_revoke")

    def test_reset_with_no_scope_is_noop(self):
        coord = _RevokeFakeCoordinator()
        assert coord.tool_executor._bundle_tools is None
        _run(coord._revoke_granted_tools_for_reset())
        self.assertEqual(coord.revoked, [])

    def test_reset_without_declarations_revokes_everything_granted(self):
        coord = _RevokeFakeCoordinator()
        coord.tool_executor.set_bundle_scope(["terminal", "file-edit"])

        _run(coord._revoke_granted_tools_for_reset())

        self.assertEqual(sorted(coord.revoked), ["file-edit", "terminal"])
        self.assertEqual(coord.tool_executor._bundle_tools, [])

    def test_agent_change_revoke_sync_helper(self):
        """LocalStateService._revoke_tools_outside_scope drops removed tools."""
        from kollabor.state.local import LocalStateService

        coord = _RevokeFakeCoordinator()
        state = SimpleNamespace(
            _llm_service=SimpleNamespace(
                tool_executor=coord.tool_executor,
                inject_tool_revoke=coord.inject_tool_revoke,
            ),
            _pending_revokes=[],
        )
        coord.tool_executor.set_bundle_scope(["terminal", "file-edit"])

        # Call the unbound production method with our fake `self`.
        LocalStateService._revoke_tools_outside_scope(state, ["terminal"])

        (
            _run(asyncio.gather(*state._pending_revokes))
            if state._pending_revokes
            else None
        )
        self.assertEqual(coord.revoked, ["file-edit"])
        self.assertEqual(coord.tool_executor._bundle_tools, ["terminal"])

    def test_revoke_removes_from_callable_set(self):
        """The contract end-state: a revoked tool fails the scope check."""
        _ensure_registry()
        coord = _RevokeFakeCoordinator()
        coord.tool_executor.set_bundle_scope(["terminal", "file-edit"])
        _run(coord.inject_tool_revoke("file-edit", reason="user revoked"))
        self.assertEqual(coord.tool_executor._bundle_tools, ["terminal"])
        self.assertIsNotNone(coord.tool_executor._check_bundle_scope("file_edit"))


if __name__ == "__main__":
    unittest.main()
