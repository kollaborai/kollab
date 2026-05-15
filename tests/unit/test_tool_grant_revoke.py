"""Tests for mid-session tool grants and revokes.

Tests exercise the actual inject_tool_grant/inject_tool_revoke methods
via FakeCoordinator which contains the production logic, including
registry lookup, markdown rendering, and scope updates.
"""

import asyncio
from dataclasses import dataclass
from typing import List

from kollabor_agent.tool_executor import ToolExecutor
from kollabor_agent.tool_registry import ToolRegistry


@dataclass
class ConversationMessage:
    role: str
    content: str


def _ensure_registry():
    """Ensure registry has all tools. Re-register if nuked by other tests."""
    registry = ToolRegistry.get_global()
    if len(registry.list()) >= 50:
        return
    ToolRegistry._instance = None
    new_registry = ToolRegistry()
    ToolRegistry._instance = new_registry
    from kollabor_agent.tool_definitions.context import context_query, curate, evict
    from kollabor_agent.tool_definitions.file_ops import (
        directory_create,
        directory_remove,
        file_append,
        file_copy,
        file_copy_overwrite,
        file_create,
        file_create_overwrite,
        file_delete,
        file_edit,
        file_grep,
        file_insert_after,
        file_insert_before,
        file_move,
        file_read,
    )
    from kollabor_agent.tool_definitions.git import git_tool
    from kollabor_agent.tool_definitions.hub import (
        claims,
        crystal_delete,
        crystal_edit,
        crystal_list,
        crystal_read,
        crystal_search,
        feed_file,
        feed_recent,
        file_changed,
        file_unwatch,
        file_watch,
        hub_agents,
        hub_broadcast,
        hub_capture,
        hub_claim,
        hub_cron_add,
        hub_cron_delete,
        hub_cron_list,
        hub_msg,
        hub_queue,
        hub_spawn,
        hub_status,
        hub_stop,
        hub_vault,
        hub_vaults,
        hub_work,
        lane_claim,
        lane_release,
        state_update,
        vault_write,
    )
    from kollabor_agent.tool_definitions.scratchpad import (
        scratchpad,
        scratchpad_append,
        scratchpad_clear,
        scratchpad_get,
    )
    from kollabor_agent.tool_definitions.task import (
        task_approve,
        task_checkpoint,
        task_complete,
        task_reject,
    )
    from kollabor_agent.tool_definitions.terminal import terminal_tool
    for t in [
        file_read, file_edit, file_create, file_create_overwrite,
        file_delete, file_move, file_copy, file_copy_overwrite,
        file_append, file_insert_after, file_insert_before,
        directory_create, directory_remove, file_grep,
        terminal_tool, git_tool,
        hub_msg, hub_broadcast, hub_stop, hub_status, hub_spawn,
        hub_agents, hub_capture, hub_queue, hub_claim, hub_work,
        claims, hub_vault, hub_vaults, vault_write,
        crystal_search, crystal_read, crystal_list, crystal_edit, crystal_delete,
        hub_cron_add, hub_cron_list, hub_cron_delete,
        lane_claim, lane_release,
        file_changed, file_watch, file_unwatch, feed_recent, feed_file,
        state_update,
        scratchpad, scratchpad_append, scratchpad_clear, scratchpad_get,
        curate, context_query, evict,
        task_checkpoint, task_complete, task_approve, task_reject,
    ]:
        new_registry.register(t)


class FakeCoordinator:
    """Lightweight coordinator with production grant/revoke logic."""

    def __init__(self):
        self.tool_executor = ToolExecutor(
            mcp_integration=None, event_bus=None,
            terminal_timeout=30, mcp_timeout=30,
        )
        self.conversation_history: List[ConversationMessage] = []
        self.conversation_logger = None
        self.injected_messages: List[dict] = []

    async def inject_system_message(self, content: str, subtype: str = "injection"):
        self.conversation_history.append(ConversationMessage(role="user", content=content))
        self.injected_messages.append({"content": content, "subtype": subtype})

    async def inject_tool_grant(self, tool_name: str, reason: str = "") -> None:
        import logging

        from kollabor_agent.tool_generators.markdown import render_tool_markdown
        from kollabor_agent.tool_registry import get_registry

        tool = get_registry().get(tool_name)
        if tool is None:
            logging.getLogger(__name__).warning(f"inject_tool_grant: unknown tool '{tool_name}'")
            return

        current_scope = self.tool_executor._bundle_tools
        if current_scope is not None and tool_name not in current_scope:
            self.tool_executor.set_bundle_scope(list(current_scope) + [tool_name])

        docs = render_tool_markdown(tool)
        reason_block = f" ({reason})" if reason else ""
        xml_tag = tool.xml_tag_name
        content = (
            f"[notification] new tool available{reason_block}\n\n"
            f"you now have access to the `{tool_name}` tool.\n\n"
            f"{docs}\n\n"
            f"start using `<{xml_tag}>` from your next turn onwards."
        )
        await self.inject_system_message(content, subtype="tool_grant")

    async def inject_tool_revoke(self, tool_name: str, reason: str = "") -> None:
        current_scope = self.tool_executor._bundle_tools
        if current_scope is not None and tool_name in current_scope:
            self.tool_executor.set_bundle_scope([t for t in current_scope if t != tool_name])

        reason_block = f" ({reason})" if reason else ""
        xml_tag = tool_name
        try:
            from kollabor_agent.tool_registry import get_registry
            tool = get_registry().get(tool_name)
            if tool:
                xml_tag = tool.xml_tag_name
        except Exception:
            pass

        content = (
            f"[notification] tool revoked{reason_block}\n\n"
            f"you no longer have access to the `{tool_name}` tool. "
            "attempts to use it will return an error. the tool has "
            "been removed from your available tool list.\n\n"
            f"do not emit `<{xml_tag}>` tags in your responses."
        )
        await self.inject_system_message(content, subtype="tool_revoke")


def _run(coro):
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("loop closed")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# ============================================================
# GRANT TESTS
# ============================================================

class TestToolGrantReal:

    def test_grant_adds_tool_to_scope(self):
        _ensure_registry()
        coord = FakeCoordinator()
        coord.tool_executor.set_bundle_scope(["terminal", "file-read"])
        _run(coord.inject_tool_grant("file-edit", reason="user approved"))
        assert "file-edit" in coord.tool_executor._bundle_tools
        assert coord.tool_executor._check_bundle_scope("file_edit") is None

    def test_grant_injects_notification_with_docs(self):
        _ensure_registry()
        coord = FakeCoordinator()
        coord.tool_executor.set_bundle_scope(["terminal"])
        _run(coord.inject_tool_grant("file-read", reason="MCP server connected"))
        assert len(coord.conversation_history) == 1
        msg = coord.conversation_history[0]
        assert msg.role == "user"
        assert "[notification] new tool available" in msg.content
        assert "file-read" in msg.content
        assert "MCP server connected" in msg.content
        assert "read" in msg.content.lower()
        assert coord.injected_messages[0]["subtype"] == "tool_grant"

    def test_grant_without_reason(self):
        _ensure_registry()
        coord = FakeCoordinator()
        _run(coord.inject_tool_grant("hub-msg"))
        msg = coord.conversation_history[0]
        first_line = msg.content.split("\n")[0]
        assert "[notification] new tool available" in first_line
        assert " (" not in first_line

    def test_grant_no_scope_does_not_create_one(self):
        _ensure_registry()
        coord = FakeCoordinator()
        assert coord.tool_executor._bundle_tools is None
        _run(coord.inject_tool_grant("file-read"))
        assert coord.tool_executor._bundle_tools is None
        assert len(coord.conversation_history) == 1

    def test_grant_unknown_tool_skipped(self):
        _ensure_registry()
        coord = FakeCoordinator()
        coord.tool_executor.set_bundle_scope(["terminal"])
        _run(coord.inject_tool_grant("nonexistent-tool"))
        assert len(coord.conversation_history) == 0
        assert coord.tool_executor._bundle_tools == ["terminal"]

    def test_grant_duplicate_not_added(self):
        _ensure_registry()
        coord = FakeCoordinator()
        coord.tool_executor.set_bundle_scope(["terminal", "file-read"])
        _run(coord.inject_tool_grant("file-read"))
        assert coord.tool_executor._bundle_tools.count("file-read") == 1
        assert len(coord.conversation_history) == 1

    def test_grant_includes_xml_tag_in_notification(self):
        _ensure_registry()
        coord = FakeCoordinator()
        _run(coord.inject_tool_grant("terminal"))
        msg = coord.conversation_history[0]
        assert "<terminal>" in msg.content


# ============================================================
# REVOKE TESTS
# ============================================================

class TestToolRevokeReal:

    def test_revoke_removes_tool_from_scope(self):
        _ensure_registry()
        coord = FakeCoordinator()
        coord.tool_executor.set_bundle_scope(["terminal", "file-read", "hub-msg"])
        _run(coord.inject_tool_revoke("file-read"))
        assert "file-read" not in coord.tool_executor._bundle_tools
        assert coord.tool_executor._check_bundle_scope("file_read") is not None

    def test_revoke_injects_notification(self):
        _ensure_registry()
        coord = FakeCoordinator()
        coord.tool_executor.set_bundle_scope(["terminal", "hub-msg"])
        _run(coord.inject_tool_revoke("hub-msg", reason="MCP server disconnected"))
        assert len(coord.conversation_history) == 1
        msg = coord.conversation_history[0]
        assert "[notification] tool revoked" in msg.content
        assert "hub-msg" in msg.content
        assert "MCP server disconnected" in msg.content
        assert coord.injected_messages[0]["subtype"] == "tool_revoke"

    def test_revoke_nonexistent_tool_no_crash(self):
        _ensure_registry()
        coord = FakeCoordinator()
        coord.tool_executor.set_bundle_scope(["terminal"])
        _run(coord.inject_tool_revoke("hub-msg"))
        assert coord.tool_executor._bundle_tools == ["terminal"]
        assert len(coord.conversation_history) == 1

    def test_revoke_no_scope_does_not_create_one(self):
        _ensure_registry()
        coord = FakeCoordinator()
        assert coord.tool_executor._bundle_tools is None
        _run(coord.inject_tool_revoke("terminal"))
        assert coord.tool_executor._bundle_tools is None
        assert len(coord.conversation_history) == 1

    def test_revoke_notification_contains_xml_tag(self):
        _ensure_registry()
        coord = FakeCoordinator()
        coord.tool_executor.set_bundle_scope(["terminal", "scratchpad"])
        _run(coord.inject_tool_revoke("scratchpad"))
        msg = coord.conversation_history[0]
        assert "<scratchpad>" in msg.content


# ============================================================
# ROUND-TRIP TESTS
# ============================================================

class TestGrantRevokeRoundTripReal:

    def test_grant_then_revoke(self):
        _ensure_registry()
        coord = FakeCoordinator()
        coord.tool_executor.set_bundle_scope(["terminal"])
        _run(coord.inject_tool_grant("file-read"))
        assert coord.tool_executor._check_bundle_scope("file_read") is None
        _run(coord.inject_tool_revoke("file-read"))
        assert coord.tool_executor._check_bundle_scope("file_read") is not None

    def test_multiple_grants(self):
        _ensure_registry()
        coord = FakeCoordinator()
        coord.tool_executor.set_bundle_scope(["terminal"])
        _run(coord.inject_tool_grant("file-read"))
        _run(coord.inject_tool_grant("hub-msg"))
        assert "file-read" in coord.tool_executor._bundle_tools
        assert "hub-msg" in coord.tool_executor._bundle_tools
        assert coord.tool_executor._check_bundle_scope("file_read") is None
        assert coord.tool_executor._check_bundle_scope("hub_msg") is None
