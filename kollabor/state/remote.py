"""RemoteStateService: RPC-backed StateService implementation.

Used in attach mode (kollab --attach). Forwards every StateService call
to the daemon via kollabor_rpc.RpcClient and reconstructs snapshot DTOs
from the dict responses.

There is NO caching in phase 2 - every call goes to the wire. Phase 3
adds per-method TTLs and stale-while-revalidate semantics for reads.

This implementation is the mirror of LocalStateService: same interface,
different transport. Commands and widgets don't know which one they're
holding.
"""

from __future__ import annotations

import logging
from typing import Any

from .context import ContextListSnapshot, ConversationContext
from .interface import StateService
from .snapshots import (
    AgentListSnapshot,
    AgentSnapshot,
    ConversationSnapshot,
    HubSnapshot,
    McpSnapshot,
    PermissionSnapshot,
    ProcessingSnapshot,
    ProfileListSnapshot,
    ProfileSnapshot,
    SessionStats,
    SkillListSnapshot,
    SystemInfoSnapshot,
    SystemPromptSnapshot,
)

logger = logging.getLogger(__name__)


class RemoteStateService(StateService):
    """RPC-backed StateService. Wraps a kollabor_rpc.RpcClient.

    The client is injected at construction. All methods serialize params
    as plain dicts, call the daemon via client.call("state.<method>", params),
    and reconstruct snapshot DTOs from the returned dicts.
    """

    # Default timeout for all state calls. Read operations are snapshot-shaped
    # and small; writes go through the same path with the same budget.
    DEFAULT_TIMEOUT: float = 10.0

    def __init__(self, rpc_client: Any, *, timeout: float | None = None) -> None:
        """Initialize with an RpcClient instance.

        Args:
            rpc_client: kollabor_rpc.RpcClient instance, already wired to a
                daemon via StreamWriter (handled by application.py attach path).
            timeout: Override default RPC timeout. None uses DEFAULT_TIMEOUT.
        """
        self._rpc = rpc_client
        self._timeout = timeout if timeout is not None else self.DEFAULT_TIMEOUT

    # === Conversation ===

    async def get_conversation(self) -> ConversationSnapshot:
        """Fetch the daemon's conversation via RPC and reconstruct the snapshot."""
        logger.debug("state rpc: get_conversation")
        result = await self._rpc.call(
            "state.get_conversation", {}, timeout=self._timeout
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.get_conversation expected dict, got {type(result).__name__}"
            )
        return ConversationSnapshot.from_dict(result)

    async def save_conversation(self, format: str = "transcript") -> str:
        """Ask the daemon to format the conversation and return the resulting string.

        The destination (file, clipboard, etc.) is the caller's concern.
        The daemon returns only the formatted content.
        """
        logger.debug("state rpc: save_conversation format=%s", format)
        result = await self._rpc.call(
            "state.save_conversation",
            {"format": format},
            timeout=self._timeout,
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.save_conversation expected dict, got {type(result).__name__}"
            )
        content = result.get("content")
        if not isinstance(content, str):
            raise TypeError("state.save_conversation result missing 'content' string")
        return content

    # === Session stats ===

    async def get_session_stats(self) -> SessionStats:
        """Fetch current session stats from the daemon."""
        logger.debug("state rpc: get_session_stats")
        result = await self._rpc.call(
            "state.get_session_stats", {}, timeout=self._timeout
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.get_session_stats expected dict, got {type(result).__name__}"
            )
        return SessionStats.from_dict(result)

    # === Profile ===

    async def get_active_profile(self) -> ProfileSnapshot:
        """Fetch the daemon's currently active profile."""
        logger.debug("state rpc: get_active_profile")
        result = await self._rpc.call(
            "state.get_active_profile", {}, timeout=self._timeout
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.get_active_profile expected dict, got {type(result).__name__}"
            )
        return ProfileSnapshot.from_dict(result)

    async def list_profiles(self) -> ProfileListSnapshot:
        """Fetch the full profile registry from the daemon."""
        logger.debug("state rpc: list_profiles")
        result = await self._rpc.call("state.list_profiles", {}, timeout=self._timeout)
        if not isinstance(result, dict):
            raise TypeError(
                f"state.list_profiles expected dict, got {type(result).__name__}"
            )
        return ProfileListSnapshot.from_dict(result)

    # === Permissions ===

    async def get_permission_state(self) -> PermissionSnapshot:
        """Fetch the daemon's permission manager state."""
        logger.debug("state rpc: get_permission_state")
        result = await self._rpc.call(
            "state.get_permission_state", {}, timeout=self._timeout
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.get_permission_state expected dict, got {type(result).__name__}"
            )
        return PermissionSnapshot.from_dict(result)

    # === MCP ===

    async def get_mcp_state(self) -> McpSnapshot:
        """Fetch the daemon's MCP integration state."""
        logger.debug("state rpc: get_mcp_state")
        result = await self._rpc.call("state.get_mcp_state", {}, timeout=self._timeout)
        if not isinstance(result, dict):
            raise TypeError(
                f"state.get_mcp_state expected dict, got {type(result).__name__}"
            )
        return McpSnapshot.from_dict(result)

    # === Hub ===

    async def get_hub_state(self) -> HubSnapshot:
        """Fetch the daemon's hub plugin state."""
        logger.debug("state rpc: get_hub_state")
        result = await self._rpc.call("state.get_hub_state", {}, timeout=self._timeout)
        if not isinstance(result, dict):
            raise TypeError(
                f"state.get_hub_state expected dict, got {type(result).__name__}"
            )
        return HubSnapshot.from_dict(result)

    # === Processing ===

    async def get_processing_state(self) -> ProcessingSnapshot:
        """Fetch the daemon's LLM processing / queue / task state."""
        logger.debug("state rpc: get_processing_state")
        result = await self._rpc.call(
            "state.get_processing_state", {}, timeout=self._timeout
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.get_processing_state expected dict, got {type(result).__name__}"
            )
        return ProcessingSnapshot.from_dict(result)

    # === System ===

    async def get_system_info(self) -> SystemInfoSnapshot:
        """Fetch the daemon's system info (cwd, git branch, pid, uptime)."""
        logger.debug("state rpc: get_system_info")
        result = await self._rpc.call(
            "state.get_system_info", {}, timeout=self._timeout
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.get_system_info expected dict, got {type(result).__name__}"
            )
        return SystemInfoSnapshot.from_dict(result)

    # === Writes (phase 4) ===

    async def set_active_profile(
        self,
        name: str,
        *,
        persist: bool = False,
        persist_local: bool = False,
    ) -> ProfileSnapshot:
        """Ask the daemon to switch profiles and return the new snapshot.

        The daemon wraps LocalStateService.set_active_profile, which kicks
        off the provider reinitialize and native tool reload as
        background tasks. By the time the RPC reply arrives, the
        in-memory profile has switched but the provider reinit may still
        be in flight -- the next chat turn is the one that actually
        sees the new model.

        persist/persist_local mirror the legacy --save / --save --local
        flags: when true the daemon writes the profile values back to
        config (global or local).
        """
        logger.debug(
            "state rpc: set_active_profile name=%s persist=%s local=%s",
            name,
            persist,
            persist_local,
        )
        result = await self._rpc.call(
            "state.set_active_profile",
            {
                "name": name,
                "persist": bool(persist),
                "persist_local": bool(persist_local),
            },
            timeout=self._timeout,
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.set_active_profile expected dict, got {type(result).__name__}"
            )
        # The handler returns either a ProfileSnapshot dict (on success) or
        # a {"error": "..."} envelope when the underlying ValueError is
        # caught (bad name). Honour the envelope by raising on the client.
        if "error" in result and result.get("error"):
            raise ValueError(str(result["error"]))
        return ProfileSnapshot.from_dict(result)

    async def set_approval_mode(self, mode: str) -> PermissionSnapshot:
        """Ask the daemon to change the approval mode and return the new snapshot.

        Accepts the same mode aliases as LocalStateService.set_approval_mode
        ("strict", "trust", etc.) -- the daemon handler does the mapping.
        """
        logger.debug("state rpc: set_approval_mode mode=%s", mode)
        result = await self._rpc.call(
            "state.set_approval_mode", {"mode": mode}, timeout=self._timeout
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.set_approval_mode expected dict, got {type(result).__name__}"
            )
        if "error" in result and result.get("error"):
            raise ValueError(str(result["error"]))
        return PermissionSnapshot.from_dict(result)

    # === Agents (phase 4.5) ===

    async def get_active_agent(self) -> AgentSnapshot:
        """Fetch the daemon's active agent snapshot."""
        logger.debug("state rpc: get_active_agent")
        result = await self._rpc.call(
            "state.get_active_agent", {}, timeout=self._timeout
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.get_active_agent expected dict, got {type(result).__name__}"
            )
        return AgentSnapshot.from_dict(result)

    async def list_agents(self) -> AgentListSnapshot:
        """Fetch the daemon's full agent registry."""
        logger.debug("state rpc: list_agents")
        result = await self._rpc.call("state.list_agents", {}, timeout=self._timeout)
        if not isinstance(result, dict):
            raise TypeError(
                f"state.list_agents expected dict, got {type(result).__name__}"
            )
        return AgentListSnapshot.from_dict(result)

    async def set_agent(self, name: str) -> AgentSnapshot:
        """Ask the daemon to switch agents and return the new snapshot."""
        logger.debug("state rpc: set_agent name=%s", name)
        result = await self._rpc.call(
            "state.set_agent", {"name": name}, timeout=self._timeout
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.set_agent expected dict, got {type(result).__name__}"
            )
        if "error" in result and result.get("error"):
            raise ValueError(str(result["error"]))
        return AgentSnapshot.from_dict(result)

    async def clear_agent(self) -> AgentSnapshot:
        """Ask the daemon to clear the active agent."""
        logger.debug("state rpc: clear_agent")
        result = await self._rpc.call("state.clear_agent", {}, timeout=self._timeout)
        if not isinstance(result, dict):
            raise TypeError(
                f"state.clear_agent expected dict, got {type(result).__name__}"
            )
        if "error" in result and result.get("error"):
            raise ValueError(str(result["error"]))
        return AgentSnapshot.from_dict(result)

    # === Skills (phase 4.5) ===

    async def list_skills(self, agent_name: str = "") -> SkillListSnapshot:
        """Fetch the skill list for an agent (active agent if empty)."""
        logger.debug("state rpc: list_skills agent=%s", agent_name or "<active>")
        result = await self._rpc.call(
            "state.list_skills",
            {"agent_name": agent_name},
            timeout=self._timeout,
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.list_skills expected dict, got {type(result).__name__}"
            )
        return SkillListSnapshot.from_dict(result)

    async def activate_skill(self, name: str) -> SkillListSnapshot:
        """Ask the daemon to load a skill onto the active agent."""
        logger.debug("state rpc: activate_skill name=%s", name)
        result = await self._rpc.call(
            "state.activate_skill", {"name": name}, timeout=self._timeout
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.activate_skill expected dict, got {type(result).__name__}"
            )
        if "error" in result and result.get("error"):
            raise ValueError(str(result["error"]))
        return SkillListSnapshot.from_dict(result)

    async def deactivate_skill(self, name: str) -> SkillListSnapshot:
        """Ask the daemon to unload a skill from the active agent."""
        logger.debug("state rpc: deactivate_skill name=%s", name)
        result = await self._rpc.call(
            "state.deactivate_skill", {"name": name}, timeout=self._timeout
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.deactivate_skill expected dict, got {type(result).__name__}"
            )
        if "error" in result and result.get("error"):
            raise ValueError(str(result["error"]))
        return SkillListSnapshot.from_dict(result)

    # === System prompt (phase 4.5) ===

    async def get_system_prompt(self) -> SystemPromptSnapshot:
        """Fetch the daemon's currently active system prompt."""
        logger.debug("state rpc: get_system_prompt")
        result = await self._rpc.call(
            "state.get_system_prompt", {}, timeout=self._timeout
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.get_system_prompt expected dict, got {type(result).__name__}"
            )
        return SystemPromptSnapshot.from_dict(result)

    async def set_system_prompt(
        self, content: str, *, source: str = "file", path: str = ""
    ) -> SystemPromptSnapshot:
        """Install a new system prompt on the daemon via RPC.

        The client is expected to have already read the file content in
        its own cwd and passed the string here -- the daemon never
        touches the filesystem for this operation.
        """
        logger.debug(
            "state rpc: set_system_prompt source=%s path=%s size=%d",
            source,
            path,
            len(content or ""),
        )
        result = await self._rpc.call(
            "state.set_system_prompt",
            {"content": content, "source": source, "path": path},
            timeout=self._timeout,
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.set_system_prompt expected dict, got {type(result).__name__}"
            )
        if "error" in result and result.get("error"):
            raise ValueError(str(result["error"]))
        return SystemPromptSnapshot.from_dict(result)

    # === Contexts (phase 4.5 step 6) ===

    async def list_contexts(
        self, *, include_archived: bool = False
    ) -> ContextListSnapshot:
        """Fetch the daemon's registry of conversation contexts."""
        logger.debug("state rpc: list_contexts include_archived=%s", include_archived)
        result = await self._rpc.call(
            "state.list_contexts",
            {"include_archived": bool(include_archived)},
            timeout=self._timeout,
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.list_contexts expected dict, got {type(result).__name__}"
            )
        return ContextListSnapshot.from_dict(result)

    async def get_active_context(self) -> ConversationContext:
        """Fetch the daemon's currently live context snapshot."""
        logger.debug("state rpc: get_active_context")
        result = await self._rpc.call(
            "state.get_active_context", {}, timeout=self._timeout
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.get_active_context expected dict, got {type(result).__name__}"
            )
        if "error" in result and result.get("error"):
            raise ValueError(str(result["error"]))
        return ConversationContext.from_dict(result)

    async def create_context(
        self,
        name: str,
        *,
        profile_name: str = "",
        agent_name: str = "",
        system_prompt: str = "",
    ) -> ConversationContext:
        """Ask the daemon to create a new context and return its snapshot."""
        logger.debug("state rpc: create_context name=%s", name)
        result = await self._rpc.call(
            "state.create_context",
            {
                "name": name,
                "profile_name": profile_name,
                "agent_name": agent_name,
                "system_prompt": system_prompt,
            },
            timeout=self._timeout,
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.create_context expected dict, got {type(result).__name__}"
            )
        if "error" in result and result.get("error"):
            raise ValueError(str(result["error"]))
        return ConversationContext.from_dict(result)

    async def attach_to_context(self, name: str) -> ConversationContext:
        """Switch the daemon's live context by name."""
        logger.debug("state rpc: attach_to_context name=%s", name)
        result = await self._rpc.call(
            "state.attach_to_context",
            {"name": name},
            timeout=self._timeout,
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.attach_to_context expected dict, got {type(result).__name__}"
            )
        if "error" in result and result.get("error"):
            # The daemon distinguishes "not found" from "turn in progress"
            # in the error string; the client just raises ValueError for
            # both. Callers that care can inspect the message.
            raise ValueError(str(result["error"]))
        return ConversationContext.from_dict(result)

    async def archive_context(self, name: str) -> ConversationContext:
        """Soft-delete a conversation context on the daemon."""
        logger.debug("state rpc: archive_context name=%s", name)
        result = await self._rpc.call(
            "state.archive_context",
            {"name": name},
            timeout=self._timeout,
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.archive_context expected dict, got {type(result).__name__}"
            )
        if "error" in result and result.get("error"):
            raise ValueError(str(result["error"]))
        return ConversationContext.from_dict(result)

    # === Session management (phase 4.5 step 7) ===

    async def restart_session(self) -> ConversationSnapshot:
        """Ask the daemon to restart the conversation session."""
        logger.debug("state rpc: restart_session")
        result = await self._rpc.call(
            "state.restart_session", {}, timeout=self._timeout
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.restart_session expected dict, got {type(result).__name__}"
            )
        if "error" in result and result.get("error"):
            raise ValueError(str(result["error"]))
        return ConversationSnapshot.from_dict(result)

    # === MCP writes (phase 4.5 step 7) ===

    async def enable_mcp_server(self, name: str) -> McpSnapshot:
        """Ask the daemon to enable an MCP server."""
        logger.debug("state rpc: enable_mcp_server name=%r", name)
        result = await self._rpc.call(
            "state.enable_mcp_server", {"name": name}, timeout=self._timeout
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.enable_mcp_server expected dict, got {type(result).__name__}"
            )
        if "error" in result and result.get("error"):
            raise ValueError(str(result["error"]))
        return McpSnapshot.from_dict(result)

    async def disable_mcp_server(self, name: str) -> McpSnapshot:
        """Ask the daemon to disable an MCP server."""
        logger.debug("state rpc: disable_mcp_server name=%r", name)
        result = await self._rpc.call(
            "state.disable_mcp_server", {"name": name}, timeout=self._timeout
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.disable_mcp_server expected dict, got {type(result).__name__}"
            )
        if "error" in result and result.get("error"):
            raise ValueError(str(result["error"]))
        return McpSnapshot.from_dict(result)

    async def test_mcp_server(self, name: str) -> dict[str, Any]:
        """Ask the daemon for an MCP server's status dict.

        The shape matches MCPManager.get_server_status (found / enabled /
        connected / tool_count / optional error) so the command handler
        can render it identically whether state came from local or remote.
        """
        logger.debug("state rpc: test_mcp_server name=%r", name)
        result = await self._rpc.call(
            "state.test_mcp_server", {"name": name}, timeout=self._timeout
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.test_mcp_server expected dict, got {type(result).__name__}"
            )
        if "error" in result and result.get("error"):
            # Only raise if found=False or error indicates "server not found".
            # Otherwise the status dict may legitimately include an "error"
            # key describing a connection problem, which the caller renders
            # as a diagnostic.
            if not result.get("found", True):
                raise ValueError(str(result["error"]))
        return result

    async def get_mcp_tools(
        self, server_filter: str | None = None
    ) -> dict[str, list[dict[str, Any]]]:
        """Ask the daemon for MCP tools grouped by server."""
        logger.debug("state rpc: get_mcp_tools filter=%r", server_filter)
        params: dict[str, Any] = {}
        if server_filter is not None:
            params["server_filter"] = server_filter
        result = await self._rpc.call(
            "state.get_mcp_tools", params, timeout=self._timeout
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.get_mcp_tools expected dict, got {type(result).__name__}"
            )
        # The daemon wraps the payload under "tools" so the envelope can
        # carry metadata later. Unwrap defensively.
        tools = result.get("tools", result)
        if not isinstance(tools, dict):
            return {}
        normalized: dict[str, list[dict[str, Any]]] = {}
        for server_name, server_tools in tools.items():
            if isinstance(server_tools, list):
                normalized[str(server_name)] = [
                    dict(t) for t in server_tools if isinstance(t, dict)
                ]
        return normalized

    async def reload_mcp_servers(self) -> dict[str, Any]:
        """Ask the daemon to hot-reload MCP config and reconnect servers."""
        logger.debug("state rpc: reload_mcp_servers")
        result = await self._rpc.call(
            "state.reload_mcp_servers", {}, timeout=self._timeout
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.reload_mcp_servers expected dict, got {type(result).__name__}"
            )
        if "error" in result and result.get("error"):
            raise ValueError(str(result["error"]))
        return result

    # === Permission writes + project reads (phase 4.5 step 7) ===

    async def clear_session_approvals(self) -> PermissionSnapshot:
        """Ask the daemon to clear session-scoped approvals."""
        logger.debug("state rpc: clear_session_approvals")
        result = await self._rpc.call(
            "state.clear_session_approvals", {}, timeout=self._timeout
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.clear_session_approvals expected dict, got {type(result).__name__}"
            )
        if "error" in result and result.get("error"):
            raise ValueError(str(result["error"]))
        return PermissionSnapshot.from_dict(result)

    async def clear_project_approvals(self) -> PermissionSnapshot:
        """Ask the daemon to clear project-scoped approvals."""
        logger.debug("state rpc: clear_project_approvals")
        result = await self._rpc.call(
            "state.clear_project_approvals", {}, timeout=self._timeout
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.clear_project_approvals expected dict, got {type(result).__name__}"
            )
        if "error" in result and result.get("error"):
            raise ValueError(str(result["error"]))
        return PermissionSnapshot.from_dict(result)

    async def list_project_approvals(self) -> list[str]:
        """Ask the daemon for the project-scoped approval list."""
        logger.debug("state rpc: list_project_approvals")
        result = await self._rpc.call(
            "state.list_project_approvals", {}, timeout=self._timeout
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.list_project_approvals expected dict, got "
                f"{type(result).__name__}"
            )
        if "error" in result and result.get("error"):
            raise ValueError(str(result["error"]))
        approvals = result.get("approvals", [])
        if not isinstance(approvals, list):
            return []
        return [str(key) for key in approvals]

    # === Hub reads (phase 4.5 step 8) ===

    async def get_hub_status_text(self) -> str:
        """Ask the daemon for the /hub status text rendering."""
        logger.debug("state rpc: get_hub_status_text")
        result = await self._rpc.call(
            "state.get_hub_status_text", {}, timeout=self._timeout
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.get_hub_status_text expected dict, got {type(result).__name__}"
            )
        if "error" in result and result.get("error"):
            raise ValueError(str(result["error"]))
        text = result.get("text", "")
        return str(text)

    async def get_hub_whoami_text(self) -> str:
        """Ask the daemon for the /hub whoami text rendering."""
        logger.debug("state rpc: get_hub_whoami_text")
        result = await self._rpc.call(
            "state.get_hub_whoami_text", {}, timeout=self._timeout
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.get_hub_whoami_text expected dict, got {type(result).__name__}"
            )
        if "error" in result and result.get("error"):
            raise ValueError(str(result["error"]))
        return str(result.get("text", ""))

    async def get_hub_work_text(self) -> str:
        """Ask the daemon for the /hub work text rendering."""
        logger.debug("state rpc: get_hub_work_text")
        result = await self._rpc.call(
            "state.get_hub_work_text", {}, timeout=self._timeout
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.get_hub_work_text expected dict, got {type(result).__name__}"
            )
        if "error" in result and result.get("error"):
            raise ValueError(str(result["error"]))
        return str(result.get("text", ""))

    # === Hub writes (phase 4.6 — attach mode msg/broadcast) ===

    async def hub_send_msg(self, target: str, content: str) -> str:
        """Ask the daemon to send a hub message to a specific agent."""
        logger.debug("state rpc: hub_send_msg target=%r", target)
        result = await self._rpc.call(
            "state.hub_send_msg",
            {"target": target, "content": content},
            timeout=self._timeout,
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.hub_send_msg expected dict, got {type(result).__name__}"
            )
        if "error" in result and result.get("error"):
            raise ValueError(str(result["error"]))
        return str(result.get("text", ""))

    async def hub_broadcast(self, content: str, force: bool = False) -> str:
        """Ask the daemon to broadcast a hub message to all agents."""
        logger.debug("state rpc: hub_broadcast")
        result = await self._rpc.call(
            "state.hub_broadcast",
            {"content": content, "force": force},
            timeout=self._timeout,
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.hub_broadcast expected dict, got {type(result).__name__}"
            )
        if "error" in result and result.get("error"):
            raise ValueError(str(result["error"]))
        return str(result.get("text", ""))

    # === Resume (phase 4.5 step 7) ===

    async def cancel_current_request(self) -> dict[str, Any]:
        """Ask the daemon to cancel the currently processing LLM request."""
        logger.debug("state rpc: cancel_current_request")
        result = await self._rpc.call(
            "state.cancel_current_request",
            {},
            timeout=self._timeout,
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.cancel_current_request expected dict, got {type(result).__name__}"
            )
        if "error" in result and result.get("error"):
            raise ValueError(str(result["error"]))
        return result

    async def resume_conversation(self, session_id: str) -> dict[str, Any]:
        """Ask the daemon to resume a saved conversation by id.

        Returns the metadata dict shape documented on the interface:
        old/new session ids, message count, display header + success
        message, and the list of rendered messages.
        """
        logger.debug("state rpc: resume_conversation id=%r", session_id)
        result = await self._rpc.call(
            "state.resume_conversation",
            {"session_id": session_id},
            timeout=self._timeout,
        )
        if not isinstance(result, dict):
            raise TypeError(
                f"state.resume_conversation expected dict, got {type(result).__name__}"
            )
        if "error" in result and result.get("error"):
            raise ValueError(str(result["error"]))
        return result
