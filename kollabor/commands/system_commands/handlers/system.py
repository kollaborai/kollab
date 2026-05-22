"""System command handler.

Handles /help, /config, /doctor, /status, /permissions, /version, /restart.
"""

import logging
import platform
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from kollabor_events.models import (
    CommandCategory,
    CommandDefinition,
    CommandMode,
    CommandResult,
    SlashCommand,
    SubcommandInfo,
    UIConfig,
)

from ....tool_contract_proof import collect_tool_contract_proofs
from ..base import BaseCommandHandler

logger = logging.getLogger(__name__)


class SystemCommandHandler(BaseCommandHandler):
    """Handles /help, /config, /doctor, /status, /version, /permissions, /restart."""

    MODAL_ACTIONS = set()  # No modal actions for system commands

    @property
    def llm_service(self):
        """Get LLM service via service registry."""
        return self.event_bus.get_service("llm_service")

    @property
    def agent_manager(self):
        """Get agent manager via service registry."""
        return self.event_bus.get_service("agent_manager")

    @property
    def permission_manager(self):
        """Get permission manager via service registry."""
        return self.event_bus.get_service("permission_manager")

    def register_commands(self) -> None:
        """Register system commands."""
        # Register /help command
        help_command = CommandDefinition(
            name="help",
            description="Show available commands and usage",
            handler=self.handle_help,
            plugin_name="system",
            category=CommandCategory.SYSTEM,
            mode=CommandMode.INSTANT,
            aliases=["h", "?"],
            icon="?",
        )
        self.command_registry.register_command(help_command)

        # Register /config command (runs as AltView fullscreen editor)
        config_command = CommandDefinition(
            name="config",
            description="Open system configuration panel",
            handler=self.handle_config,
            plugin_name="system",
            category=CommandCategory.SYSTEM,
            mode=CommandMode.ALTVIEW,
            aliases=["settings", "preferences"],
            icon="[CFG]",
        )
        self.command_registry.register_command(config_command)

        # Register /doctor command
        doctor_command = CommandDefinition(
            name="doctor",
            description="Run a first-run readiness check and harmless proof task",
            handler=self.handle_doctor,
            plugin_name="system",
            category=CommandCategory.SYSTEM,
            mode=CommandMode.INSTANT,
            aliases=["checkup", "ready"],
            icon="[DR]",
            cli_hidden=False,
        )
        self.command_registry.register_command(doctor_command)

        # Register /status command
        status_command = CommandDefinition(
            name="status",
            description="Show system status and diagnostics",
            handler=self.handle_status,
            plugin_name="system",
            category=CommandCategory.SYSTEM,
            mode=CommandMode.STATUS_TAKEOVER,
            aliases=["info", "diagnostics"],
            icon="[STATS]",
            ui_config=UIConfig(
                type="table",
                navigation=["? ?", "Esc"],
                height=12,
                title="System Status",
                footer="↑↓ navigate • Esc exit",
            ),
        )
        self.command_registry.register_command(status_command)

        # Register /permissions command
        permissions_command = CommandDefinition(
            name="permissions",
            description="Manage tool execution permissions",
            handler=self.handle_permissions,
            plugin_name="system",
            category=CommandCategory.SYSTEM,
            mode=CommandMode.INLINE_INPUT,
            aliases=["perms", "security", "permission"],
            icon="[LOCK]",
            cli_hidden=False,  # Allow CLI: kollab --permissions trust
            subcommands=[
                SubcommandInfo("show", "", "Show current permission settings"),
                SubcommandInfo("default", "", "Use DEFAULT mode (HIGH risk only)"),
                SubcommandInfo(
                    "strict", "", "Use CONFIRM_ALL mode (prompt everything)"
                ),
                SubcommandInfo("trust", "", "Use TRUST_ALL mode (approve everything)"),
                SubcommandInfo("stats", "", "Show permission statistics"),
                SubcommandInfo("clear", "", "Clear session approvals"),
            ],
        )
        self.command_registry.register_command(permissions_command)

        # Register /mode command
        mode_command = CommandDefinition(
            name="mode",
            description="Switch terminal contrast mode for dark or light backgrounds",
            handler=self.handle_mode,
            plugin_name="system",
            category=CommandCategory.UI,
            mode=CommandMode.INSTANT,
            aliases=["contrast"],
            icon="[MODE]",
            cli_hidden=False,
            subcommands=[
                SubcommandInfo(
                    "dark", "", "Use light text for dark terminal backgrounds"
                ),
                SubcommandInfo(
                    "light", "", "Use dark text for light terminal backgrounds"
                ),
            ],
        )
        self.command_registry.register_command(mode_command)

        # Register /version command
        version_command = CommandDefinition(
            name="version",
            description="Show application version information",
            handler=self.handle_version,
            plugin_name="system",
            category=CommandCategory.SYSTEM,
            mode=CommandMode.INSTANT,
            aliases=["v", "ver"],
            icon="[INFO]",
        )
        self.command_registry.register_command(version_command)

        # Register /restart command
        restart_command = CommandDefinition(
            name="restart",
            description="Clear conversation and start fresh session",
            handler=self.handle_restart,
            plugin_name="system",
            category=CommandCategory.SYSTEM,
            mode=CommandMode.INSTANT,
            aliases=["new", "clear"],
            icon="[NEW]",
        )
        self.command_registry.register_command(restart_command)

    async def handle_help(self, command: SlashCommand) -> CommandResult:
        """Handle /help command.

        Args:
            command: Parsed slash command.

        Returns:
            Command execution result.
        """
        try:
            if command.args:
                # Show help for specific command
                command_name = command.args[0]
                return await self._show_command_help(command_name)
            else:
                # Show all commands categorized by plugin
                return await self._show_all_commands()

        except Exception as e:
            self.logger.error(f"Error in help command: {e}")
            return CommandResult(
                success=False,
                message=f"Error displaying help: {str(e)}",
                display_type="error",
            )

    async def handle_config(self, command: SlashCommand) -> CommandResult:
        """Handle /config command via ConfigAltView.

        Pushes the config editor AltView onto the stack manager so it
        takes over the terminal alternate buffer with full widget support.

        Args:
            command: Parsed slash command.

        Returns:
            Command execution result.
        """
        try:
            from plugins.altview.config_altview import ConfigAltView

            altview = ConfigAltView()

            # Get or create the stack manager
            stack_mgr = self.event_bus.get_service("altview_stack_manager")
            if not stack_mgr:
                try:
                    from kollabor_tui.altview.stack_manager import (
                        AltViewStackManager,
                    )

                    renderer = self.event_bus.get_service("renderer")
                    stack_mgr = AltViewStackManager(self.event_bus, renderer)
                    self.event_bus.register_service("altview_stack_manager", stack_mgr)
                except Exception as e:
                    logger.error("Failed to create AltView stack manager: %s", e)
                    return CommandResult(
                        success=False,
                        message=f"Config UI unavailable: {e}",
                        display_type="error",
                    )

            # Inject config_service for reading/saving config values
            llm_svc = self.llm_service
            if llm_svc and hasattr(llm_svc, "config"):
                altview.config_service = llm_svc.config
            elif self.config_manager:
                altview.config_service = self.config_manager

            # push() blocks until the user exits
            await stack_mgr.push(altview, "config")

            return CommandResult(
                success=True,
                message="",
                display_type="success",
            )

        except Exception as e:
            self.logger.error(f"Error in config command: {e}")
            return CommandResult(
                success=False,
                message=f"Error opening configuration: {str(e)}",
                display_type="error",
            )

    async def handle_mode(self, command: SlashCommand) -> CommandResult:
        """Handle /mode command for terminal contrast theme switching."""
        try:
            from kollabor_tui.design_system import THEMES, get_theme, set_theme

            if isinstance(command.args, list):
                requested = command.args[0].lower() if command.args else ""
            elif isinstance(command.args, str):
                requested = command.args.strip().lower()
            else:
                requested = ""

            allowed = {"dark", "light"}
            if not requested:
                current = get_theme().name
                return CommandResult(
                    success=True,
                    message=(
                        f"mode: {current}\n"
                        "usage: /mode dark | /mode light\n"
                        "  dark  light text for black/dark terminal backgrounds\n"
                        "  light dark text for white/light terminal backgrounds"
                    ),
                    display_type="info",
                )

            if requested not in allowed or requested not in THEMES:
                return CommandResult(
                    success=False,
                    message="unknown mode. use: /mode dark or /mode light",
                    display_type="error",
                )

            set_theme(requested)
            config_service = self._get_mode_config_service()
            persisted = False
            if config_service is not None:
                saved = config_service.save_key(
                    "kollabor.ui.theme",
                    requested,
                    save_target="global",
                )
                if not saved:
                    return CommandResult(
                        success=False,
                        message=(f"mode set to {requested}, but failed to save it"),
                        display_type="warning",
                        data={"mode": requested, "persisted": False},
                    )
                persisted = True

            message = (
                f"mode set to {requested} and saved"
                if persisted
                else f"mode set to {requested} for this session"
            )
            return CommandResult(
                success=True,
                message=message,
                display_type="success",
                data={"mode": requested, "persisted": persisted},
            )

        except Exception as e:
            self.logger.error(f"Error in mode command: {e}")
            return CommandResult(
                success=False,
                message=f"Error setting mode: {e}",
                display_type="error",
            )

    def _get_mode_config_service(self):
        """Return the ConfigService used to persist terminal contrast mode."""
        if self._config_manager is not None and hasattr(
            self._config_manager, "save_key"
        ):
            return self._config_manager

        llm_service = self.llm_service
        config_service = getattr(llm_service, "config", None)
        if config_service is not None and hasattr(config_service, "save_key"):
            return config_service

        config_manager = self.event_bus.get_service("config_manager")
        if config_manager is not None and hasattr(config_manager, "save_key"):
            return config_manager

        return None

    async def handle_status(self, command: SlashCommand) -> CommandResult:
        """Handle /status command.

        Phase 4.5 step 7: reads all system info from state_service so
        attach mode shows the daemon's view (python version, platform,
        command registry stats) instead of the client's local shadow.
        In local mode state_service reads from in-process services
        directly, so the numbers are the same either way -- just the
        indirection layer is unified.

        Args:
            command: Parsed slash command.

        Returns:
            Command execution result with status modal UI.
        """
        try:
            state_service = None
            if self.event_bus and hasattr(self.event_bus, "get_service"):
                state_service = self.event_bus.get_service("state_service")
            if state_service is None:
                return CommandResult(
                    success=False,
                    message="State service not available -- cannot show status.",
                    display_type="error",
                )

            try:
                sys_info = await state_service.get_system_info()
            except Exception as e:
                self.logger.error(f"state_service.get_system_info failed: {e}")
                return CommandResult(
                    success=False,
                    message=f"Error reading system info: {e}",
                    display_type="error",
                )

            status_definition = await self._build_status_modal_definition(
                sys_info,
                state_service=state_service,
            )

            return CommandResult(
                success=True,
                message="System status opened",
                ui_config=UIConfig(
                    type="modal",
                    title=status_definition["title"],
                    width=status_definition.get("width"),
                    height=int(status_definition.get("height", 20)),
                    modal_config=status_definition,
                ),
                display_type="modal",
            )

        except Exception as e:
            self.logger.error(f"Error in status command: {e}")
            return CommandResult(
                success=False,
                message=f"Error showing status: {str(e)}",
                display_type="error",
            )

    async def handle_doctor(self, command: SlashCommand) -> CommandResult:
        """Handle /doctor command.

        Runs a read-only readiness report for first-run and attach debugging.
        The command deliberately avoids mutating config or starting servers.
        """
        try:
            state_service = None
            if self.event_bus and hasattr(self.event_bus, "get_service"):
                state_service = self.event_bus.get_service("state_service")

            checks: list[dict[str, str]] = []

            def add(status: str, name: str, detail: str, fix: str = "") -> None:
                checks.append(
                    {
                        "status": status,
                        "name": name,
                        "detail": detail,
                        "fix": fix,
                    }
                )

            cwd = Path.cwd()
            git_branch = self._read_git_branch(cwd)
            add("ok", "cwd", str(cwd))
            if git_branch:
                add("ok", "git", f"branch {git_branch}")
            else:
                add("warn", "git", "not in a git worktree", "run from a project repo")

            if state_service is None:
                add(
                    "block",
                    "state service",
                    "not registered",
                    "start the full kollab app, not a bare handler",
                )
            else:
                await self._doctor_state_checks(state_service, add)

            self._doctor_service_checks(add)
            self._doctor_proof_check(cwd, add)
            self._doctor_contract_proof_checks(add)

            message = self._format_doctor_report(checks)
            blocked = any(c["status"] == "block" for c in checks)

            return CommandResult(
                success=not blocked,
                message=message,
                display_type="error" if blocked else "success",
            )
        except Exception as e:
            self.logger.error(f"Error in doctor command: {e}")
            return CommandResult(
                success=False,
                message=f"doctor failed: {e}",
                display_type="error",
            )

    async def _build_status_modal_definition(
        self,
        sys_info: Any,
        *,
        state_service: Any,
    ) -> Dict[str, Any]:
        """Build the /status modal definition from a SystemInfoSnapshot.

        The snapshot already carries python/platform/arch and command
        registry stats (phase 4.5 step 7 extension), so there's no
        fallback to local platform module lookups -- if the daemon
        returned an empty string, we show an empty string.
        """
        profile = await self._maybe_state_snapshot(state_service, "get_active_profile")
        agent = await self._maybe_state_snapshot(state_service, "get_active_agent")
        perm = await self._maybe_state_snapshot(state_service, "get_permission_state")
        proc = await self._maybe_state_snapshot(state_service, "get_processing_state")
        hub = await self._maybe_state_snapshot(state_service, "get_hub_state")
        attach_runtime = self._get_service("attach_runtime_state") or {}
        rpc_client = self._get_service("rpc_client")
        pending_rpc = getattr(rpc_client, "pending_count", 0) if rpc_client else 0
        profile_label = (
            f"{getattr(profile, 'name', '') or 'unknown'} | "
            f"{getattr(profile, 'model', '') or 'unknown'}"
        )

        return {
            "title": "System Status",
            "footer": "Esc to close",
            "sections": [
                {
                    "title": "Runtime",
                    "widgets": [
                        {
                            "type": "label",
                            "label": "Identity",
                            "value": str(attach_runtime.get("identity") or "local"),
                        },
                        {
                            "type": "label",
                            "label": "Socket",
                            "value": str(attach_runtime.get("socket_path") or "local"),
                        },
                        {
                            "type": "label",
                            "label": "Heartbeat",
                            "value": self._format_attach_heartbeat(attach_runtime),
                        },
                        {
                            "type": "label",
                            "label": "Pending RPC",
                            "value": str(pending_rpc),
                        },
                        {
                            "type": "label",
                            "label": "Ctrl+Z",
                            "value": "detach; daemon keeps running",
                        },
                        {
                            "type": "label",
                            "label": "Ctrl+C",
                            "value": "stop attached client and owned daemon",
                        },
                    ],
                },
                {
                    "title": "Session",
                    "widgets": [
                        {
                            "type": "label",
                            "label": "Profile",
                            "value": profile_label,
                        },
                        {
                            "type": "label",
                            "label": "Agent",
                            "value": str(getattr(agent, "name", "") or "unknown"),
                        },
                        {
                            "type": "label",
                            "label": "Permissions",
                            "value": str(
                                getattr(perm, "approval_mode", "") or "unknown"
                            ),
                        },
                        {
                            "type": "label",
                            "label": "Pending Tools",
                            "value": str(getattr(proc, "pending_tools_count", 0) or 0),
                        },
                        {
                            "type": "label",
                            "label": "Hub",
                            "value": (
                                f"{getattr(hub, 'my_identity', '') or 'none'} | "
                                f"{getattr(hub, 'peer_count', 0) or 0} peers"
                            ),
                        },
                    ],
                },
                {
                    "title": "Commands",
                    "widgets": [
                        {
                            "type": "label",
                            "label": "Registered",
                            "value": str(getattr(sys_info, "total_commands", 0)),
                        },
                        {
                            "type": "label",
                            "label": "Enabled",
                            "value": str(getattr(sys_info, "enabled_commands", 0)),
                        },
                        {
                            "type": "label",
                            "label": "Categories",
                            "value": str(getattr(sys_info, "command_categories", 0)),
                        },
                    ],
                },
                {
                    "title": "Plugins",
                    "widgets": [
                        {
                            "type": "label",
                            "label": "Active",
                            "value": str(getattr(sys_info, "plugin_count", 0)),
                        },
                    ],
                },
                {
                    "title": "System",
                    "widgets": [
                        {
                            "type": "label",
                            "label": "Python",
                            "value": str(getattr(sys_info, "python_version", "")),
                        },
                        {
                            "type": "label",
                            "label": "Platform",
                            "value": str(getattr(sys_info, "platform_name", "")),
                        },
                        {
                            "type": "label",
                            "label": "Architecture",
                            "value": str(getattr(sys_info, "platform_arch", "")),
                        },
                        {
                            "type": "label",
                            "label": "Daemon PID",
                            "value": str(getattr(sys_info, "daemon_pid", 0)),
                        },
                        {
                            "type": "label",
                            "label": "Working Directory",
                            "value": str(getattr(sys_info, "cwd", "")),
                        },
                    ],
                },
                {
                    "title": "Services",
                    "widgets": [
                        {"type": "label", "label": "Event Bus", "value": "[ok] Active"},
                        {
                            "type": "label",
                            "label": "Input Handler",
                            "value": "[ok] Running",
                        },
                        {
                            "type": "label",
                            "label": "Terminal Renderer",
                            "value": "[ok] Active",
                        },
                    ],
                },
            ],
            "actions": [
                {
                    "key": "Escape",
                    "label": "Close",
                    "action": "cancel",
                    "style": "secondary",
                }
            ],
        }

    def _get_service(self, name: str) -> Any:
        if self.event_bus and hasattr(self.event_bus, "get_service"):
            try:
                return self.event_bus.get_service(name)
            except Exception:
                return None
        return None

    async def _maybe_state_snapshot(self, state_service: Any, method: str) -> Any:
        try:
            fn = getattr(state_service, method)
            return await fn()
        except Exception:
            return None

    def _format_attach_heartbeat(self, attach_runtime: dict[str, Any]) -> str:
        last = attach_runtime.get("last_heartbeat_at")
        if not last:
            return "none"
        try:
            age = max(0.0, time.time() - float(last))
        except (TypeError, ValueError):
            return "unknown"
        return f"{age:.1f}s ago"

    async def _doctor_state_checks(self, state_service: Any, add: Any) -> None:
        """Collect readiness checks from the unified StateService."""
        try:
            profile = await state_service.get_active_profile()
            name = getattr(profile, "name", "") or ""
            model = getattr(profile, "model", "") or ""
            provider = getattr(profile, "provider", "") or ""
            if name and model:
                add("ok", "profile", f"{name} | {provider or 'provider?'} | {model}")
            elif name:
                add("warn", "profile", f"{name} has no model", "set a model/profile")
            else:
                add("block", "profile", "no active profile", "run /profile list")
        except Exception as e:
            add("block", "profile", f"unreadable: {e}", "check profile config")

        try:
            perm = await state_service.get_permission_state()
            mode = getattr(perm, "approval_mode", "") or "unknown"
            add("ok", "permissions", mode)
        except Exception as e:
            add("warn", "permissions", f"unreadable: {e}", "run /permissions show")

        try:
            mcp = await state_service.get_mcp_state()
            total = int(getattr(mcp, "total_servers", 0) or 0)
            connected = int(getattr(mcp, "connected_servers", 0) or 0)
            tools = int(getattr(mcp, "total_tools", 0) or 0)
            if total == 0:
                add("warn", "mcp", "0 servers configured", "run /mcp add or /mcp show")
            elif connected == 0:
                add(
                    "warn",
                    "mcp",
                    f"{total} configured, 0 connected",
                    "run /mcp test <server>",
                )
            else:
                add("ok", "mcp", f"{connected}/{total} connected, {tools} tools")
        except Exception as e:
            add("warn", "mcp", f"unreadable: {e}", "run /mcp show")

        try:
            hub = await state_service.get_hub_state()
            ident = getattr(hub, "my_identity", "") or ""
            peers = int(getattr(hub, "peer_count", 0) or 0)
            if ident:
                add("ok", "hub", f"{ident}, {peers} peers")
            else:
                add("warn", "hub", "no identity yet", "start with hub enabled")
        except Exception as e:
            add("warn", "hub", f"unreadable: {e}", "run /hub status")

        try:
            agent = await state_service.get_active_agent()
            name = getattr(agent, "name", "") or ""
            if name:
                add("ok", "agent", name)
            else:
                add("warn", "agent", "default/no active agent", "run /agent list")
        except Exception as e:
            add("warn", "agent", f"unreadable: {e}", "run /agent list")

        try:
            sys_info = await state_service.get_system_info()
            pid = int(getattr(sys_info, "daemon_pid", 0) or 0)
            uptime = float(getattr(sys_info, "daemon_uptime_seconds", 0.0) or 0.0)
            if pid:
                add("ok", "daemon", f"pid {pid}, uptime {int(uptime)}s")
            else:
                add("ok", "runtime", "local process")
        except Exception as e:
            add("warn", "runtime", f"unreadable: {e}", "run /status")

    def _doctor_service_checks(self, add: Any) -> None:
        """Collect service registry checks without assuming full app startup."""
        services = {
            "renderer": "terminal renderer missing",
            "command_registry": "command registry missing",
            "permission_manager": "permission manager missing",
            "llm_service": "llm service missing",
        }
        for service_name, missing in services.items():
            try:
                if service_name == "command_registry":
                    service = getattr(self, "command_registry", None)
                else:
                    service = (
                        self.event_bus.get_service(service_name)
                        if self.event_bus and hasattr(self.event_bus, "get_service")
                        else None
                    )
            except Exception:
                service = None
            if service is None:
                add("warn", service_name, missing)
            else:
                add("ok", service_name, "registered")

    def _doctor_proof_check(self, cwd: Path, add: Any) -> None:
        """Run a harmless proof read so /doctor proves local tool viability."""
        candidates = [
            cwd / "pyproject.toml",
            cwd / "README.md",
            cwd / "AGENTS.md",
        ]
        proof = next((p for p in candidates if p.is_file()), None)
        if proof is None:
            add(
                "warn",
                "proof read",
                "no pyproject/readme/agents file found",
                "run from a project root",
            )
            return

        try:
            with proof.open("rb") as fh:
                chunk = fh.read(128)
            if chunk:
                add("ok", "proof read", f"read {proof.name} ({len(chunk)} bytes)")
            else:
                add("warn", "proof read", f"{proof.name} is empty")
        except Exception as e:
            add("block", "proof read", f"failed: {e}", "check file permissions")

    def _doctor_contract_proof_checks(self, add: Any) -> None:
        """Validate XML, native, and MCP tool-call contract normalization."""
        try:
            for name, detail in collect_tool_contract_proofs():
                add("ok", name, detail)
        except Exception as e:
            add("block", "proof contracts", f"failed: {e}", "run tool contract tests")

    def _read_git_branch(self, cwd: Path) -> str:
        """Read the current git branch with a bounded, read-only probe."""
        git = shutil.which("git")
        if not git:
            return ""
        try:
            proc = subprocess.run(
                [git, "branch", "--show-current"],
                cwd=str(cwd),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=1.5,
                check=False,
            )
        except Exception:
            return ""
        return (proc.stdout or "").strip()

    def _format_doctor_report(self, checks: list[dict[str, str]]) -> str:
        counts = {
            "ok": sum(1 for c in checks if c["status"] == "ok"),
            "warn": sum(1 for c in checks if c["status"] == "warn"),
            "block": sum(1 for c in checks if c["status"] == "block"),
        }
        if counts["block"]:
            verdict = "blocked"
        elif counts["warn"]:
            verdict = "degraded"
        else:
            verdict = "ready"

        icon = {"ok": "[ok]", "warn": "[warn]", "block": "[block]"}
        lines = [
            "kollab doctor:",
            f"  verdict: {verdict}",
            f"  checks: {counts['ok']} ok, {counts['warn']} warn, {counts['block']} blocked",
            "",
        ]
        for check in checks:
            line = (
                f"  {icon[check['status']]} {check['name']:<18} " f"{check['detail']}"
            )
            lines.append(line.rstrip())
            if check["fix"]:
                lines.append(f"       fix: {check['fix']}")
        return "\n".join(lines)

    async def handle_permissions(self, command: SlashCommand) -> CommandResult:
        """Handle /permissions command.

        Args:
            command: Parsed slash command.

        Returns:
            Command execution result.
        """
        try:
            # Get permission manager via service registry
            permission_manager = self.permission_manager
            if not permission_manager:
                return CommandResult(
                    success=False,
                    message="Permission system not initialized",
                    display_type="error",
                )

            # Handle args (can be list or string)
            if isinstance(command.args, list):
                subcommand = command.args[0].lower() if command.args else "show"
            elif isinstance(command.args, str):
                subcommand = command.args.strip().lower() if command.args else "show"
            else:
                subcommand = "show"

            # Handle subcommands
            if subcommand in ("", "show"):
                # Show current settings.
                # Phase 3 migration: prefer state_service (works in local AND
                # attach mode). Fall back to direct permission_manager read if
                # state_service isn't wired for any reason.
                mode_str = ""
                stats = {}
                state_service = None
                if self.event_bus and hasattr(self.event_bus, "get_service"):
                    state_service = self.event_bus.get_service("state_service")

                if state_service is not None:
                    try:
                        perm_snapshot = await state_service.get_permission_state()
                        # Snapshot.approval_mode is the enum name (e.g. "DEFAULT",
                        # "CONFIRM_ALL", "TRUST_ALL"). Re-prefix with "ApprovalMode."
                        # so the description lookup below keeps matching.
                        mode_str = f"ApprovalMode.{perm_snapshot.approval_mode}"
                        stats = dict(perm_snapshot.stats)
                    except Exception as e:
                        self.logger.warning(
                            f"state_service.get_permission_state failed, "
                            f"falling back to direct read: {e}"
                        )
                        state_service = None

                if state_service is None:
                    mode = permission_manager.approval_mode
                    stats = permission_manager.get_stats()
                    mode_str = str(mode)

                mode_descriptions = {
                    "ApprovalMode.DEFAULT": "DEFAULT - Confirm HIGH risk only",
                    "ApprovalMode.CONFIRM_ALL": "CONFIRM ALL - Confirm everything",
                    "ApprovalMode.AUTO_APPROVE_EDITS": "AUTO APPROVE EDITS - Confirm shell only",
                    "ApprovalMode.TRUST_ALL": "TRUST ALL - Approve everything (DANGEROUS)",
                }
                mode_desc = mode_descriptions.get(mode_str, mode_str)

                # Defensive: stats may be missing keys if state_service returned
                # a PermissionSnapshot with an empty stats dict.
                stats.setdefault("total_checks", 0)
                stats.setdefault("auto_approved", 0)
                stats.setdefault("user_approved", 0)
                stats.setdefault("denied", 0)
                stats.setdefault("blocked", 0)

                message = f"""[info] Permission Settings

Mode: {mode_desc}

Statistics:
  Total checks: {stats['total_checks']}
  Auto-approved: {stats['auto_approved']}
  User approved: {stats['user_approved']}
  Denied: {stats['denied']}
  Blocked: {stats['blocked']}

Commands:
  /permissions default - Use DEFAULT mode
  /permissions strict - Use CONFIRM_ALL mode
  /permissions trust - Use TRUST_ALL mode
  /permissions stats - Show statistics
  /permissions project - Show project approvals
  /permissions clear - Clear session approvals
  /permissions clear-project - Clear project approvals"""

                return CommandResult(success=True, message=message, display_type="info")

            elif subcommand in (
                "default",
                "strict",
                "confirm_all",
                "trust",
                "trust_all",
            ):
                # Phase 4: route the write through state_service so the
                # same command works in local and attach mode. The
                # state_service.set_approval_mode method accepts aliases
                # ("strict" -> CONFIRM_ALL, "trust" -> TRUST_ALL), so we
                # can forward the subcommand verb directly. The output
                # message matches the pre-migration format exactly so
                # nothing downstream (tests, docs, screenshots) breaks.
                mode_display_map = {
                    "default": (
                        "DEFAULT",
                        "Permission mode set to DEFAULT (confirm HIGH risk only)",
                        "success",
                    ),
                    "strict": (
                        "CONFIRM_ALL",
                        "Permission mode set to CONFIRM_ALL (confirm everything)",
                        "success",
                    ),
                    "confirm_all": (
                        "CONFIRM_ALL",
                        "Permission mode set to CONFIRM_ALL (confirm everything)",
                        "success",
                    ),
                    "trust": (
                        "TRUST_ALL",
                        "Permission mode set to TRUST_ALL (approve everything - DANGEROUS)",
                        "warning",
                    ),
                    "trust_all": (
                        "TRUST_ALL",
                        "Permission mode set to TRUST_ALL (approve everything - DANGEROUS)",
                        "warning",
                    ),
                }
                mode_name, mode_msg, mode_display = mode_display_map[subcommand]

                state_service = None
                if self.event_bus and hasattr(self.event_bus, "get_service"):
                    state_service = self.event_bus.get_service("state_service")

                if state_service is not None:
                    try:
                        await state_service.set_approval_mode(mode_name)
                        return CommandResult(
                            success=True,
                            message=mode_msg,
                            display_type=mode_display,
                        )
                    except ValueError as e:
                        return CommandResult(
                            success=False,
                            message=f"[error] {e}",
                            display_type="error",
                        )
                    except Exception as e:
                        self.logger.warning(
                            f"state_service.set_approval_mode failed, "
                            f"falling back to direct write: {e}"
                        )

                # Fallback: direct permission_manager write (pre-migration path)
                from kollabor_events.permissions_models import ApprovalMode

                direct_map = {
                    "DEFAULT": ApprovalMode.DEFAULT,
                    "CONFIRM_ALL": ApprovalMode.CONFIRM_ALL,
                    "TRUST_ALL": ApprovalMode.TRUST_ALL,
                }
                permission_manager.set_approval_mode(direct_map[mode_name])
                return CommandResult(
                    success=True,
                    message=mode_msg,
                    display_type=mode_display,
                )

            elif subcommand == "stats":
                # Phase 3: prefer state_service, fall back to direct read.
                stats = {}
                state_service = None
                if self.event_bus and hasattr(self.event_bus, "get_service"):
                    state_service = self.event_bus.get_service("state_service")
                if state_service is not None:
                    try:
                        perm_snapshot = await state_service.get_permission_state()
                        stats = dict(perm_snapshot.stats)
                    except Exception as e:
                        self.logger.warning(
                            f"state_service.get_permission_state failed: {e}"
                        )
                        state_service = None
                if state_service is None:
                    stats = permission_manager.get_stats()

                # Defensive defaults so the f-string can't KeyError if the
                # daemon returned a partial stats dict.
                stats.setdefault("total_checks", 0)
                stats.setdefault("auto_approved", 0)
                stats.setdefault("user_approved", 0)
                stats.setdefault("denied", 0)
                stats.setdefault("blocked", 0)

                message = f"""[info] Permission Statistics

Total checks: {stats['total_checks']}
Auto-approved: {stats['auto_approved']}
User approved: {stats['user_approved']}
Denied: {stats['denied']}
Blocked: {stats['blocked']}"""

                return CommandResult(success=True, message=message, display_type="info")

            elif subcommand == "clear":
                # Phase 4.5 step 7: state_service only, no fallback.
                state_service = None
                if self.event_bus and hasattr(self.event_bus, "get_service"):
                    state_service = self.event_bus.get_service("state_service")
                if state_service is None:
                    return CommandResult(
                        success=False,
                        message="State service not available.",
                        display_type="error",
                    )
                try:
                    await state_service.clear_session_approvals()
                except ValueError as e:
                    return CommandResult(
                        success=False,
                        message=f"[error] {e}",
                        display_type="error",
                    )
                return CommandResult(
                    success=True,
                    message="Session approvals cleared",
                    display_type="success",
                )

            elif subcommand == "project":
                # Phase 4.5 step 7: state_service only.
                state_service = None
                if self.event_bus and hasattr(self.event_bus, "get_service"):
                    state_service = self.event_bus.get_service("state_service")
                if state_service is None:
                    return CommandResult(
                        success=False,
                        message="State service not available.",
                        display_type="error",
                    )
                try:
                    project_approvals: List[str] = (
                        await state_service.list_project_approvals()
                    )
                except Exception as e:
                    self.logger.error(
                        f"state_service.list_project_approvals failed: {e}"
                    )
                    return CommandResult(
                        success=False,
                        message=f"Error reading project approvals: {e}",
                        display_type="error",
                    )

                if project_approvals:
                    approvals_list = "\n".join(
                        f"  - {key}" for key in project_approvals
                    )
                    message = (
                        f"[info] Project Approvals ({len(project_approvals)}):"
                        f"\n{approvals_list}"
                    )
                else:
                    message = "[info] No project approvals recorded"
                return CommandResult(success=True, message=message, display_type="info")

            elif subcommand in ("clear-project", "clearproject"):
                # Phase 4.5 step 7: state_service only, no fallback.
                state_service = None
                if self.event_bus and hasattr(self.event_bus, "get_service"):
                    state_service = self.event_bus.get_service("state_service")
                if state_service is None:
                    return CommandResult(
                        success=False,
                        message="State service not available.",
                        display_type="error",
                    )
                try:
                    await state_service.clear_project_approvals()
                except ValueError as e:
                    return CommandResult(
                        success=False,
                        message=f"[error] {e}",
                        display_type="error",
                    )
                return CommandResult(
                    success=True,
                    message="Project approvals cleared",
                    display_type="success",
                )

            else:
                return CommandResult(
                    success=False,
                    message=(
                        f"[error] Unknown subcommand: {subcommand}\n\n"
                        "Use: /permissions [show|default|strict|trust|stats|project|clear|clear-project]"
                    ),
                    display_type="error",
                )

        except Exception as e:
            self.logger.error(f"Error in permissions command: {e}")
            import traceback

            traceback.print_exc()
            return CommandResult(
                success=False,
                message=f"[error] Error managing permissions: {str(e)}",
                display_type="error",
            )

    async def handle_version(self, command: SlashCommand) -> CommandResult:
        """Handle /version command.

        Args:
            command: Parsed slash command.

        Returns:
            Command execution result.
        """
        try:
            # Get version information
            version_info = self._get_version_info()

            message = f"""Kollab v{version_info['version']}
Built: {version_info['build_date']}
Python: {version_info['python_version']}
Platform: {version_info['platform']}"""

            return CommandResult(
                success=True, message=message, display_type="info", data=version_info
            )

        except Exception as e:
            self.logger.error(f"Error in version command: {e}")
            return CommandResult(
                success=False,
                message=f"Error getting version: {str(e)}",
                display_type="error",
            )

    async def handle_restart(self, command: SlashCommand) -> CommandResult:
        """Handle /restart command - clear conversation and start fresh.

        Phase 4.5 step 7: state_service is the only path. Works in
        local and attach mode. No legacy fallback.

        Args:
            command: Parsed slash command.

        Returns:
            Command execution result.
        """
        try:
            state_service = None
            if self.event_bus and hasattr(self.event_bus, "get_service"):
                state_service = self.event_bus.get_service("state_service")
            if state_service is None:
                return CommandResult(
                    success=False,
                    message="State service not available -- cannot restart.",
                    display_type="error",
                )

            # Read the pre-restart snapshot so the success message can
            # report what was cleared and which agent was active.
            old_message_count = 0
            agent_name = "default"
            try:
                old_snapshot = await state_service.get_conversation()
                # Exclude the system message from the count.
                old_message_count = max(0, old_snapshot.message_count - 1)
                active_agent = await state_service.get_active_agent()
                if active_agent.name:
                    agent_name = active_agent.name
            except Exception as e:
                self.logger.debug(f"pre-restart snapshot read failed: {e}")

            try:
                await state_service.restart_session()
            except ValueError as e:
                return CommandResult(
                    success=False,
                    message=f"[error] Failed to restart: {e}",
                    display_type="error",
                )
            except Exception as e:
                self.logger.error(f"state_service.restart_session failed: {e}")
                return CommandResult(
                    success=False,
                    message=f"Error restarting session: {e}",
                    display_type="error",
                )

            return CommandResult(
                success=True,
                message=(
                    f"[ok] Session restarted\n  Agent: {agent_name}\n"
                    f"  Cleared: {old_message_count} messages"
                ),
                display_type="success",
            )

        except Exception as e:
            self.logger.error(f"Error in restart command: {e}")
            return CommandResult(
                success=False,
                message=f"Error restarting session: {str(e)}",
                display_type="error",
            )

    async def _show_command_help(self, command_name: str) -> CommandResult:
        """Show help for a specific command.

        Args:
            command_name: Name of command to show help for.

        Returns:
            Command result with help information.
        """
        command_def = self.command_registry.get_command(command_name)
        if not command_def:
            return CommandResult(
                success=False,
                message=f"Unknown command: /{command_name}",
                display_type="error",
            )

        # Format detailed help for the command
        help_text = f"""Command: /{command_def.name}
Description: {command_def.description}
Plugin: {command_def.plugin_name}
Category: {command_def.category.value}
Mode: {command_def.mode.value}"""

        if command_def.aliases:
            help_text += f"\nAliases: {', '.join(command_def.aliases)}"

        if command_def.parameters:
            help_text += "\nParameters:"
            for param in command_def.parameters:
                required = " (required)" if param.required else ""
                help_text += f"\n  {param.name}: {param.description}{required}"

        return CommandResult(success=True, message=help_text, display_type="info")

    async def _show_all_commands(self) -> CommandResult:
        """Show all available commands grouped by plugin in a status modal.

        Returns:
            Command result with status modal UI config.
        """
        # Get commands grouped by plugin
        plugin_categories = self.command_registry.get_plugin_categories()

        # Build command list for modal display
        command_sections = []

        for plugin_name in sorted(plugin_categories.keys()):
            commands = self.command_registry.get_commands_by_plugin(plugin_name)
            if not commands:
                continue

            # Create section for this plugin
            section_commands = []
            for cmd in sorted(commands, key=lambda c: c.name):
                aliases = f" ({', '.join(cmd.aliases)})" if cmd.aliases else ""
                section_commands.append(
                    {"name": f"/{cmd.name}{aliases}", "description": cmd.description}
                )

            command_sections.append(
                {
                    "title": f"{plugin_name.title()} Commands",
                    "commands": section_commands,
                }
            )

        return CommandResult(
            success=True,
            message="Help opened in status modal",
            ui_config=UIConfig(
                type="status_modal",
                title="Available Commands",
                # Width and height are dynamic (terminal_width - 2)
                modal_config={
                    "sections": command_sections,
                    "footer": "Esc/Enter close • /help <command> for details",
                    "scrollable": True,
                },
            ),
            display_type="status_modal",
        )

    def _get_version_info(self) -> Dict[str, str]:
        """Get application version information.

        Returns:
            Dictionary with version details.
        """
        import sys

        from .... import __version__

        return {
            "version": __version__,
            "build_date": datetime.now().strftime("%Y-%m-%d"),
            "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "platform": platform.system(),
            "architecture": platform.machine(),
        }
