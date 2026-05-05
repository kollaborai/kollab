"""StateService protocol - the single abstraction for reading and writing
daemon state.

LocalStateService (in-process) and RemoteStateService (RPC-backed) both
implement this protocol. Commands and widgets depend on the protocol,
never on a specific implementation, so code works identically in local
mode and attach mode.

Phase 2 includes only conversation and save methods. Phase 3 will add
profile/permissions/mcp/hub read methods. Phase 4 will add write methods.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .context import ContextListSnapshot, ConversationContext
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


@runtime_checkable
class StateService(Protocol):
    """The unified state access protocol.

    All methods are async so that remote implementations can await RPC calls
    without imposing sync constraints on callers. Local implementations
    should wrap sync reads in plain `async def` even if they don't await
    anything internally.
    """

    # === Conversation ===

    async def get_conversation(self) -> ConversationSnapshot:
        """Return the current conversation history as a snapshot.

        Used by /save and any other command that needs the full message list.
        The snapshot is a point-in-time copy; subsequent calls may return
        different data if the conversation is ongoing.
        """
        ...

    async def save_conversation(self, format: str = "transcript") -> str:
        """Format and return the conversation as a string.

        Args:
            format: One of "transcript", "markdown", "jsonl", "raw".

        Returns:
            The formatted conversation as a string. The CALLER is responsible
            for choosing a destination (clipboard, local file, project snapshots
            dir) and writing the bytes. This split exists because destinations
            are client-side context - even in attach mode, the file system or
            clipboard the user wants to save to is the client's.

        Raises:
            ValueError: if the format is not recognized.
        """
        ...

    # === Session stats ===

    async def get_session_stats(self) -> SessionStats:
        """Return current LLM session statistics.

        Used by status widgets that show message count and token usage.
        """
        ...

    # === Profile ===

    async def get_active_profile(self) -> ProfileSnapshot:
        """Return the currently active LLM profile."""
        ...

    async def list_profiles(self) -> ProfileListSnapshot:
        """Return the full profile registry with the active one marked."""
        ...

    # === Permissions ===

    async def get_permission_state(self) -> PermissionSnapshot:
        """Return the current tool permission manager state."""
        ...

    # === MCP ===

    async def get_mcp_state(self) -> McpSnapshot:
        """Return the current MCP server/tool state."""
        ...

    # === Hub ===

    async def get_hub_state(self) -> HubSnapshot:
        """Return the current hub plugin identity + roster state."""
        ...

    # === Processing ===

    async def get_processing_state(self) -> ProcessingSnapshot:
        """Return the current LLM processing / queue / task state."""
        ...

    # === System ===

    async def get_system_info(self) -> SystemInfoSnapshot:
        """Return daemon-level system info (cwd, git branch, pid, uptime)."""
        ...

    # === Writes (phase 4) ===
    #
    # Writes are synchronous from the caller's perspective: the user
    # initiates an action, expects it to complete, and sees confirmation.
    # They go straight to the daemon (no client-side cache). Each returns
    # the NEW snapshot reflecting the post-write state so callers can
    # display an updated view without a follow-up read.

    async def set_active_profile(
        self,
        name: str,
        *,
        persist: bool = False,
        persist_local: bool = False,
    ) -> ProfileSnapshot:
        """Switch to a different LLM profile by name.

        Args:
            name: Profile name registered in the profile manager.
            persist: If True, also persist the profile to config (mirrors
                the legacy --save flag). Default False.
            persist_local: If True with persist, save to local project
                config instead of global (mirrors --save --local).

        Returns:
            ProfileSnapshot of the newly-active profile.

        Raises:
            ValueError: if the profile name doesn't exist.
        """
        ...

    async def set_approval_mode(self, mode: str) -> PermissionSnapshot:
        """Change the tool permission approval mode.

        Args:
            mode: One of "DEFAULT", "CONFIRM_ALL", "AUTO_APPROVE_EDITS",
                "TRUST_ALL". Case-insensitive; aliases "strict" and "trust"
                are accepted for backward compatibility.

        Returns:
            PermissionSnapshot reflecting the updated mode.

        Raises:
            ValueError: if the mode name is not recognized.
        """
        ...

    # === Agents (phase 4.5) ===

    async def get_active_agent(self) -> AgentSnapshot:
        """Return the currently active agent as a snapshot.

        Returns an empty AgentSnapshot (name="") when no agent is active.
        """
        ...

    async def list_agents(self) -> AgentListSnapshot:
        """Return the full agent registry with the active one marked."""
        ...

    async def set_agent(self, name: str) -> AgentSnapshot:
        """Switch to a different agent by name.

        The daemon resolves the agent, swaps in its system prompt, default
        skills, and preferred profile (if set), and rebuilds the active
        system prompt. New requests use the switched agent.

        Args:
            name: Agent name from the agent registry.

        Returns:
            AgentSnapshot of the newly active agent.

        Raises:
            ValueError: if the agent name doesn't exist.
        """
        ...

    async def clear_agent(self) -> AgentSnapshot:
        """Clear the active agent, reverting to the default system prompt.

        Returns:
            AgentSnapshot of the agent that was cleared (or an empty
            snapshot if no agent was active).
        """
        ...

    # === Skills (phase 4.5) ===

    async def list_skills(self, agent_name: str = "") -> SkillListSnapshot:
        """Return all skills available to an agent.

        Args:
            agent_name: Optional agent name. When empty, lists skills
                for the currently active agent.

        Returns:
            SkillListSnapshot with every skill's name, description, and
            whether it's currently active on the target agent.
        """
        ...

    async def activate_skill(self, name: str) -> SkillListSnapshot:
        """Load a skill onto the active agent.

        Skill activation injects the skill's content into the conversation
        as a user-role message, so future turns are aware of the skill's
        instructions. The daemon acquires the hub plugin's history lock
        (if hub is present) before appending to avoid racing against
        hub message injection.

        Args:
            name: Skill name known to the active agent.

        Returns:
            SkillListSnapshot reflecting the updated skill state for the
            active agent.

        Raises:
            ValueError: if no active agent, or the skill name isn't
                registered for the active agent.
        """
        ...

    async def deactivate_skill(self, name: str) -> SkillListSnapshot:
        """Unload a skill from the active agent.

        Args:
            name: Skill name currently loaded on the active agent.

        Returns:
            SkillListSnapshot reflecting the updated skill state.

        Raises:
            ValueError: if no active agent, or the skill isn't loaded.
        """
        ...

    # === System prompt (phase 4.5) ===

    async def get_system_prompt(self) -> SystemPromptSnapshot:
        """Return the currently active system prompt as a snapshot.

        The snapshot contains the fully rendered content (post-trender
        expansion). Use this sparingly in widgets -- the content can be
        several KB.
        """
        ...

    async def set_system_prompt(
        self, content: str, *, source: str = "file", path: str = ""
    ) -> SystemPromptSnapshot:
        """Install a new system prompt on the daemon.

        In attach mode the client reads the --system-prompt file from
        its OWN cwd (because the daemon's cwd may differ) and sends the
        content string over RPC. The daemon installs it without touching
        the filesystem.

        Args:
            content: Full system prompt content.
            source: Provenance tag -- "file", "env", or "inline".
                Stored on the snapshot for /status and debugging.
            path: Optional absolute path the content came from. Stored
                for display only; the daemon does not re-read this file.

        Returns:
            SystemPromptSnapshot reflecting the newly installed prompt.

        Raises:
            ValueError: if content is empty or exceeds the size limit
                (currently 1 MB -- prompt files larger than this are
                almost certainly a mistake).
        """
        ...

    # === Contexts (phase 4.5 step 6) ===

    async def list_contexts(
        self, *, include_archived: bool = False
    ) -> ContextListSnapshot:
        """Return all conversation contexts in the daemon's registry.

        The live context is marked in the returned snapshot's
        ``active`` field. Archived contexts are hidden by default.
        """
        ...

    async def get_active_context(self) -> ConversationContext:
        """Return a snapshot of the currently live conversation context.

        Includes the full message history.
        """
        ...

    async def create_context(
        self,
        name: str,
        *,
        profile_name: str = "",
        agent_name: str = "",
        system_prompt: str = "",
    ) -> ConversationContext:
        """Create a new empty conversation context in the registry.

        The new context is NOT automatically activated. Use
        attach_to_context to make it live.

        Raises:
            ValueError: if the name is empty, already exists, or
                contains invalid characters (path separators, spaces,
                shell metacharacters, leading dot).
        """
        ...

    async def attach_to_context(self, name: str) -> ConversationContext:
        """Switch the live context to the named one.

        Snapshots the current live conversation history back into the
        registry, loads the target context's history into the live
        LLMCoordinator, and marks the target as live.

        Raises:
            ValueError: if the name doesn't exist or is archived.
            RuntimeError: if a turn is in progress on the current
                live context (llm_service.is_processing is True).
        """
        ...

    async def archive_context(self, name: str) -> ConversationContext:
        """Soft-delete a conversation context.

        Archived contexts are hidden from list_contexts by default
        but their history is preserved on disk.

        Raises:
            ValueError: if the name doesn't exist or is the currently
                live context (attach to another first).
        """
        ...

    # === Session management (phase 4.5 step 7) ===

    async def restart_session(self) -> ConversationSnapshot:
        """Clear the live conversation and rebuild the system prompt.

        Mirrors the legacy /restart command: drops the current
        conversation history and re-initializes with a fresh system
        prompt from the active agent/profile. The newly-restarted
        conversation snapshot is returned so the caller can display
        the post-restart state.
        """
        ...

    # === MCP writes (phase 4.5 step 7) ===
    #
    # MCP write methods mutate ~/.kollab/mcp/mcp_settings.json on
    # the daemon side. Hot-reload of MCP server subprocesses is deferred
    # to phase 4.6; these methods update the file and expect a restart
    # to apply. Each returns the updated McpSnapshot so callers don't
    # need a follow-up get_mcp_state() read.

    async def enable_mcp_server(self, name: str) -> McpSnapshot:
        """Enable a configured MCP server.

        Sets `servers[name].enabled = True` in the MCP settings file.
        Does NOT hot-reload the server subprocess; caller should surface
        a "restart to apply" message in the success output. Phase 4.6
        may add hot-reload if the tradeoff is worth the complexity.

        Args:
            name: MCP server name as it appears in mcp_settings.json.

        Returns:
            Updated McpSnapshot with the new enabled flag reflected.

        Raises:
            ValueError: if no config exists or the server isn't in it.
        """
        ...

    async def disable_mcp_server(self, name: str) -> McpSnapshot:
        """Disable a configured MCP server.

        Sets `servers[name].enabled = False` in the MCP settings file.
        Does NOT hot-unload the server subprocess; caller should surface
        a "restart to apply" message.

        Args:
            name: MCP server name as it appears in mcp_settings.json.

        Returns:
            Updated McpSnapshot.

        Raises:
            ValueError: if no config exists or the server isn't in it.
        """
        ...

    async def test_mcp_server(self, name: str) -> dict[str, Any]:
        """Test connection to an MCP server and return its status.

        Read-only equivalent of the legacy `/mcp test <name>` command.
        Returns a dict with `found`, `enabled`, `connected`, `tool_count`,
        and optional `error` keys — shape matches MCPManager.get_server_status
        so the command handler can render from it directly without
        transformation.

        Args:
            name: MCP server name.

        Returns:
            Status dict with the keys above.

        Raises:
            ValueError: if the server isn't found in config.
        """
        ...

    async def get_mcp_tools(
        self, server_filter: str | None = None
    ) -> dict[str, list[dict[str, Any]]]:
        """Return MCP tools grouped by server.

        Mirrors `/mcp tools` — returns {server_name: [{"name": ..., "description": ...}, ...]}.
        When server_filter is provided, returns only that server's tools
        (or an empty dict if it's not registered).

        Args:
            server_filter: Optional server name to limit results to.

        Returns:
            Dict of server_name → list of tool dicts.
        """
        ...

    async def reload_mcp_servers(self) -> dict[str, Any]:
        """Reload MCP config and reconnect enabled servers.

        This is the explicit hot-reload path for `/mcp reload`. Unlike
        enable/disable, which only writes the config file, this method is
        allowed to restart MCP subprocesses and returns summary counts
        plus a serialized `McpSnapshot`.
        """
        ...

    # === Permission writes + project reads (phase 4.5 step 7) ===
    #
    # set_approval_mode is already phase 4. These add the remaining
    # mutations and the project-approval listing so /permissions clear,
    # /permissions clear-project, and /permissions project work in
    # attach mode.

    async def clear_session_approvals(self) -> PermissionSnapshot:
        """Clear all session-scoped tool approvals.

        Mirrors PermissionManager.clear_session_approvals. The snapshot
        returned reflects the post-clear state so callers can confirm
        session_approvals_count dropped to 0.
        """
        ...

    async def clear_project_approvals(self) -> PermissionSnapshot:
        """Clear all project-scoped tool approvals.

        Mirrors PermissionManager.clear_project_approvals. Also wipes
        the project approval file on disk if the manager has one
        configured.
        """
        ...

    async def list_project_approvals(self) -> list[str]:
        """Return the list of project-scoped approval keys.

        Matches PermissionManager.get_project_approvals which returns
        a list of tool/pattern keys the user has approved for this
        project. Used by `/permissions project` for display.
        """
        ...

    # === Hub reads (phase 4.5 step 8) ===
    #
    # /hub status, /hub whoami, /hub work all render pre-formatted text
    # from the hub plugin's internal state. For phase 4.5 step 8 the
    # cheap-win migration returns the rendered strings directly so the
    # client can print them verbatim. A future phase 4.6 migration
    # could return structured data and move rendering to the client.

    async def get_hub_status_text(self) -> str:
        """Return the /hub status rendering as a monospace text blob.

        Mirrors HubPlugin._format_status: agent count, per-agent line,
        and pending work queue summary. Empty string when the hub plugin
        isn't loaded (hub disabled in config).
        """
        ...

    async def get_hub_whoami_text(self) -> str:
        """Return the /hub whoami rendering as a monospace text blob.

        Mirrors HubPlugin._format_whoami: identity, role, agent_id,
        pid, project. Empty string when hub isn't loaded.
        """
        ...

    async def get_hub_work_text(self) -> str:
        """Return the /hub work rendering as a monospace text blob.

        Mirrors HubPlugin._format_work: slot count + per-slot line.
        Empty string when hub isn't loaded.
        """
        ...

    # === Hub writes (phase 4.6 — attach mode msg/broadcast) ===

    async def hub_send_msg(self, target: str, content: str) -> str:
        """Send a hub message to a specific agent via the daemon.

        Delegates to HubPlugin._handle_msg_command on the daemon side.
        Returns the result string (e.g. "sent to lapis" or error text).
        """
        ...

    async def hub_broadcast(self, content: str, force: bool = False) -> str:
        """Broadcast a hub message to all agents via the daemon.

        Delegates to HubPlugin._handle_broadcast_command on the daemon side.
        Returns the result string (e.g. "broadcast to 3 agent(s)").
        """
        ...

    # === Resume (phase 4.5 step 7) ===

    async def resume_conversation(self, session_id: str) -> dict[str, Any]:
        """Resume a prior conversation by session id.

        Daemon-side flow mirrors the legacy ResumeConversationPlugin
        _load_conversation path:

          1. Auto-save the current conversation if it has messages.
          2. Load the target session from disk via conversation_manager.
          3. Convert raw dicts to ConversationMessage objects.
          4. Replace llm_service.conversation_history IN PLACE (via
             clear + extend so QueueProcessor / SessionManager / hub
             keep their cached list references valid).
          5. Generate a new session id for the resumed conversation.
          6. Return a metadata dict the client can render from.

        Args:
            session_id: ID of the session to resume.

        Returns:
            A dict shaped like:
              {
                "old_session_id": str,       # previous session (auto-saved)
                "new_session_id": str,       # freshly generated id
                "message_count": int,        # messages loaded
                "header": str,               # display header
                "success_message": str,      # confirmation line
                "messages": list[dict],      # [{role, content}, ...] for display
              }
            The client renders `header` + `messages` + `success_message`
            through its own message_coordinator -- the daemon does no
            display work.

        Raises:
            ValueError: if conversation_manager isn't available or the
                target session isn't found/loadable.
        """
        ...
