"""LocalStateService: in-process StateService implementation.

Wraps the existing in-process services (llm_service, profile_manager, etc.)
and exposes them behind the StateService protocol. This is the "native" code
path - no RPC, no serialization, just direct attribute reads and method calls
that get wrapped in snapshot DTOs.

This implementation is used in two places:
  1. Local `kollab` mode - the single-process TUI talks to it directly
  2. Daemon side of `kollab --detached` - RPC handlers (kollabor/state/handlers.py)
     call into this implementation, then serialize the snapshots to JSON
     for the wire.

Either way, there's exactly ONE place where the business logic lives.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from .context import ContextListSnapshot, ConversationContext
from .context_registry import ContextRegistry
from .interface import StateService
from .snapshots import (
    AgentListSnapshot,
    AgentSnapshot,
    ConversationSnapshot,
    HubPeer,
    HubSnapshot,
    McpServerInfo,
    McpSnapshot,
    MessageDto,
    PermissionSnapshot,
    ProcessingSnapshot,
    ProfileListSnapshot,
    ProfileSnapshot,
    SessionStats,
    SkillInfo,
    SkillListSnapshot,
    SystemInfoSnapshot,
    SystemPromptSnapshot,
)

logger = logging.getLogger(__name__)


class LocalStateService(StateService):
    """In-process StateService backed by the existing kollabor services.

    Dependencies are injected at construction time. Callers must provide
    references to llm_service and profile_manager - other services are
    added in later phases.

    All methods are async for protocol compatibility with RemoteStateService,
    even though most of them don't actually await anything internally.
    """

    def __init__(
        self,
        llm_service: Any,
        profile_manager: Any,
        *,
        permission_manager: Any = None,
        agent_manager: Any = None,
        event_bus: Any = None,
        started_at: float | None = None,
        context_registry: ContextRegistry | None = None,
        context_identity: str | None = None,
    ) -> None:
        import time as _time

        self._llm_service = llm_service
        self._profile_manager = profile_manager
        self._permission_manager = permission_manager
        self._agent_manager = agent_manager
        self._event_bus = event_bus
        self._started_at = started_at if started_at is not None else _time.monotonic()
        # Cached git branch: (value, monotonic_timestamp). The widget path
        # is sync and will call this many times per second, so we fork
        # git at most once per cache_ttl seconds.
        self._git_branch_cache: tuple[str, float] | None = None
        # Phase 4.5 step 6: context registry. Created lazily on first
        # access so tests that don't care about contexts can pass None
        # and existing code paths don't need to know it exists. The
        # identity string picks the persistence file name; defaults to
        # the daemon pid.
        self._context_registry: ContextRegistry | None = context_registry
        self._context_identity = context_identity

    # === Conversation ===

    async def get_conversation(self) -> ConversationSnapshot:
        """Return a snapshot of the current conversation.

        Reads llm_service.conversation_history (List[ConversationMessage])
        and converts each message to a MessageDto with ISO-string timestamps.
        """
        history = getattr(self._llm_service, "conversation_history", None) or []
        messages: list[MessageDto] = []
        for m in history:
            # Timestamp: datetime -> ISO string, fallback to str() or empty
            timestamp_iso = ""
            ts = getattr(m, "timestamp", None)
            if isinstance(ts, datetime):
                timestamp_iso = ts.isoformat()
            elif ts is not None:
                timestamp_iso = str(ts)

            # Defensive copies of mutable fields
            metadata = dict(getattr(m, "metadata", None) or {})
            thinking = getattr(m, "thinking", None)

            messages.append(
                MessageDto(
                    role=getattr(m, "role", "") or "",
                    content=getattr(m, "content", "") or "",
                    timestamp=timestamp_iso,
                    metadata=metadata,
                    thinking=thinking,
                )
            )

        # Session id and started_at come from conversation_manager if present
        conv_mgr = getattr(self._llm_service, "conversation_manager", None)
        session_id = ""
        started_at = ""
        metadata: dict[str, Any] = {}
        if conv_mgr is not None:
            session_id = getattr(conv_mgr, "current_session_id", "") or ""
            conv_meta = getattr(conv_mgr, "conversation_metadata", None) or {}
            started_at = conv_meta.get("started_at", "") or ""
            metadata = dict(conv_meta)

        return ConversationSnapshot(
            messages=messages,
            session_id=session_id,
            started_at=started_at,
            message_count=len(messages),
            metadata=metadata,
        )

    async def save_conversation(self, format: str = "transcript") -> str:
        """Format the conversation as a string.

        Args:
            format: One of "transcript", "markdown", "jsonl", "raw".

        Returns:
            The formatted conversation. The caller chooses the destination.

        Raises:
            ValueError: if the format is not recognized.
        """
        snapshot = await self.get_conversation()

        if format == "transcript":
            return self._format_as_transcript(snapshot)
        if format == "markdown":
            return self._format_as_markdown(snapshot)
        if format == "jsonl":
            return self._format_as_jsonl(snapshot)
        if format == "raw":
            return self._format_as_raw(snapshot)
        raise ValueError(f"unknown save format: {format!r}")

    # === Format helpers ===
    #
    # These mirror plugins/save_conversation_plugin.py _format_as_* methods
    # byte-for-byte. The phase 2 migration goal is that `/save` produces
    # byte-identical output before and after this code is wired in.

    def _format_as_transcript(self, snapshot: ConversationSnapshot) -> str:
        """Plain text format: role section headers and raw content.

        Matches plugins/save_conversation_plugin.py _format_as_transcript.
        """
        lines: list[str] = []

        for msg in snapshot.messages:
            role = msg.role or "unknown"
            content = msg.content or ""

            if role == "system":
                lines.append("--- system_prompt ---")
            elif role == "user":
                lines.append("\n--- user ---")
            elif role == "assistant":
                lines.append("\n--- llm ---")
            else:
                lines.append(f"\n--- {role} ---")

            lines.append(content)

        return "\n".join(lines)

    def _format_as_markdown(self, snapshot: ConversationSnapshot) -> str:
        """Markdown format with metadata block and numbered role headers.

        Matches plugins/save_conversation_plugin.py _format_as_markdown.
        """
        lines: list[str] = ["# Conversation Transcript", ""]

        messages = snapshot.messages
        if messages:
            first_timestamp = messages[0].timestamp or ""
            last_timestamp = messages[-1].timestamp or ""
            lines.append(f"**Started:** {first_timestamp}")
            lines.append(f"**Ended:** {last_timestamp}")
            lines.append(f"**Messages:** {len(messages)}")
            lines.append("")
            lines.append("---")
            lines.append("")

        for i, msg in enumerate(messages):
            role = msg.role or "unknown"
            content = msg.content or ""
            timestamp = msg.timestamp or ""

            if role == "system":
                lines.append("## System Prompt")
                lines.append("")
                lines.append(f"```\n{content}\n```")
            elif role == "user":
                lines.append(f"## User Message {i+1}")
                if timestamp:
                    lines.append(f"*{timestamp}*")
                lines.append("")
                lines.append(content)
            elif role == "assistant":
                lines.append(f"## Assistant Response {i+1}")
                if timestamp:
                    lines.append(f"*{timestamp}*")
                lines.append("")
                lines.append(content)
            else:
                lines.append(f"## {role.title()} {i+1}")
                lines.append("")
                lines.append(content)

            lines.append("")
            lines.append("---")
            lines.append("")

        return "\n".join(lines)

    def _format_as_jsonl(self, snapshot: ConversationSnapshot) -> str:
        """JSON Lines: one JSON object per message, no trailing newline.

        Matches plugins/save_conversation_plugin.py _format_as_jsonl, which
        writes just `{"role": ..., "content": ..., "timestamp": ..., ...}`
        per line and joins with \\n (no trailing newline).
        """
        lines: list[str] = []
        for msg in snapshot.messages:
            # Build dict matching the original plugin's shape exactly.
            # Original emits role, content, timestamp, and optionally thinking.
            msg_dict: dict[str, Any] = {
                "role": msg.role,
                "content": msg.content,
                "timestamp": msg.timestamp or datetime.now().isoformat(),
            }
            if msg.thinking:
                msg_dict["thinking"] = msg.thinking
            lines.append(json.dumps(msg_dict))

        return "\n".join(lines)

    def _format_as_raw(self, snapshot: ConversationSnapshot) -> str:
        """Raw API-ready JSON payload with model, messages, temperature, metadata.

        Matches plugins/save_conversation_plugin.py _format_as_raw_api.
        Reads the active profile's model and temperature via get_model() and
        get_temperature() so env var overrides are honored.
        """
        # Build API messages: only role + content, matching the original.
        api_messages = [
            {"role": m.role, "content": m.content} for m in snapshot.messages
        ]

        # Read model + temperature from active profile via env-aware getters.
        model = "unknown"
        temperature: float = 0.7
        try:
            profile = self._profile_manager.get_active_profile()
            if profile is not None:
                if hasattr(profile, "get_model"):
                    model = profile.get_model() or "unknown"
                else:
                    model = getattr(profile, "model", "") or "unknown"
                if hasattr(profile, "get_temperature"):
                    temperature = float(profile.get_temperature())
                else:
                    temperature = float(getattr(profile, "temperature", 0.7))
        except Exception as e:
            logger.debug(f"could not read active profile for raw save: {e}")

        payload = {
            "model": model,
            "messages": api_messages,
            "temperature": temperature,
            "metadata": {
                "exported_at": datetime.now().isoformat(),
                "message_count": len(snapshot.messages),
                "format": "raw_api_payload",
            },
        }

        return json.dumps(payload, indent=2, ensure_ascii=False)

    # === Session stats ===

    async def get_session_stats(self) -> SessionStats:
        """Read llm_service.session_stats dict and wrap as SessionStats DTO."""
        stats = getattr(self._llm_service, "session_stats", None) or {}

        # Read session_id from conversation_manager for widget display
        conv_mgr = getattr(self._llm_service, "conversation_manager", None)
        _session_id = ""
        if conv_mgr is not None:
            _session_id = getattr(conv_mgr, "current_session_id", "") or ""

        return SessionStats(
            messages=int(stats.get("messages", 0) or 0),
            input_tokens=int(stats.get("input_tokens", 0) or 0),
            output_tokens=int(stats.get("output_tokens", 0) or 0),
            total_input_tokens=int(stats.get("total_input_tokens", 0) or 0),
            total_output_tokens=int(stats.get("total_output_tokens", 0) or 0),
            cache_read_tokens=int(stats.get("cache_read_tokens", 0) or 0),
            thinking_duration=float(stats.get("thinking_duration", 0.0) or 0.0),
            cost_usd=float(stats.get("cost_usd", 0.0) or 0.0),
            total_cost_usd=float(stats.get("total_cost_usd", 0.0) or 0.0),
            session_id=_session_id,
        )

    # === Profile ===

    async def get_active_profile(self) -> ProfileSnapshot:
        """Return the currently active profile as a snapshot."""
        try:
            active = self._profile_manager.get_active_profile()
        except Exception as e:
            logger.debug(f"could not read active profile: {e}")
            return ProfileSnapshot(name="", is_active=False)
        if active is None:
            return ProfileSnapshot(name="", is_active=False)
        return self._profile_to_snapshot(active, is_active=True)

    async def list_profiles(self) -> ProfileListSnapshot:
        """Return the full profile registry with the active one marked."""
        try:
            profiles = self._profile_manager.list_profiles() or []
        except Exception as e:
            logger.debug(f"could not list profiles: {e}")
            profiles = []
        active_name = getattr(self._profile_manager, "active_profile_name", "") or ""
        snapshots = [
            self._profile_to_snapshot(
                p,
                is_active=(getattr(p, "name", "") == active_name),
            )
            for p in profiles
        ]
        return ProfileListSnapshot(active=active_name, profiles=snapshots)

    # === Internal helpers ===

    def _profile_to_snapshot(
        self, profile: Any, is_active: bool = False
    ) -> ProfileSnapshot:
        """Build a ProfileSnapshot from an LLMProfile.

        Uses the env-var-aware getters when available (get_model,
        get_endpoint, get_provider, get_supports_tools, get_temperature)
        and falls back to raw attributes if a getter is missing or raises.
        """
        # model
        try:
            if hasattr(profile, "get_model"):
                model = profile.get_model()
            else:
                model = getattr(profile, "model", "")
        except Exception:
            model = getattr(profile, "model", "")

        # endpoint (base_url)
        try:
            if hasattr(profile, "get_endpoint"):
                endpoint = profile.get_endpoint()
            else:
                endpoint = getattr(profile, "base_url", "")
        except Exception:
            endpoint = getattr(profile, "base_url", "")

        # provider
        try:
            if hasattr(profile, "get_provider"):
                provider = profile.get_provider()
            else:
                provider = getattr(profile, "provider", "")
        except Exception:
            provider = getattr(profile, "provider", "")

        # supports_tools
        try:
            if hasattr(profile, "get_supports_tools"):
                supports_tools = profile.get_supports_tools()
            else:
                supports_tools = getattr(profile, "supports_tools", True)
        except Exception:
            supports_tools = getattr(profile, "supports_tools", True)

        # temperature (env-aware if possible)
        try:
            if hasattr(profile, "get_temperature"):
                temperature = float(profile.get_temperature())
            else:
                temperature = float(getattr(profile, "temperature", 0.7))
        except Exception:
            temperature = float(getattr(profile, "temperature", 0.7) or 0.7)

        return ProfileSnapshot(
            name=getattr(profile, "name", "") or "",
            model=(model or "") if model is not None else "",
            provider=(provider or "") if provider is not None else "",
            endpoint=(endpoint or "") if endpoint is not None else "",
            supports_tools=bool(supports_tools),
            temperature=temperature,
            description=getattr(profile, "description", "") or "",
            is_active=is_active,
        )

    # === Permissions ===

    async def get_permission_state(self) -> PermissionSnapshot:
        """Read permission manager state into a PermissionSnapshot.

        Accepts the permission manager from either the explicit
        constructor kwarg or the event bus. approval_mode may be an enum
        or a plain string depending on how it was stored; we normalize
        both to the enum .name when possible, str() otherwise.
        """
        pm = self._permission_manager
        if pm is None and self._event_bus is not None:
            try:
                pm = self._event_bus.get_service("permission_manager")
            except Exception as e:
                logger.debug(f"event_bus.get_service(permission_manager) error: {e}")
                pm = None
        if pm is None:
            return PermissionSnapshot()
        try:
            approval_mode = getattr(pm, "approval_mode", None)
            if approval_mode is None:
                mode_str = "DEFAULT"
            elif hasattr(approval_mode, "name"):
                mode_str = str(approval_mode.name)
            else:
                mode_str = str(approval_mode)

            session_approvals = getattr(pm, "session_approvals", None) or {}
            project_approvals = getattr(pm, "project_approvals", None) or {}
            raw_stats = getattr(pm, "stats", None) or {}
            # Coerce stats values to int defensively; widgets assume counters.
            stats: dict[str, int] = {}
            for k, v in raw_stats.items():
                try:
                    stats[str(k)] = int(v)
                except (TypeError, ValueError):
                    continue
            return PermissionSnapshot(
                approval_mode=mode_str,
                session_approvals_count=len(session_approvals),
                project_approvals_count=len(project_approvals),
                stats=stats,
            )
        except Exception as e:
            logger.debug(f"get_permission_state error: {e}")
            return PermissionSnapshot()

    # === MCP ===

    async def get_mcp_state(self) -> McpSnapshot:
        """Read MCP integration state from llm_service.mcp_integration.

        Reads server_connections (dict[name, Connection]) and tool_registry
        (dict[tool_name, {"server": str, "definition": ...}]) and folds
        them into per-server tool counts plus aggregate totals.
        """
        mcp_integration = getattr(self._llm_service, "mcp_integration", None)
        if mcp_integration is None:
            return McpSnapshot()
        try:
            connections = getattr(mcp_integration, "server_connections", None) or {}
            tool_registry = getattr(mcp_integration, "tool_registry", None) or {}

            # Build per-server tool name lists in a single pass instead of
            # N^2 scanning the registry for each server.
            tools_by_server: dict[str, list[str]] = {}
            for tool_name, tool_entry in tool_registry.items():
                if not isinstance(tool_entry, dict):
                    continue
                server_name = tool_entry.get("server", "")
                if not server_name:
                    continue
                tools_by_server.setdefault(str(server_name), []).append(str(tool_name))

            servers: list[McpServerInfo] = []
            connected_count = 0
            for name, conn in connections.items():
                is_initialized = bool(getattr(conn, "initialized", False))
                if is_initialized:
                    connected_count += 1
                tool_names = sorted(tools_by_server.get(str(name), []))
                servers.append(
                    McpServerInfo(
                        name=str(name),
                        enabled=True,
                        connected=is_initialized,
                        tool_count=len(tool_names),
                        tools=tool_names,
                    )
                )

            return McpSnapshot(
                total_servers=len(connections),
                total_tools=len(tool_registry),
                connected_servers=connected_count,
                servers=servers,
            )
        except Exception as e:
            logger.debug(f"get_mcp_state error: {e}")
            return McpSnapshot()

    # === Hub ===

    async def get_hub_state(self) -> HubSnapshot:
        """Read hub plugin state via event_bus.get_service('hub_plugin').

        Pulls _identity (AgentRuntime-like with .identity/.agent_id/
        .is_coordinator) and _roster (list of dicts with identity/state/
        current_task/is_coordinator). If the hub plugin is not present
        (plugins not loaded, or hub disabled), returns an empty snapshot.
        """
        if self._event_bus is None:
            return HubSnapshot()
        try:
            hub = self._event_bus.get_service("hub_plugin")
        except Exception as e:
            logger.debug(f"event_bus.get_service(hub_plugin) error: {e}")
            return HubSnapshot()
        if hub is None:
            return HubSnapshot()
        try:
            identity_obj = getattr(hub, "_identity", None)
            roster = getattr(hub, "_roster", None) or []

            my_identity = ""
            my_agent_id = ""
            my_is_coordinator = False
            if identity_obj is not None:
                my_identity = getattr(identity_obj, "identity", "") or ""
                my_agent_id = getattr(identity_obj, "agent_id", "") or ""
                my_is_coordinator = bool(getattr(identity_obj, "is_coordinator", False))

            peers: list[HubPeer] = []
            for entry in roster:
                if not isinstance(entry, dict):
                    continue
                peers.append(
                    HubPeer(
                        identity=str(entry.get("identity", "") or ""),
                        state=str(entry.get("state", "") or ""),
                        current_task=str(entry.get("current_task", "") or ""),
                        is_coordinator=bool(entry.get("is_coordinator", False)),
                    )
                )

            return HubSnapshot(
                my_identity=my_identity,
                my_agent_id=my_agent_id,
                my_is_coordinator=my_is_coordinator,
                peer_count=len(peers),
                roster=peers,
            )
        except Exception as e:
            logger.debug(f"get_hub_state error: {e}")
            return HubSnapshot()

    # === Processing ===

    async def get_processing_state(self) -> ProcessingSnapshot:
        """Read LLM processing / queue / task / circuit breaker state.

        Sources:
          - llm_service.is_processing
          - llm_service.status_service: queue_size, queue_max,
            dropped_messages, circuit_breaker_state
          - llm_service.pending_tools (question gate suspended tools)
          - llm_service.task_manager._background_tasks
        """
        try:
            is_processing = bool(getattr(self._llm_service, "is_processing", False))

            queue_size = 0
            queue_max = 0
            dropped_messages = 0
            circuit_state = "closed"
            status_service = getattr(self._llm_service, "status_service", None)
            if status_service is not None:
                try:
                    queue_size = int(getattr(status_service, "queue_size", 0) or 0)
                except (TypeError, ValueError):
                    queue_size = 0
                try:
                    queue_max = int(getattr(status_service, "queue_max", 0) or 0)
                except (TypeError, ValueError):
                    queue_max = 0
                try:
                    dropped_messages = int(
                        getattr(status_service, "dropped_messages", 0) or 0
                    )
                except (TypeError, ValueError):
                    dropped_messages = 0
                circuit_state = str(
                    getattr(status_service, "circuit_breaker_state", "closed")
                    or "closed"
                )

            pending_tools = getattr(self._llm_service, "pending_tools", None) or []
            try:
                pending_tools_count = len(pending_tools)
            except TypeError:
                pending_tools_count = 0

            bg_count = 0
            task_mgr = getattr(self._llm_service, "task_manager", None)
            if task_mgr is not None:
                tasks = getattr(task_mgr, "_background_tasks", None) or []
                try:
                    bg_count = len(tasks)
                except TypeError:
                    bg_count = 0

            return ProcessingSnapshot(
                is_processing=is_processing,
                queue_size=queue_size,
                queue_max=queue_max,
                dropped_messages=dropped_messages,
                pending_tools_count=pending_tools_count,
                bg_tasks_count=bg_count,
                circuit_breaker_state=circuit_state,
            )
        except Exception as e:
            logger.debug(f"get_processing_state error: {e}")
            return ProcessingSnapshot()

    # === System ===

    async def get_system_info(self) -> SystemInfoSnapshot:
        """Read daemon-level system info.

        cwd / git branch describe the daemon's working directory (which
        in attach mode is what the client wants to see). terminal session
        count is from the TmuxPlugin service (subprocess-based, not actual
        tmux). pid + uptime come from the process itself. Phase 4.5 step 7
        adds python/platform/architecture and command-registry counts so
        the /status modal renders the daemon's view in attach mode.
        """
        import os as _os
        import platform as _platform
        import time as _time
        from pathlib import Path as _Path

        try:
            cwd = str(_Path.cwd())
        except Exception as e:
            logger.debug(f"get_system_info: cwd lookup failed: {e}")
            cwd = ""
        git_branch = self._resolve_git_branch(cwd) if cwd else ""

        tmux_count = 0
        if self._event_bus is not None:
            try:
                tmux_plugin = self._event_bus.get_service("tmux_plugin")
            except Exception as e:
                logger.debug(f"event_bus.get_service(tmux_plugin) error: {e}")
                tmux_plugin = None
            if tmux_plugin is not None:
                sessions = getattr(tmux_plugin, "sessions", None) or {}
                try:
                    tmux_count = len(sessions)
                except TypeError:
                    tmux_count = 0

        try:
            daemon_pid = int(_os.getpid())
        except Exception:
            daemon_pid = 0
        try:
            uptime = float(_time.monotonic() - self._started_at)
        except Exception:
            uptime = 0.0

        # Phase 4.5 step 7: platform / interpreter / registry stats
        try:
            python_version = _platform.python_version()
        except Exception:
            python_version = ""
        try:
            platform_name = _platform.system()
        except Exception:
            platform_name = ""
        try:
            platform_arch = _platform.machine()
        except Exception:
            platform_arch = ""

        total_commands = 0
        enabled_commands = 0
        command_categories = 0
        if self._event_bus is not None:
            try:
                command_registry = self._event_bus.get_service("command_registry")
            except Exception:
                command_registry = None
            if command_registry is not None and hasattr(
                command_registry, "get_registry_stats"
            ):
                try:
                    stats = command_registry.get_registry_stats() or {}
                    total_commands = int(stats.get("total_commands", 0))
                    enabled_commands = int(stats.get("enabled_commands", 0))
                    command_categories = int(stats.get("categories", 0))
                except Exception as e:
                    logger.debug(f"command_registry stats error: {e}")

        plugin_count = 0
        if self._event_bus is not None:
            try:
                plugin_registry = self._event_bus.get_service("plugin_registry")
            except Exception:
                plugin_registry = None
            if plugin_registry is not None:
                try:
                    plugins = getattr(plugin_registry, "plugins", None)
                    if plugins is not None:
                        plugin_count = len(plugins)
                except Exception as e:
                    logger.debug(f"plugin_registry count error: {e}")

        return SystemInfoSnapshot(
            cwd=cwd,
            git_branch=git_branch,
            tmux_sessions_count=tmux_count,
            daemon_pid=daemon_pid,
            daemon_uptime_seconds=uptime,
            python_version=python_version,
            platform_name=platform_name,
            platform_arch=platform_arch,
            total_commands=total_commands,
            enabled_commands=enabled_commands,
            command_categories=command_categories,
            plugin_count=plugin_count,
        )

    # === Writes (phase 4) ===

    async def set_active_profile(
        self,
        name: str,
        *,
        persist: bool = False,
        persist_local: bool = False,
    ) -> ProfileSnapshot:
        """Switch the active LLM profile and trigger a provider reinitialize.

        Mirrors the existing `/profile set` behavior in the system command
        handler: delegates to profile_manager.set_active_profile, then
        kicks off background re-initialization of the provider and
        native tool registry so the new profile's model/endpoint takes
        effect without a restart.

        Args:
            name: Profile name registered in the profile manager.
            persist: If True, also persist the profile to config (mirrors
                the legacy --save flag). Default False for the common
                case (/profile set).
            persist_local: If True with persist, save to local project
                config instead of global (mirrors --save --local).
                Default False. Ignored when persist is False.

        Raises ValueError if the profile name is not registered.
        Returns a ProfileSnapshot of the new active profile on success.
        """
        if self._profile_manager is None:
            raise ValueError("profile manager not available")
        ok = bool(self._profile_manager.set_active_profile(name))
        if not ok:
            # Match the error vocabulary used by the existing /profile handler.
            try:
                available = list(self._profile_manager.get_profile_names())
            except Exception:
                available = []
            raise ValueError(
                f"profile not found: {name!r}"
                + (f" (available: {', '.join(available)})" if available else "")
            )

        profile = self._profile_manager.get_active_profile()

        # Phase 4.5: honor --save / --save --local by persisting the profile
        # to config. Mirrors the legacy path in application.py:187-192.
        if persist and profile is not None:
            try:
                if hasattr(self._profile_manager, "save_profile_values_to_config"):
                    # save_profile_values_to_config signature varies between
                    # builds; we pass keyword-only when supported.
                    import inspect as _inspect

                    save_fn = self._profile_manager.save_profile_values_to_config
                    try:
                        sig = _inspect.signature(save_fn)
                        if "local" in sig.parameters:
                            save_fn(profile, local=persist_local)
                        else:
                            save_fn(profile)
                    except (TypeError, ValueError):
                        save_fn(profile)
                    logger.info(
                        f"Saved profile {name!r} to config (local={persist_local})"
                    )
            except Exception as e:
                logger.warning(f"failed to persist profile {name!r}: {e}")

        # Mirror the legacy /profile set path: reinitialize the provider
        # so new requests use the switched profile, and reload native
        # tools because the new profile may have different
        # supports_tools. These are background tasks so the state-write
        # returns immediately -- subsequent chat turns will see the
        # updated provider.
        llm = self._llm_service
        if (
            llm is not None
            and hasattr(llm, "api_service")
            and hasattr(llm, "create_background_task")
        ):
            try:
                llm.create_background_task(
                    llm.api_service.reinitialize_provider(profile),
                    name="reinitialize_provider",
                )
            except Exception as e:
                logger.debug(f"reinitialize_provider background task error: {e}")
            if hasattr(llm, "_load_native_tools"):
                try:
                    llm.create_background_task(
                        llm._load_native_tools(),
                        name="reload_native_tools",
                    )
                except Exception as e:
                    logger.debug(f"reload_native_tools background task error: {e}")

        return self._profile_to_snapshot(profile, is_active=True)

    async def set_approval_mode(self, mode: str) -> PermissionSnapshot:
        """Change the tool permission approval mode and return the new snapshot.

        Accepts the canonical enum names ("DEFAULT", "CONFIRM_ALL",
        "AUTO_APPROVE_EDITS", "TRUST_ALL") plus the aliases "strict"
        (-> CONFIRM_ALL) and "trust" (-> TRUST_ALL) so the RPC path
        matches what /permissions strict and /permissions trust already
        send. Case-insensitive.

        Raises ValueError if the mode name is not recognized or the
        permission manager is not available.
        """
        from kollabor_events.permissions_models import ApprovalMode

        pm = self._permission_manager
        if pm is None and self._event_bus is not None:
            pm = self._event_bus.get_service("permission_manager")
        if pm is None:
            raise ValueError("permission manager not available")

        raw = (mode or "").strip()
        if not raw:
            raise ValueError("approval mode cannot be empty")

        alias_map = {
            "DEFAULT": ApprovalMode.DEFAULT,
            "CONFIRM_ALL": ApprovalMode.CONFIRM_ALL,
            "CONFIRMALL": ApprovalMode.CONFIRM_ALL,
            "STRICT": ApprovalMode.CONFIRM_ALL,
            "AUTO_APPROVE_EDITS": ApprovalMode.AUTO_APPROVE_EDITS,
            "AUTO": ApprovalMode.AUTO_APPROVE_EDITS,
            "TRUST_ALL": ApprovalMode.TRUST_ALL,
            "TRUST": ApprovalMode.TRUST_ALL,
            "TRUSTALL": ApprovalMode.TRUST_ALL,
        }
        enum_val = alias_map.get(raw.upper())
        if enum_val is None:
            raise ValueError(
                f"unknown approval mode: {mode!r} "
                f"(expected DEFAULT, CONFIRM_ALL, AUTO_APPROVE_EDITS, or TRUST_ALL)"
            )

        pm.set_approval_mode(enum_val)
        # Re-read to build the new snapshot -- set_approval_mode may
        # persist side-effects (config save) that we want reflected.
        return await self.get_permission_state()

    # === Agents (phase 4.5) ===

    def _sync_bundle_scope(self, agent) -> None:
        """Sync the active agent's tool list to the tool executor's bundle scope.

        When an agent has a 'tools' field in agent.json, only those tools
        are allowed. When agent is None or has no tools field, all tools
        are allowed (legacy default).
        """
        if self._llm_service is None:
            return
        tool_executor = getattr(self._llm_service, "tool_executor", None)
        if tool_executor is None:
            return
        if not hasattr(tool_executor, "set_bundle_scope"):
            return

        if agent is None:
            tool_executor.clear_bundle_scope()
            return

        tools = getattr(agent, "tools", [])
        if tools:
            tool_executor.set_bundle_scope(tools)
        else:
            tool_executor.clear_bundle_scope()

    def _agent_to_snapshot(
        self, agent: Any, *, is_active: bool = False
    ) -> AgentSnapshot:
        """Build an AgentSnapshot from an AgentRuntime (or Agent).

        Tolerates both the legacy Agent dataclass and the AgentRuntime
        wrapper -- they expose the same field names for the subset
        the snapshot needs.
        """
        if agent is None:
            return AgentSnapshot(name="", is_active=False)

        name = getattr(agent, "name", "") or ""
        description = getattr(agent, "description", "") or ""
        profile = getattr(agent, "profile", "") or ""
        source = getattr(agent, "source", "") or ""

        # default_skills: names list (always a list of strings)
        try:
            defaults = list(getattr(agent, "default_skills", []) or [])
        except Exception:
            defaults = []

        # active_skills: names list
        try:
            actives = list(getattr(agent, "active_skills", []) or [])
        except Exception:
            actives = []

        # available skills: from the agent's skills dict (keys)
        try:
            skills_dict = getattr(agent, "skills", {}) or {}
            availables = list(skills_dict.keys())
        except Exception:
            availables = []

        return AgentSnapshot(
            name=name,
            description=description,
            profile=profile,
            active_skills=actives,
            available_skills=availables,
            default_skills=defaults,
            is_active=is_active,
            source=source,
        )

    async def get_active_agent(self) -> AgentSnapshot:
        """Return the active agent as a snapshot, empty snapshot if none."""
        if self._agent_manager is None:
            return AgentSnapshot(name="", is_active=False)
        try:
            agent = self._agent_manager.get_active_agent()
        except Exception as e:
            logger.debug(f"get_active_agent error: {e}")
            return AgentSnapshot(name="", is_active=False)
        if agent is None:
            return AgentSnapshot(name="", is_active=False)
        return self._agent_to_snapshot(agent, is_active=True)

    async def list_agents(self) -> AgentListSnapshot:
        """List all registered agents with the active one marked."""
        if self._agent_manager is None:
            return AgentListSnapshot(active="", agents=[])
        try:
            agents = self._agent_manager.list_agents() or []
        except Exception as e:
            logger.debug(f"list_agents error: {e}")
            agents = []
        try:
            active_name = getattr(self._agent_manager, "_active_agent_name", "") or ""
        except Exception:
            active_name = ""
        snapshots = [
            self._agent_to_snapshot(
                a, is_active=(getattr(a, "name", "") == active_name)
            )
            for a in agents
        ]
        return AgentListSnapshot(active=active_name, agents=snapshots)

    async def set_agent(self, name: str) -> AgentSnapshot:
        """Switch the active agent by name, rebuild the system prompt.

        Mirrors the existing /agent set behavior in kollabor/commands/
        system_commands/handlers/agent_actions.py: calls
        agent_manager.set_active_agent, then llm_service.rebuild_system_prompt
        so the new agent's prompt + default skills take effect without
        a restart.

        Raises ValueError if the agent name is not registered.
        """
        if self._agent_manager is None:
            raise ValueError("agent manager not available")
        if not name or not isinstance(name, str):
            raise ValueError("agent name is required")
        name = name.strip()
        if not name:
            raise ValueError("agent name is required")

        ok = bool(self._agent_manager.set_active_agent(name))
        if not ok:
            try:
                available = list(self._agent_manager.get_agent_names())
            except Exception:
                available = []
            raise ValueError(
                f"agent not found: {name!r}"
                + (f" (available: {', '.join(available)})" if available else "")
            )

        # Rebuild system prompt so the new agent's prompt + default skills
        # take effect for the NEXT turn. Mirrors agent_actions.py.
        llm = self._llm_service
        if llm is not None and hasattr(llm, "rebuild_system_prompt"):
            try:
                result = llm.rebuild_system_prompt()
                # rebuild_system_prompt may be sync or async depending on impl.
                # Await only if it returned a coroutine.
                import asyncio as _aio
                import inspect as _inspect

                if _inspect.iscoroutine(result):
                    await result
                elif _aio.isfuture(result):
                    await result
            except Exception as e:
                logger.warning(f"rebuild_system_prompt error: {e}")

        agent = self._agent_manager.get_active_agent()

        # Sync bundle scope to tool executor
        self._sync_bundle_scope(agent)

        return self._agent_to_snapshot(agent, is_active=True)

    async def clear_agent(self) -> AgentSnapshot:
        """Clear the active agent and rebuild the system prompt.

        Returns the snapshot of the agent that was cleared (or an empty
        snapshot if none was active). After clearing, the next turn
        uses the default system prompt.
        """
        if self._agent_manager is None:
            return AgentSnapshot(name="", is_active=False)
        try:
            previous = self._agent_manager.get_active_agent()
        except Exception:
            previous = None
        self._agent_manager.clear_active_agent()

        # Clear bundle scope when agent is cleared
        self._sync_bundle_scope(None)

        llm = self._llm_service
        if llm is not None and hasattr(llm, "rebuild_system_prompt"):
            try:
                import asyncio as _aio
                import inspect as _inspect

                result = llm.rebuild_system_prompt()
                if _inspect.iscoroutine(result):
                    await result
                elif _aio.isfuture(result):
                    await result
            except Exception as e:
                logger.warning(f"rebuild_system_prompt error: {e}")

        return self._agent_to_snapshot(previous, is_active=False)

    # === Skills (phase 4.5) ===

    def _skill_to_info(self, skill: Any, *, active: bool = False) -> SkillInfo:
        """Build a SkillInfo from a Skill dataclass."""
        content = getattr(skill, "content", "") or ""
        return SkillInfo(
            name=getattr(skill, "name", "") or "",
            description=getattr(skill, "description", "") or "",
            active=active,
            source=getattr(skill, "source", "") or "",
            content_length=len(content),
        )

    async def list_skills(self, agent_name: str = "") -> SkillListSnapshot:
        """List skills for an agent (active agent if name is empty)."""
        if self._agent_manager is None:
            return SkillListSnapshot(agent_name="", skills=[])

        target_name = (agent_name or "").strip()
        agent: Any
        if target_name:
            agent = self._agent_manager.get_agent(target_name)
            if agent is None:
                return SkillListSnapshot(agent_name=target_name, skills=[])
        else:
            agent = self._agent_manager.get_active_agent()
            if agent is None:
                return SkillListSnapshot(agent_name="", skills=[])
            target_name = getattr(agent, "name", "") or ""

        # Build the skill list from agent.skills (Dict[str, Skill])
        try:
            skills_dict = getattr(agent, "skills", {}) or {}
            active_set = set(getattr(agent, "active_skills", []) or [])
        except Exception:
            skills_dict = {}
            active_set = set()

        infos: list[SkillInfo] = []
        for skill_name, skill in skills_dict.items():
            infos.append(self._skill_to_info(skill, active=(skill_name in active_set)))

        return SkillListSnapshot(agent_name=target_name, skills=infos)

    async def _inject_skill_content(
        self, skill_name: str, content: str, *, activate: bool
    ) -> None:
        """Inject a skill activation/deactivation marker into the conversation.

        When a skill is loaded, the agent needs to be told (as a new
        conversation turn) that a skill is now active, otherwise it
        has no way to know about the skill's instructions. This mirrors
        the existing pattern in /skills load which calls
        llm_service._add_conversation_message() directly, but we route
        through the canonical primitive and acquire the hub plugin's
        history lock (if present) to avoid racing hub message injection.
        """
        llm = self._llm_service
        if llm is None:
            return

        verb = "loaded" if activate else "unloaded"
        header = f"[skill {verb}: {skill_name}]"
        if activate and content:
            body = f"{header}\n\n{content}"
        else:
            body = header

        # Prefer the canonical inject_system_message primitive if present.
        inject_fn = getattr(llm, "inject_system_message", None)

        # Acquire the hub plugin's lock if available so we don't race hub
        # message injection (phase 4.5 inventory: hub.plugin:1777 appends
        # with _history_lock held; any new injection path that doesn't
        # take the same lock can interleave frames).
        hub_lock = None
        if self._event_bus is not None:
            try:
                hub_plugin = self._event_bus.get_service("hub_plugin")
                if hub_plugin is not None:
                    hub_lock = getattr(hub_plugin, "_history_lock", None)
            except Exception:
                hub_lock = None

        async def _do_inject() -> None:
            if inject_fn is not None:
                try:
                    result = inject_fn(body, subtype="skill_activation")
                    import inspect as _inspect

                    if _inspect.iscoroutine(result):
                        await result
                    return
                except Exception as e:
                    logger.debug(f"inject_system_message failed: {e}")

            # Fallback: direct append to conversation_history. This path
            # is exercised only if the daemon's llm_service doesn't expose
            # inject_system_message (older builds, test harnesses).
            history = getattr(llm, "conversation_history", None)
            if history is None:
                return
            try:
                from kollabor_events.data_models import ConversationMessage

                history.append(
                    ConversationMessage(
                        role="user",
                        content=body,
                        metadata={
                            "skill_activation": skill_name,
                            "subtype": "skill_activation",
                        },
                    )
                )
            except Exception as e:
                logger.debug(f"conversation_history append failed: {e}")

        if hub_lock is not None:
            async with hub_lock:
                await _do_inject()
        else:
            await _do_inject()

    async def activate_skill(self, name: str) -> SkillListSnapshot:
        """Load a skill on the active agent and inject its content."""
        if self._agent_manager is None:
            raise ValueError("agent manager not available")
        if not name or not isinstance(name, str):
            raise ValueError("skill name is required")
        name = name.strip()
        if not name:
            raise ValueError("skill name is required")

        agent = self._agent_manager.get_active_agent()
        if agent is None:
            raise ValueError("no active agent to load skill onto")

        skills_dict = getattr(agent, "skills", {}) or {}
        if name not in skills_dict:
            available = list(skills_dict.keys())
            raise ValueError(
                f"skill not found on active agent: {name!r}"
                + (f" (available: {', '.join(available)})" if available else "")
            )

        # load_skill is sync on AgentRuntime proxy. Returns bool.
        try:
            ok = bool(agent.load_skill(name))
        except Exception as e:
            raise ValueError(f"failed to load skill {name!r}: {e}") from e
        if not ok:
            raise ValueError(f"failed to load skill {name!r}")

        # Inject the skill content so the agent is aware of the skill.
        skill = skills_dict.get(name)
        content = getattr(skill, "content", "") if skill is not None else ""
        await self._inject_skill_content(name, content, activate=True)

        return await self.list_skills()

    async def deactivate_skill(self, name: str) -> SkillListSnapshot:
        """Unload a skill from the active agent and inject a marker."""
        if self._agent_manager is None:
            raise ValueError("agent manager not available")
        if not name or not isinstance(name, str):
            raise ValueError("skill name is required")
        name = name.strip()
        if not name:
            raise ValueError("skill name is required")

        agent = self._agent_manager.get_active_agent()
        if agent is None:
            raise ValueError("no active agent to unload skill from")

        active_set = set(getattr(agent, "active_skills", []) or [])
        if name not in active_set:
            raise ValueError(f"skill not active: {name!r}")

        try:
            ok = bool(agent.unload_skill(name))
        except Exception as e:
            raise ValueError(f"failed to unload skill {name!r}: {e}") from e
        if not ok:
            raise ValueError(f"failed to unload skill {name!r}")

        await self._inject_skill_content(name, "", activate=False)

        return await self.list_skills()

    # === System prompt (phase 4.5) ===

    async def get_system_prompt(self) -> SystemPromptSnapshot:
        """Return the currently active rendered system prompt."""
        llm = self._llm_service
        if llm is None:
            return SystemPromptSnapshot(source="default", content="", size_chars=0)

        # llm_service.get_current_system_prompt / system_prompt attribute
        # varies by build. Try several known shapes.
        content = ""
        source = "default"
        path = ""
        try:
            if hasattr(llm, "get_current_system_prompt"):
                result = llm.get_current_system_prompt()
                import inspect as _inspect

                if _inspect.iscoroutine(result):
                    content = str(await result) or ""
                else:
                    content = str(result) or ""
            elif hasattr(llm, "system_prompt"):
                content = str(getattr(llm, "system_prompt", "") or "")
        except Exception as e:
            logger.debug(f"get_system_prompt read error: {e}")
            content = ""

        # Determine source via known attributes.
        try:
            if getattr(llm, "_cli_system_prompt_file", None):
                source = "file"
                path = str(llm._cli_system_prompt_file)
            elif getattr(llm, "_system_prompt_source", "") == "env":
                source = "env"
            elif self._agent_manager is not None:
                active = self._agent_manager.get_active_agent()
                if active is not None and getattr(active, "system_prompt", ""):
                    source = "agent"
        except Exception:
            pass

        return SystemPromptSnapshot(
            source=source,
            path=path,
            content=content,
            size_chars=len(content),
            rendered_tags=[],
        )

    async def set_system_prompt(
        self, content: str, *, source: str = "file", path: str = ""
    ) -> SystemPromptSnapshot:
        """Install a new system prompt on the daemon.

        Expects the caller to have already read the file content in the
        client's cwd. The daemon does not touch the filesystem here.
        """
        if content is None:
            raise ValueError("system prompt content is required")
        if not isinstance(content, str):
            raise ValueError(
                f"system prompt content must be a string, got {type(content).__name__}"
            )
        # Sanity limit: 1 MB. A legitimate system prompt is under 100 KB.
        MAX_SIZE = 1_048_576
        if len(content) > MAX_SIZE:
            raise ValueError(
                f"system prompt too large: {len(content)} bytes (max {MAX_SIZE})"
            )
        if not content.strip():
            raise ValueError("system prompt content is empty")

        llm = self._llm_service
        if llm is None:
            raise ValueError("llm service not available")

        # Install via the known llm_service surface. We try several shapes
        # because the exact API varies by build.
        installed = False
        try:
            if hasattr(llm, "set_system_prompt_content"):
                result = llm.set_system_prompt_content(content)
                import inspect as _inspect

                if _inspect.iscoroutine(result):
                    await result
                installed = True
        except Exception as e:
            logger.debug(f"set_system_prompt_content error: {e}")
        if not installed:
            try:
                if hasattr(llm, "system_prompt"):
                    llm.system_prompt = content
                    installed = True
            except Exception as e:
                logger.debug(f"system_prompt assign error: {e}")
        if not installed:
            # Last resort: poke into conversation history's system message
            # directly. If even that fails, surface the error.
            raise ValueError("llm_service does not expose a way to set system prompt")

        # Rebuild the system prompt so the conversation reflects the change.
        if hasattr(llm, "rebuild_system_prompt"):
            try:
                import asyncio as _aio
                import inspect as _inspect

                result = llm.rebuild_system_prompt()
                if _inspect.iscoroutine(result):
                    await result
                elif _aio.isfuture(result):
                    await result
            except Exception as e:
                logger.debug(f"rebuild_system_prompt error after set: {e}")

        return SystemPromptSnapshot(
            source=source,
            path=path,
            content=content,
            size_chars=len(content),
            rendered_tags=[],
        )

    # === Contexts (phase 4.5 step 6) ===

    def set_context_identity(self, identity: str) -> None:
        """Set the identity used to pick the context persistence file.

        Called by the hub plugin after it assigns a gem name, so the
        context registry uses a stable per-daemon filename
        (for example, ``contexts/jarvis.json`` under the active hub dir) instead of the
        pid-based fallback. Safe to call multiple times -- only the
        FIRST call that arrives before the registry is created has
        an effect. After the registry exists, changing identity
        would orphan the on-disk file, so subsequent calls are
        ignored with a debug log.
        """
        if not isinstance(identity, str) or not identity.strip():
            return
        identity = identity.strip()
        if self._context_registry is not None:
            logger.debug(
                "set_context_identity: registry already created with "
                "identity=%s, ignoring new identity=%s",
                self._context_identity,
                identity,
            )
            return
        self._context_identity = identity
        logger.info(f"state: context identity set to {identity!r}")

    def _get_or_create_registry(self) -> ContextRegistry:
        """Return the ContextRegistry, creating it on first access.

        Lazy initialization so tests and code paths that never touch
        contexts don't pay the cost of building the registry + loading
        from disk. The registry needs a reference to llm_service for
        its snapshot-and-swap operations, which is already held.
        """
        if self._context_registry is None:
            if self._llm_service is None:
                raise ValueError("cannot create context registry without llm_service")
            self._context_registry = ContextRegistry(
                self._llm_service,
                identity=self._context_identity,
            )
        return self._context_registry

    async def list_contexts(
        self, *, include_archived: bool = False
    ) -> ContextListSnapshot:
        """Delegate to the registry."""
        reg = self._get_or_create_registry()
        return reg.list_all(include_archived=include_archived)

    async def get_active_context(self) -> ConversationContext:
        """Return the currently live context snapshot."""
        reg = self._get_or_create_registry()
        ctx = reg.get_context(reg.get_active_name())
        if ctx is None:
            # Shouldn't happen -- registry always has at least main.
            raise ValueError(
                f"active context not found in registry: " f"{reg.get_active_name()!r}"
            )
        return ctx

    async def create_context(
        self,
        name: str,
        *,
        profile_name: str = "",
        agent_name: str = "",
        system_prompt: str = "",
    ) -> ConversationContext:
        """Delegate to the registry.

        Raises ValueError on empty/duplicate/invalid name -- the
        handler catches these and returns an error envelope.
        """
        reg = self._get_or_create_registry()
        return await reg.create(
            name,
            profile_name=profile_name,
            agent_name=agent_name,
            system_prompt=system_prompt,
        )

    async def attach_to_context(self, name: str) -> ConversationContext:
        """Delegate to the registry.

        On success, also rebuilds the system prompt so the switched
        context's prompt takes effect for the next turn. Mirrors the
        pattern in set_active_profile / set_agent.
        """
        reg = self._get_or_create_registry()
        ctx = await reg.attach_to(name)

        # Rebuild the system prompt so the context's profile/agent
        # changes propagate to the next turn. Best-effort; errors
        # don't roll back the switch.
        llm = self._llm_service
        if llm is not None and hasattr(llm, "rebuild_system_prompt"):
            try:
                import asyncio as _aio
                import inspect as _inspect

                result = llm.rebuild_system_prompt()
                if _inspect.iscoroutine(result):
                    await result
                elif _aio.isfuture(result):
                    await result
            except Exception as e:
                logger.warning(
                    f"rebuild_system_prompt after context switch failed: {e}"
                )

        return ctx

    async def archive_context(self, name: str) -> ConversationContext:
        """Delegate to the registry."""
        reg = self._get_or_create_registry()
        return await reg.archive(name)

    # === Session management (phase 4.5 step 7) ===

    async def restart_session(self) -> ConversationSnapshot:
        """Clear the live conversation and rebuild the system prompt.

        Mirrors kollabor/commands/system_commands/handlers/system.py
        handle_restart: clears conversation_manager, re-initializes
        llm_service conversation, and returns the post-restart snapshot.

        Used by /restart in both local and attach mode.

        Raises:
            ValueError: if llm_service is not available.
        """
        llm = self._llm_service
        if llm is None:
            raise ValueError("llm service not available")

        # Best-effort: save current conversation before clearing.
        conv_mgr = getattr(llm, "conversation_manager", None)
        if conv_mgr is not None:
            try:
                conv_mgr.clear_conversation()
            except Exception as e:
                logger.debug(f"restart_session: clear_conversation error: {e}")

        # Re-initialize conversation (clears history, rebuilds system prompt).
        # The method is async in llm_coordinator.
        if hasattr(llm, "_initialize_conversation"):
            try:
                import asyncio as _aio
                import inspect as _inspect

                result = llm._initialize_conversation()
                if _inspect.iscoroutine(result):
                    await result
                elif _aio.isfuture(result):
                    await result
            except Exception as e:
                logger.warning(f"restart_session: _initialize_conversation failed: {e}")

        # Return a fresh conversation snapshot.
        return await self.get_conversation()

    # === MCP writes (phase 4.5 step 7) ===

    def _mcp_manager(self) -> Any:
        """Create a fresh MCPManager instance.

        MCPManager is stateless -- it wraps a settings file path and all
        operations re-read/re-write the file. Creating one per call is
        cheaper than holding a reference on the state service.
        """
        from kollabor_agent import MCPManager

        return MCPManager()

    async def enable_mcp_server(self, name: str) -> McpSnapshot:
        """Enable an MCP server by flipping `enabled = True` in the settings file.

        Phase 4.5 step 7: no hot-reload -- caller surfaces "restart to
        apply" in the success message. This mirrors /profile set which
        also requires a provider reinit; the symmetric choice here is to
        punt the subprocess lifecycle rabbit hole to phase 4.6 and ship
        the predictable file-write path now.
        """
        mgr = self._mcp_manager()
        result = mgr.enable_server(name)
        if not result.get("success"):
            err = result.get("error") or f"failed to enable MCP server {name!r}"
            raise ValueError(err)
        logger.info(f"MCP server {name!r} enabled via state_service")
        return await self.get_mcp_state()

    async def disable_mcp_server(self, name: str) -> McpSnapshot:
        """Disable an MCP server by flipping `enabled = False` in the settings file.

        Phase 4.5 step 7: no hot-unload -- caller surfaces "restart to
        apply" in the success message.
        """
        mgr = self._mcp_manager()
        result = mgr.disable_server(name)
        if not result.get("success"):
            err = result.get("error") or f"failed to disable MCP server {name!r}"
            raise ValueError(err)
        logger.info(f"MCP server {name!r} disabled via state_service")
        return await self.get_mcp_state()

    async def test_mcp_server(self, name: str) -> dict[str, Any]:
        """Return live status for an MCP server.

        Mirrors MCPManager.get_server_status: reads the config file for
        enabled state and cross-references llm_service.mcp_integration
        for connection/tool-count. Raises ValueError if the server isn't
        configured, which the command handler translates into a readable
        error message matching the legacy path.
        """
        mgr = self._mcp_manager()
        mcp_integration = getattr(self._llm_service, "mcp_integration", None)
        status = mgr.get_server_status(name, mcp_integration)
        if not status.get("found"):
            err = status.get("error") or f"server {name!r} not found in configuration"
            raise ValueError(err)
        return status

    async def get_mcp_tools(
        self, server_filter: str | None = None
    ) -> dict[str, list[dict[str, Any]]]:
        """Return MCP tools grouped by server.

        Delegates to MCPManager.list_tools which already groups by server,
        shapes each tool into {name, description}, and honors the
        server_filter argument. The return type matches the legacy dict
        format so /mcp tools can render from it unchanged.
        """
        mgr = self._mcp_manager()
        mcp_integration = getattr(self._llm_service, "mcp_integration", None)
        if mcp_integration is None:
            return {}
        try:
            result = mgr.list_tools(mcp_integration, server_filter)
            tools_by_server = result.get("tools", {})
            if not isinstance(tools_by_server, dict):
                return {}
            # Defensive copy + shape normalization: ensure every value is
            # a list of dicts so remote callers can rely on the structure.
            normalized: dict[str, list[dict[str, Any]]] = {}
            for server_name, tools in tools_by_server.items():
                if not isinstance(tools, list):
                    continue
                normalized[str(server_name)] = [
                    dict(t) for t in tools if isinstance(t, dict)
                ]
            return normalized
        except Exception as e:
            logger.debug(f"get_mcp_tools error: {e}")
            return {}

    async def reload_mcp_servers(self) -> dict[str, Any]:
        """Hot-reload MCP config and reconnect enabled servers."""
        mcp_integration = getattr(self._llm_service, "mcp_integration", None)
        if mcp_integration is None:
            raise ValueError("MCP integration not available")

        if hasattr(mcp_integration, "reload_mcp_servers"):
            summary = await mcp_integration.reload_mcp_servers()
        else:
            await mcp_integration.shutdown()
            mcp_integration.mcp_servers.clear()
            mcp_integration._load_mcp_config()
            discovered = await mcp_integration.discover_mcp_servers()
            summary = {
                "configured": len(mcp_integration.mcp_servers),
                "discovered": len(discovered) if isinstance(discovered, dict) else 0,
                "reconnected": len(mcp_integration.server_connections),
            }

        snapshot = await self.get_mcp_state()
        result = dict(summary)
        result["snapshot"] = snapshot.to_dict()
        return result

    # === Permission writes + project reads (phase 4.5 step 7) ===

    def _resolve_permission_manager(self) -> Any:
        """Look up the active PermissionManager instance.

        Prefers the one handed in at construction time, falls back to
        event_bus.get_service('permission_manager') for the daemon path
        where the manager is registered after state_service init. This
        mirrors _resolve_permission_manager in set_approval_mode.
        """
        pm = self._permission_manager
        if pm is None and self._event_bus is not None:
            pm = self._event_bus.get_service("permission_manager")
        if pm is None:
            raise ValueError("permission manager not available")
        return pm

    async def clear_session_approvals(self) -> PermissionSnapshot:
        """Clear session-scoped approvals and return the updated snapshot.

        Phase 4.5 step 7: mirrors PermissionManager.clear_session_approvals.
        The post-clear snapshot is generated by re-reading state so the
        caller sees session_approvals_count = 0 without a follow-up rpc.
        """
        pm = self._resolve_permission_manager()
        try:
            pm.clear_session_approvals()
        except Exception as e:
            raise ValueError(f"failed to clear session approvals: {e}")
        logger.info("session approvals cleared via state_service")
        return await self.get_permission_state()

    async def clear_project_approvals(self) -> PermissionSnapshot:
        """Clear project-scoped approvals and return the updated snapshot.

        Phase 4.5 step 7: mirrors PermissionManager.clear_project_approvals.
        """
        pm = self._resolve_permission_manager()
        try:
            pm.clear_project_approvals()
        except Exception as e:
            raise ValueError(f"failed to clear project approvals: {e}")
        logger.info("project approvals cleared via state_service")
        return await self.get_permission_state()

    async def list_project_approvals(self) -> list[str]:
        """Return the list of project-scoped approval keys.

        Phase 4.5 step 7: mirrors PermissionManager.get_project_approvals.
        Returns an empty list if the manager can't be resolved rather
        than raising, so /permissions project renders "no approvals"
        instead of erroring when state_service is used in a stripped-
        down context.
        """
        try:
            pm = self._resolve_permission_manager()
        except ValueError:
            return []
        try:
            approvals = pm.get_project_approvals()
        except Exception as e:
            logger.debug(f"list_project_approvals error: {e}")
            return []
        if not isinstance(approvals, list):
            return []
        return [str(key) for key in approvals]

    # === Hub reads (phase 4.5 step 8) ===

    def _resolve_hub_plugin(self) -> Any:
        """Look up the hub plugin instance or return None.

        The hub plugin registers itself on the event bus as 'hub_plugin'
        during _initialize_plugins. Returns None if it isn't registered
        (hub disabled in config, or plugins haven't initialized yet).
        """
        if self._event_bus is None:
            return None
        try:
            return self._event_bus.get_service("hub_plugin")
        except Exception:
            return None

    async def get_hub_status_text(self) -> str:
        """Return HubPlugin._format_status output verbatim."""
        hub = self._resolve_hub_plugin()
        if hub is None:
            return ""
        fn = getattr(hub, "_format_status", None)
        if fn is None:
            return ""
        try:
            result = fn()
            return str(result) if result is not None else ""
        except Exception as e:
            logger.debug(f"hub _format_status error: {e}")
            return ""

    async def get_hub_whoami_text(self) -> str:
        """Return HubPlugin._format_whoami output verbatim."""
        hub = self._resolve_hub_plugin()
        if hub is None:
            return ""
        fn = getattr(hub, "_format_whoami", None)
        if fn is None:
            return ""
        try:
            result = fn()
            return str(result) if result is not None else ""
        except Exception as e:
            logger.debug(f"hub _format_whoami error: {e}")
            return ""

    async def get_hub_work_text(self) -> str:
        """Return HubPlugin._format_work output verbatim."""
        hub = self._resolve_hub_plugin()
        if hub is None:
            return ""
        fn = getattr(hub, "_format_work", None)
        if fn is None:
            return ""
        try:
            result = fn()
            return str(result) if result is not None else ""
        except Exception as e:
            logger.debug(f"hub _format_work error: {e}")
            return ""

    # === Hub writes (phase 4.6 — attach mode msg/broadcast) ===

    async def hub_send_msg(self, target: str, content: str) -> str:
        """Delegate to HubPlugin._handle_msg_command."""
        hub = self._resolve_hub_plugin()
        if hub is None:
            return "hub: not connected"
        fn = getattr(hub, "_handle_msg_command", None)
        if fn is None:
            return "hub: msg handler not available"
        try:
            return str(await fn(f"{target} {content}"))
        except Exception as e:
            logger.debug(f"hub _handle_msg_command error: {e}")
            return f"hub msg error: {e}"

    async def hub_broadcast(self, content: str, force: bool = False) -> str:
        """Delegate to HubPlugin._handle_broadcast_command."""
        hub = self._resolve_hub_plugin()
        if hub is None:
            return "hub: not connected"
        fn = getattr(hub, "_handle_broadcast_command", None)
        if fn is None:
            return "hub: broadcast handler not available"
        try:
            return str(await fn(content, force=force))
        except Exception as e:
            logger.debug(f"hub _handle_broadcast_command error: {e}")
            return f"hub broadcast error: {e}"

    # === Cancel (phase 4.6) ===

    async def cancel_current_request(self) -> dict[str, Any]:
        """Cancel the currently processing LLM request.

        Delegates to LLMCoordinator.cancel_current_request(). Safe to
        call when nothing is processing -- it's a no-op in that case.
        """
        llm = self._llm_service
        if llm is None:
            return {"cancelled": False, "reason": "no llm service"}
        was_processing = getattr(llm, "is_processing", False)
        if hasattr(llm, "cancel_current_request"):
            llm.cancel_current_request()
        return {"cancelled": was_processing}

    # === Resume (phase 4.5 step 7) ===

    async def resume_conversation(self, session_id: str) -> dict[str, Any]:
        """Load a saved session and swap it into the live conversation.

        Phase 4.5 step 7: daemon-side resume. Mirrors the legacy
        ResumeConversationPlugin _load_conversation + _load_and_display_session
        flow but uses the in-place list replacement so QueueProcessor,
        SessionManager, and the hub plugin keep their cached references
        valid (the bug fixed in commit 4b42a6f).

        Returns a plain dict rather than a typed ConversationSnapshot
        because the caller needs extra metadata (old/new session ids,
        display-ready header/success lines) that don't belong on the
        generic snapshot shape.
        """
        llm = self._llm_service
        if llm is None:
            raise ValueError("llm service not available")

        conv_mgr = getattr(llm, "conversation_manager", None)
        if conv_mgr is None:
            raise ValueError("conversation manager not available")

        # Auto-save current conversation if it has messages.
        old_session_id = getattr(conv_mgr, "current_session_id", "") or ""
        try:
            has_messages = bool(getattr(conv_mgr, "messages", None))
            if has_messages and hasattr(conv_mgr, "save_conversation"):
                conv_mgr.save_conversation()
                logger.info(f"resume: auto-saved session {old_session_id!r}")
        except Exception as e:
            logger.debug(f"resume: auto-save error (non-fatal): {e}")

        # Load the target session.
        try:
            loaded = conv_mgr.load_session(session_id)
        except Exception as e:
            raise ValueError(f"failed to load session {session_id!r}: {e}")
        if not loaded:
            raise ValueError(f"failed to load session {session_id!r}")

        raw_messages = getattr(conv_mgr, "messages", None) or []

        # Generate a fresh session id for the resumed conversation.
        try:
            from kollabor_ai.session_naming import generate_session_name

            new_session_id = generate_session_name()
            conv_mgr.current_session_id = new_session_id
        except Exception as e:
            logger.debug(f"resume: session naming error: {e}")
            new_session_id = session_id  # fall back to reused id

        # Convert raw dicts to ConversationMessage objects.
        try:
            from kollabor_events.data_models import ConversationMessage
        except Exception as e:
            raise ValueError(f"ConversationMessage import failed: {e}")

        loaded_messages: list[Any] = []
        display_messages: list[dict[str, Any]] = []
        for msg in raw_messages:
            if not isinstance(msg, dict):
                continue
            role = str(msg.get("role", "user"))
            content = str(msg.get("content", ""))
            loaded_messages.append(ConversationMessage(role=role, content=content))
            if role in ("user", "assistant"):
                display_messages.append({"role": role, "content": content})

        # Replace history IN PLACE (list-identity preservation).
        history = getattr(llm, "conversation_history", None)
        if history is None:
            raise ValueError("llm.conversation_history missing")
        if hasattr(history, "clear") and hasattr(history, "extend"):
            history.clear()
            history.extend(loaded_messages)
        else:
            # Shouldn't happen in production; log and assign.
            logger.warning(
                "resume: conversation_history is not a mutable list; "
                "direct assignment (list identity lost)"
            )
            llm.conversation_history = loaded_messages  # type: ignore[misc]

        # Update session_stats so widgets reading it see the new count.
        if hasattr(llm, "session_stats"):
            try:
                llm.session_stats["messages"] = len(loaded_messages)
            except Exception as e:
                logger.debug(f"resume: session_stats update error: {e}")

        header = f"--- Resumed: {session_id[:20]}... as {new_session_id} ---"
        success_message = f"[ok] Resumed: {new_session_id}. Continue below."

        return {
            "old_session_id": old_session_id,
            "new_session_id": new_session_id,
            "message_count": len(loaded_messages),
            "header": header,
            "success_message": success_message,
            "messages": display_messages,
        }

    def _resolve_git_branch(self, cwd: str, cache_ttl: float = 60.0) -> str:
        """Cached git branch lookup. Returns empty string on failure.

        Runs `git rev-parse --abbrev-ref HEAD` in a subprocess with a 1
        second timeout. Result is cached for ``cache_ttl`` seconds so
        repeated calls from the refresher loop don't fork git every tick.
        """
        import subprocess as _sub
        import time as _time

        now = _time.monotonic()
        if self._git_branch_cache is not None:
            cached_value, cached_at = self._git_branch_cache
            if now - cached_at < cache_ttl:
                return cached_value

        branch = ""
        try:
            result = _sub.run(
                ["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True,
                text=True,
                timeout=1.0,
            )
            if result.returncode == 0:
                branch = (result.stdout or "").strip()
        except Exception as e:
            logger.debug(f"_resolve_git_branch subprocess error: {e}")
            branch = ""

        self._git_branch_cache = (branch, now)
        return branch
