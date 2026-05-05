"""MCP (Model Context Protocol) management command with setup wizard.

Provides slash command interface for:
- Interactive setup wizard for MCP configuration
- Viewing MCP server status
- Listing available MCP tools
- Managing MCP server connections (enable/disable)
- Testing MCP server connections

This is now a thin command handler that delegates business logic to
kollabor_agent.MCPManager. The UI/interaction code remains here.
"""

import logging
import sys
from typing import Any, Dict, List, Optional

from kollabor_agent import MCPManager
from kollabor_events.models import (
    CommandCategory,
    CommandDefinition,
    CommandMode,
    CommandResult,
    SlashCommand,
    SubcommandInfo,
)
from kollabor_tui.status.mcp_status_view import MCPStatusView

IS_WINDOWS = sys.platform == "win32"

if not IS_WINDOWS:
    import termios
    import tty

logger = logging.getLogger(__name__)


class MCPSetupWizard:
    """Interactive setup wizard for MCP server configuration.

    This class handles the UI/interaction parts of the setup wizard,
    while delegating configuration management to MCPManager.
    """

    def __init__(self, renderer, mcp_manager: Optional[MCPManager] = None):
        """Initialize MCP setup wizard.

        Args:
            renderer: TerminalRenderer instance for display
            mcp_manager: Optional MCPManager instance (created if not provided)
        """
        self.renderer = renderer
        self.mcp_manager = mcp_manager or MCPManager()

    async def run(self) -> Dict[str, Any]:
        """Run the interactive setup wizard.

        Returns:
            Command result dictionary
        """
        try:
            # Step 1: Ensure directory exists
            self.mcp_manager.ensure_directory()

            # Step 2: Load example config
            example_config = self.mcp_manager.load_example_config()

            if not example_config:
                return {
                    "success": False,
                    "error": "Could not load example configuration",
                    "output": "Example config not found at: "
                    + str(self.mcp_manager.example_config_path),
                }

            # Step 3: Create initial config if needed
            current_config = self.mcp_manager.load_config()

            if current_config is None:
                current_config = {"servers": {}}
                self.mcp_manager.save_config(current_config)

            # Step 4: Run server selection wizard
            selected_servers = await self._server_selection_wizard(
                example_config, current_config
            )

            if not selected_servers:
                return {
                    "success": True,
                    "output": "MCP setup cancelled. No changes made.",
                }

            # Step 5: Configure API keys for selected servers
            configured_servers = await self._configure_servers(
                selected_servers, current_config
            )

            # Step 6: Save final configuration
            final_config = {"servers": configured_servers}
            self.mcp_manager.save_config(final_config)

            # Step 7: Display summary
            return await self._display_summary(final_config)

        except Exception as e:
            logger.error(f"Error in MCP setup wizard: {e}")
            return {"success": False, "error": str(e)}

    async def _server_selection_wizard(
        self, example_config: Dict, current_config: Dict
    ) -> Dict[str, Dict]:
        """Run interactive server selection wizard.

        Uses the standard fullscreen modal pattern from CLAUDE.md:
        1. Pause render loop via coordinator
        2. Enter ANSI alternate screen buffer
        3. Render modal and handle input
        4. Exit ANSI alternate buffer
        5. Restore render state via coordinator

        Args:
            example_config: Example configuration with all available servers
            current_config: Current user configuration

        Returns:
            Dictionary of selected server configs
        """
        # 1. Pause render loop via coordinator
        self.renderer.message_coordinator.enter_alternate_buffer()

        selected_servers: Dict[str, Any] = {}
        old_settings = None

        try:
            # 2. Enter ANSI alternate screen buffer
            sys.stdout.write("\033[?1049h")  # Enter alternate buffer
            sys.stdout.write("\033[?25l")  # Hide cursor
            sys.stdout.flush()

            # Setup raw mode for input
            if not IS_WINDOWS:
                old_settings = termios.tcgetattr(sys.stdin)
                tty.setraw(sys.stdin.fileno())

            example_servers = example_config.get("servers", {})
            current_servers = current_config.get("servers", {})

            # Build server list with descriptions
            server_list = []
            for name, config in sorted(example_servers.items()):
                description = config.get("description", "No description")
                currently_enabled = current_servers.get(name, {}).get("enabled", False)
                server_list.append(
                    {
                        "name": name,
                        "description": description,
                        "enabled": currently_enabled,
                        "config": config,
                    }
                )

            current_index = 0

            # Display server selection
            while True:
                self._render_server_list(server_list, current_index)

                # Read input (platform-specific)
                if IS_WINDOWS:
                    import msvcrt

                    ch = getattr(msvcrt, "getwch")()
                else:
                    ch = sys.stdin.read(1)

                if IS_WINDOWS and ch in ("\x00", "\xe0"):
                    # Windows special key prefix - read scancode
                    scan = getattr(msvcrt, "getwch")()
                    if scan == "H":  # Up arrow
                        current_index = max(0, current_index - 1)
                    elif scan == "P":  # Down arrow
                        current_index = min(len(server_list) - 1, current_index + 1)
                elif ch == "\x1b":  # Escape sequence (Unix) or Esc key (Windows)
                    if IS_WINDOWS:
                        selected_servers = {}
                        break
                    # Check for arrow keys (Unix)
                    ch2 = sys.stdin.read(1)
                    if ch2 == "[":
                        ch3 = sys.stdin.read(1)
                        if ch3 == "A":  # Up arrow
                            current_index = max(0, current_index - 1)
                        elif ch3 == "B":  # Down arrow
                            current_index = min(len(server_list) - 1, current_index + 1)
                    else:  # Just Esc - user cancelled
                        selected_servers = {}
                        break

                elif ch == " ":  # Space - toggle selection
                    server_list[current_index]["enabled"] = not server_list[
                        current_index
                    ]["enabled"]

                elif ch == "\r" or ch == "\n":  # Enter - confirm
                    # Collect selected servers
                    for server in server_list:
                        if server["enabled"]:
                            selected_servers[server["name"]] = server["config"].copy()
                            selected_servers[server["name"]]["enabled"] = True
                    break

        except Exception as e:
            logger.error(f"Error in server selection wizard: {e}")

        finally:
            # Restore terminal settings (Unix only)
            if not IS_WINDOWS and old_settings:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)

            # 4. Exit ANSI alternate buffer
            sys.stdout.write("\033[?25h")  # Show cursor
            sys.stdout.write("\033[?1049l")  # Exit alternate buffer
            sys.stdout.flush()

            # 5. Restore render state via coordinator
            self.renderer.message_coordinator.exit_alternate_buffer(restore_state=True)

        return selected_servers

    def _render_server_list(self, server_list: List[Dict], current_index: int) -> None:
        """Render the server selection list.

        Args:
            server_list: List of server configurations
            current_index: Currently selected index
        """
        # Clear screen and position cursor at top
        sys.stdout.write("\033[2J\033[H")

        # Header
        self._print("\033[1;36m")
        self._print("  ╔════════════════════════════════════════════════╗")
        self._print("  ║     MCP Server Setup Wizard                    ║")
        self._print("  ╚════════════════════════════════════════════════╝")
        self._print("\033[0m")

        self._print("")
        self._print("  Select MCP Servers to Enable")
        self._print("  " + "=" * 50)
        self._print("")
        self._print(
            "  Arrow keys to navigate | Space to toggle | Enter to confirm | Esc to cancel"
        )
        self._print("")

        for i, server in enumerate(server_list):
            prefix = ">" if i == current_index else " "
            checkbox = "[x]" if server["enabled"] else "[ ]"
            status = "enabled" if server["enabled"] else "disabled"

            # Highlight current selection
            if i == current_index:
                self._print(
                    f"  {prefix} {checkbox} \033[1;32m{server['name']}\033[0m - {server['description']}"
                )
                self._print(f"       Status: {status}")
                if server["config"].get("env"):
                    env_vars = list(server["config"]["env"].keys())
                    self._print(f"       Requires: {', '.join(env_vars)}")
            else:
                self._print(
                    f"  {prefix} {checkbox} {server['name']} - {server['description']}"
                )

        sys.stdout.flush()

    async def _configure_servers(
        self, selected_servers: Dict[str, Dict], current_config: Dict
    ) -> Dict[str, Dict]:
        """Configure API keys for servers that need them.

        Args:
            selected_servers: Servers user selected
            current_config: Current configuration (for existing API keys)

        Returns:
            Configured server dictionary
        """
        # Check if any servers need API keys
        servers_needing_keys = self.mcp_manager.get_servers_needing_keys(
            selected_servers
        )

        if not servers_needing_keys:
            return selected_servers

        # Pause render loop via coordinator
        self.renderer.message_coordinator.enter_alternate_buffer()

        old_settings = None
        key_values: Dict[str, Dict[str, str]] = {}

        try:
            # Enter ANSI alternate screen buffer
            sys.stdout.write("\033[?1049h")  # Enter alternate buffer
            sys.stdout.flush()

            if not IS_WINDOWS:
                old_settings = termios.tcgetattr(sys.stdin)

            for server_name in servers_needing_keys:
                if server_name not in key_values:
                    key_values[server_name] = {}

                server_config = selected_servers[server_name]
                env_vars = server_config.get("env", {})

                for env_key in env_vars.keys():
                    # Clear and render form
                    sys.stdout.write("\033[2J\033[H")
                    self._print("\033[1;36m")
                    self._print("  ╔════════════════════════════════════════════════╗")
                    self._print("  ║     MCP Server Setup Wizard                    ║")
                    self._print("  ╚════════════════════════════════════════════════╝")
                    self._print("\033[0m")

                    self._print("")
                    self._print(f"  Configure API Key for {server_name}")
                    self._print("  " + "=" * 50)
                    self._print("")
                    self._print(f"  Environment Variable: {env_key}")

                    # Check if already configured
                    existing_value = self.mcp_manager.get_existing_key_value(
                        server_name, env_key, current_config
                    )

                    if existing_value and not existing_value.endswith("-here"):
                        self._print(f"  Current value: {existing_value[:20]}...")
                        self._print("")
                        self._print(
                            "  Press Enter to keep current value, or type new value:"
                        )
                        self._print("")
                        sys.stdout.write(f"  {env_key}: ")
                        sys.stdout.flush()

                        new_value = input().strip()

                        if not new_value:
                            # Keep existing value
                            key_values[server_name][env_key] = existing_value
                        else:
                            key_values[server_name][env_key] = new_value

                    else:
                        self._print("")
                        self._print("  Enter API key value:")
                        self._print("")
                        sys.stdout.write(f"  {env_key}: ")
                        sys.stdout.flush()

                        new_value = input().strip()
                        key_values[server_name][env_key] = new_value

        except Exception as e:
            logger.error(f"Error configuring servers: {e}")

        finally:
            if not IS_WINDOWS and old_settings:
                termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old_settings)

            # Exit ANSI alternate buffer
            sys.stdout.write("\033[?1049l")  # Exit alternate buffer
            sys.stdout.flush()

            self.renderer.message_coordinator.exit_alternate_buffer(restore_state=True)

        # Apply the configured keys using MCPManager
        return self.mcp_manager.configure_server_keys(
            selected_servers, current_config, key_values
        )

    async def _display_summary(self, final_config: Dict) -> Dict[str, Any]:
        """Display configuration summary.

        Args:
            final_config: Final configuration dictionary

        Returns:
            Command result dictionary
        """
        lines = []
        lines.append("MCP Configuration Saved")
        lines.append("=" * 40)
        lines.append("")
        lines.append(f"Configuration file: {self.mcp_manager.config_path}")
        lines.append("")

        servers = final_config.get("servers", {})
        enabled_servers = [
            name for name, config in servers.items() if config.get("enabled")
        ]

        lines.append(f"Total servers configured: {len(servers)}")
        lines.append(f"Enabled servers: {len(enabled_servers)}")
        lines.append("")

        if enabled_servers:
            lines.append("Enabled servers:")
            for name in sorted(enabled_servers):
                config = servers[name]
                lines.append(f"  + {name}")
                if config.get("env"):
                    lines.append("    Environment variables configured:")
                    for key in config["env"].keys():
                        value = config["env"][key]
                        if value.endswith("-here"):
                            lines.append(f"      - {key}: NOT CONFIGURED")
                        else:
                            lines.append(f"      - {key}: ***configured***")
                lines.append("")

        lines.append("Note: Servers are being connected in the background...")

        output = "\n".join(lines)

        if self.renderer and self.renderer.message_coordinator:
            for line in lines:
                await self.renderer.display_thinking(line, add_to_history=False)

        return {"success": True, "output": output}

    def _print(self, text: str = ""):
        """Print text with proper line endings for raw mode.

        In raw mode, \n doesn't translate to \r\n, so we need to
        write \r\n explicitly for proper cursor positioning.
        """
        sys.stdout.write(text + "\r\n")
        sys.stdout.flush()


class MCPCommandHandler:
    """Handler for /mcp slash command.

    This is a thin wrapper that delegates business logic to MCPManager
    and handles UI/interaction.
    """

    def __init__(self, command_registry, mcp_integration, renderer=None, app=None):
        """Initialize MCP command handler.

        Args:
            command_registry: CommandRegistry instance
            mcp_integration: MCPIntegration instance
            renderer: Optional terminal renderer
            app: Optional application instance
        """
        self.command_registry = command_registry
        self.mcp_integration = mcp_integration
        self.renderer = renderer
        self.app = app
        self.name = "mcp"
        self.mcp_manager = MCPManager()

    def register_commands(self) -> None:
        """Register all MCP commands."""

        # Register /mcp command with enhanced subcommands
        mcp_command = CommandDefinition(
            name="mcp",
            description="Manage MCP (Model Context Protocol) servers and tools",
            handler=self.handle_mcp,
            plugin_name=self.name,
            category=CommandCategory.SYSTEM,
            mode=CommandMode.INSTANT,
            aliases=["mcps", "servers"],
            subcommands=[
                SubcommandInfo(
                    "setup", "", "Interactive setup wizard for MCP configuration"
                ),
                SubcommandInfo("list", "", "Show MCP status (alias for show)"),
                SubcommandInfo(
                    "test", "<server>", "Test connection to a specific server"
                ),
                SubcommandInfo("enable", "<server>", "Enable a specific MCP server"),
                SubcommandInfo("disable", "<server>", "Disable a specific MCP server"),
                SubcommandInfo("show", "", "Show MCP status panel"),
                SubcommandInfo("reload", "", "Reload MCP config and reconnect servers"),
                SubcommandInfo("tools", "[server]", "Show available MCP tools"),
            ],
        )
        self.command_registry.register_command(mcp_command)

        logger.info("MCP commands registered")

    async def handle_mcp(self, command: SlashCommand) -> CommandResult:
        """Handle /mcp command.

        Args:
            command: Parsed slash command with args

        Returns:
            Command execution result
        """
        args = command.args

        # Setup wizard works without mcp_integration
        if args and args[0].lower() == "setup":
            return await self._run_setup_wizard()

        # Other commands require mcp_integration
        if not self.mcp_integration:
            return CommandResult(
                success=False,
                message="MCP integration not available.\n\nRun /mcp setup to configure MCP servers.",
                display_type="warning",
            )

        if not args:
            # Show help text followed by status
            help_text = self._get_help_text()
            status_result = await self._show_status()
            combined = help_text + "\n\n" + status_result.message
            return CommandResult(
                success=True, message=combined, display_type="info"
            )

        subcommand = args[0].lower()

        if subcommand == "list":
            return await self._show_status()
        elif subcommand == "test":
            server_name = args[1] if len(args) > 1 else None
            return await self._test_server(server_name)
        elif subcommand == "enable":
            server_name = args[1] if len(args) > 1 else None
            return await self._toggle_server(server_name, enable=True)
        elif subcommand == "disable":
            server_name = args[1] if len(args) > 1 else None
            return await self._toggle_server(server_name, enable=False)
        elif subcommand in ("show", "status"):
            return await self._show_status()
        elif subcommand == "reload":
            return await self._reload_servers()
        elif subcommand == "tools":
            server_filter = args[1] if len(args) > 1 else None
            return await self._list_tools(server_filter)
        else:
            return CommandResult(
                success=False,
                message=f"Unknown subcommand: {subcommand}\n{self._get_help_text()}",
                display_type="error",
            )

    async def _run_setup_wizard(self) -> CommandResult:
        """Run the interactive MCP setup wizard.

        Returns:
            Command execution result
        """
        try:
            wizard = MCPSetupWizard(self.renderer, self.mcp_manager)
            result = await wizard.run()

            # Hot reload MCP servers after setup
            if self.mcp_integration:
                await self.mcp_integration.discover_mcp_servers()

            # Convert dict result to CommandResult if needed
            if isinstance(result, dict):
                return CommandResult(
                    success=result.get("success", True),
                    message=str(
                        result.get("output", result.get("message", "Setup complete"))
                    ),
                    display_type="success" if result.get("success", True) else "error",
                )
            return result
        except Exception as e:
            logger.error(f"Error running setup wizard: {e}")
            return CommandResult(success=False, message=str(e), display_type="error")

    async def _reload_servers(self) -> CommandResult:
        """Reload MCP configuration and reconnect configured servers."""
        state_service = self._get_state_service()
        if state_service is not None and hasattr(state_service, "reload_mcp_servers"):
            try:
                summary = await state_service.reload_mcp_servers()
                return self._format_reload_result(summary)
            except Exception as e:
                logger.error(f"state_service.reload_mcp_servers failed: {e}")
                return CommandResult(
                    success=False,
                    message=f"Error reloading MCP servers: {e}",
                    display_type="error",
                )

        if not self.mcp_integration:
            return CommandResult(
                success=False,
                message="MCP integration not available.",
                display_type="error",
            )

        try:
            if hasattr(self.mcp_integration, "reload_mcp_servers"):
                summary = await self.mcp_integration.reload_mcp_servers()
            else:
                await self.mcp_integration.shutdown()
                self.mcp_integration.mcp_servers.clear()
                self.mcp_integration._load_mcp_config()
                discovered = await self.mcp_integration.discover_mcp_servers()
                summary = {
                    "reconnected": len(self.mcp_integration.server_connections),
                    "configured": len(self.mcp_integration.mcp_servers),
                    "discovered": (
                        len(discovered) if isinstance(discovered, dict) else 0
                    ),
                }
        except Exception as e:
            logger.error(f"Error reloading MCP servers: {e}")
            return CommandResult(
                success=False,
                message=f"Error reloading MCP servers: {e}",
                display_type="error",
            )

        return self._format_reload_result(summary)

    def _format_reload_result(self, summary: Dict[str, Any]) -> CommandResult:
        """Render the `/mcp reload` summary."""
        reconnected = int(summary.get("reconnected", 0) or 0)
        configured = int(summary.get("configured", 0) or 0)
        discovered_count = int(summary.get("discovered", 0) or 0)

        lines: List[str] = []
        lines.append("MCP Servers Reloaded")
        lines.append("=" * 40)
        lines.append("")
        lines.append(f"Reconnected {reconnected} server(s).")
        lines.append(f"Loaded {configured} configured server(s).")
        if discovered_count != configured:
            lines.append(f"Discovered {discovered_count} server definition(s).")

        return CommandResult(
            success=True,
            message="\n".join(lines),
            display_type="success",
        )

    def _get_state_service(self) -> Any:
        """Look up the state_service on the event bus, or None.

        Phase 4.5 step 7: /mcp commands that touch daemon state route
        through state_service so they work identically in local and
        attach mode. When no state_service is wired (early init or
        stripped-down CLI contexts), the caller falls back to the
        direct MCPManager/mcp_integration path preserved below.
        """
        event_bus = getattr(self.app, "event_bus", None) if self.app else None
        if event_bus is None or not hasattr(event_bus, "get_service"):
            return None
        return event_bus.get_service("state_service")

    async def _test_server(self, server_name: Optional[str]) -> CommandResult:
        """Test connection to a specific MCP server via state_service.

        Phase 4.5 step 7: state_service is the only path.

        Args:
            server_name: Name of server to test

        Returns:
            Command execution result
        """
        if not server_name:
            return CommandResult(
                success=False,
                message="Usage: /mcp test <server_name>\n\nExample: /mcp test github",
                display_type="error",
            )

        state_service = self._get_state_service()
        if state_service is None:
            return CommandResult(
                success=False,
                message="State service not available -- cannot test server.",
                display_type="error",
            )

        try:
            status = await state_service.test_mcp_server(server_name)
        except ValueError as e:
            return CommandResult(
                success=False,
                message=(
                    f"Server '{server_name}' not found in configuration.\n\n"
                    f"{e}\n\nUse /mcp setup to configure MCP servers."
                ),
                display_type="error",
            )
        except Exception as e:
            logger.error(f"state_service.test_mcp_server failed: {e}")
            return CommandResult(
                success=False,
                message=f"Error testing server: {e}",
                display_type="error",
            )

        return self._format_test_result(server_name, status)

    def _format_test_result(
        self, server_name: str, status: Dict[str, Any]
    ) -> CommandResult:
        """Render the /mcp test output from a status dict.

        The status dict shape matches MCPManager.get_server_status:
        found / enabled / connected / tool_count / optional error.
        """
        lines: List[str] = []
        lines.append(f"Testing MCP Server: {server_name}")
        lines.append("=" * 40)
        lines.append("")

        if not status.get("enabled"):
            lines.append("Status: DISABLED")
            lines.append("")
            lines.append("Server is configured but not enabled.")
            lines.append(f"Use: /mcp enable {server_name}")
        elif status.get("connected"):
            lines.append("Status: CONNECTED")
            lines.append("")
            lines.append("Connection is active and working.")
            lines.append(f"Tools available: {status.get('tool_count', 0)}")
        else:
            lines.append("Status: NOT CONNECTED")
            lines.append("")
            lines.append("Server is enabled but not connected.")
            lines.append("This could be due to:")
            lines.append("  - Missing dependencies (npm packages)")
            lines.append("  - Invalid configuration")
            lines.append("  - Missing API keys")
            lines.append("")
            lines.append("Check the application logs for details.")

        return CommandResult(
            success=True, message="\n".join(lines), display_type="info"
        )

    async def _toggle_server(
        self, server_name: Optional[str], enable: bool = True
    ) -> CommandResult:
        """Enable or disable a specific MCP server via state_service.

        Phase 4.5 step 7: state_service is the only path. Daemon writes
        ~/.kollab/mcp/mcp_settings.json without hot-reloading the
        server subprocesses -- users see a "Restart to apply" message.
        Hot-reload is phase 4.6 work.

        Args:
            server_name: Name of server to toggle
            enable: True to enable, False to disable

        Returns:
            Command execution result
        """
        if not server_name:
            action = "enable" if enable else "disable"
            return CommandResult(
                success=False,
                message=f"Usage: /mcp {action} <server_name>\n\nExample: /mcp {action} github",
                display_type="error",
            )

        state_service = self._get_state_service()
        if state_service is None:
            return CommandResult(
                success=False,
                message="State service not available -- cannot toggle server.",
                display_type="error",
            )

        try:
            if enable:
                await state_service.enable_mcp_server(server_name)
            else:
                await state_service.disable_mcp_server(server_name)
        except ValueError as e:
            return CommandResult(
                success=False,
                message=str(e),
                display_type="error",
            )
        except Exception as e:
            logger.error(
                f"state_service.{('enable' if enable else 'disable')}_mcp_server failed: {e}"
            )
            return CommandResult(
                success=False,
                message=f"Error toggling server: {e}",
                display_type="error",
            )

        return self._format_toggle_result(server_name, enable)

    def _format_toggle_result(self, server_name: str, enable: bool) -> CommandResult:
        """Render the /mcp enable|disable success output.

        Phase 4.5 step 7: ends with "Restart to apply" because hot-reload
        is deferred to phase 4.6 -- a bare "Server is now connecting..."
        would lie to the user about what just happened.
        """
        action_text = "enabled" if enable else "disabled"
        lines: List[str] = []
        lines.append(f"MCP Server {action_text.title()}")
        lines.append("=" * 40)
        lines.append("")
        lines.append(f"Server '{server_name}' has been {action_text}.")
        lines.append("")
        lines.append("Restart kollab to apply this change.")
        return CommandResult(
            success=True, message="\n".join(lines), display_type="success"
        )

    async def _show_status(self) -> CommandResult:
        """Show MCP status panel.

        Phase 3: prefer state_service for reads so /mcp show works
        identically in local and attach mode. Falls back to MCPStatusView
        if state_service isn't wired (shouldn't happen in a normally
        initialized app, but keeps the command working in CLI-only
        contexts).

        Returns:
            Command execution result
        """
        try:
            from kollabor_tui.design_system import S

            state_service = None
            event_bus = getattr(self.app, "event_bus", None) if self.app else None
            if event_bus is not None and hasattr(event_bus, "get_service"):
                state_service = event_bus.get_service("state_service")

            if state_service is not None:
                try:
                    mcp_snapshot = await state_service.get_mcp_state()
                except Exception as e:
                    logger.warning(
                        f"state_service.get_mcp_state failed, "
                        f"falling back to MCPStatusView: {e}"
                    )
                    state_service = None

            if state_service is None:
                # Legacy path -- direct read from in-process mcp_integration.
                status_view = MCPStatusView(self.mcp_integration)
                lines = status_view.render()
                return CommandResult(
                    success=True, message="\n".join(lines), display_type="info"
                )

            # Render from the snapshot. Format matches MCPStatusView.render()
            # so the command output is identical in local and attach mode.
            lines = []
            lines.append(
                f"{S.BOLD}MCP SERVERS{S.RESET_BOLD}  "
                f"{mcp_snapshot.connected_servers}/{mcp_snapshot.total_servers} "
                f"servers | {mcp_snapshot.total_tools} tools"
            )

            connected_servers = [s for s in mcp_snapshot.servers if s.connected]
            if connected_servers:
                for server in sorted(connected_servers, key=lambda s: s.name):
                    lines.append(f"+ {server.name}: {server.tool_count} tools")
                    for tool_name in server.tools:
                        lines.append(f"  - {tool_name}")
            elif mcp_snapshot.total_servers == 0:
                lines.append("No MCP servers configured")
                lines.append("See: docs/mcp/MCP_SETUP.md")
            else:
                lines.append("No servers connected")
                lines.append("Check configuration")

            return CommandResult(
                success=True, message="\n".join(lines), display_type="info"
            )
        except Exception as e:
            logger.error(f"Error showing MCP status: {e}")
            return CommandResult(success=False, message=str(e), display_type="error")

    async def _list_tools(self, server_filter: Optional[str] = None) -> CommandResult:
        """List MCP tools via state_service.

        Phase 4.5 step 7: state_service is the only path.

        Args:
            server_filter: Optional server name to filter by

        Returns:
            Command execution result
        """
        state_service = self._get_state_service()
        if state_service is None:
            return CommandResult(
                success=False,
                message="State service not available -- cannot list tools.",
                display_type="error",
            )

        try:
            server_tools = await state_service.get_mcp_tools(server_filter)
        except Exception as e:
            logger.error(f"state_service.get_mcp_tools failed: {e}")
            return CommandResult(
                success=False,
                message=f"Error listing tools: {e}",
                display_type="error",
            )

        return self._format_tools_result(server_tools, server_filter)

    def _format_tools_result(
        self,
        server_tools: Dict[str, List[Dict[str, Any]]],
        server_filter: Optional[str],
    ) -> CommandResult:
        """Render the /mcp tools output from a tools-by-server dict."""
        lines: List[str] = []
        lines.append("MCP Tools")
        lines.append("=" * 40)

        if not server_tools:
            if server_filter:
                lines.append(f"No tools found for server: {server_filter}")
            else:
                lines.append("No MCP tools available")
                lines.append("")
                lines.append("Configure MCP servers to enable tools:")
                lines.append("See: docs/mcp/MCP_SETUP.md")
        else:
            for server_name in sorted(server_tools.keys()):
                tools = sorted(server_tools[server_name], key=lambda x: x["name"])
                lines.append(f"@ {server_name}")
                lines.append("-" * 40)

                for tool in tools:
                    lines.append(f"* {tool['name']}")
                    if tool.get("description"):
                        lines.append(f"  {tool['description']}")
                lines.append("")

        return CommandResult(
            success=True, message="\n".join(lines), display_type="info"
        )

    def _get_help_text(self) -> str:
        """Get help text for MCP command.

        Returns:
            Help text string
        """
        return """MCP Command Usage:

/mcp setup         - Interactive setup wizard for MCP configuration
/mcp list          - List all MCP servers and their status
/mcp test <server> - Test connection to a specific server
/mcp enable <x>    - Enable a specific MCP server
/mcp disable <x>   - Disable a specific MCP server
/mcp show          - Show MCP status panel
/mcp reload        - Reload MCP config and reconnect servers
/mcp tools         - Show available MCP tools
/mcp tools <x>     - Show tools from specific server

Examples:
  /mcp setup              - Run the setup wizard
  /mcp test github        - Test GitHub server connection
  /mcp enable filesystem  - Enable filesystem server
  /mcp disable brave      - Disable Brave Search server
  /mcp reload             - Reload MCP config and reconnect servers

For more information, see: docs/mcp/MCP_SETUP.md"""


def register_mcp_commands(
    command_registry, mcp_integration, renderer=None, app=None
) -> MCPCommandHandler:
    """Register MCP commands with the command registry.

    Args:
        command_registry: CommandRegistry instance
        mcp_integration: MCPIntegration instance
        renderer: Optional terminal renderer
        app: Optional application instance

    Returns:
        MCPCommandHandler instance
    """
    handler = MCPCommandHandler(
        command_registry=command_registry,
        mcp_integration=mcp_integration,
        renderer=renderer,
        app=app,
    )
    handler.register_commands()
    return handler
