"""MCP (Model Context Protocol) integration for LLM core service.

Provides integration with MCP servers for tool discovery and execution,
enabling the LLM to interact with external tools and services.

Implements MCP JSON-RPC 2.0 protocol over stdio for:
- Server initialization handshake
- tools/list for tool discovery
- tools/call for tool execution
"""

import asyncio
import json
import logging
import os
import re
import shlex
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from kollabor_config.config_utils import (
    APP_CONFIG_DIR_NAME,
    get_config_directory_candidates,
)
from kollabor_events.models import EventType

from .runtime import get_agent_tool_scope

if TYPE_CHECKING:
    from kollabor_events.bus import EventBus

logger = logging.getLogger(__name__)


def _get_loop():
    """Return the running event loop, or create one if none is running."""
    try:
        return asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.new_event_loop()


def _get_version() -> str:
    """Get application version without circular import."""
    try:
        from importlib.metadata import version

        return version("kollabor")
    except Exception:
        return "1.0.0"


class MCPServerConnection:
    """Manages a connection to an MCP server via stdio."""

    def __init__(
        self,
        server_name: str,
        command: str,
        cwd: Optional[Path] = None,
        extra_env: Optional[dict[str, str]] = None,
    ):
        self.server_name = server_name
        self.command = command
        self.cwd = cwd
        self.extra_env = extra_env or {}
        self.process: Optional[asyncio.subprocess.Process] = None
        self.initialized = False
        self._read_buffer = ""

    async def connect(self) -> bool:
        """Start the MCP server process."""
        try:
            command_parts = shlex.split(self.command)
            env = {**os.environ, **self.extra_env}
            self.process = await asyncio.create_subprocess_exec(
                *command_parts,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.cwd) if self.cwd else None,
                env=env,
            )
            logger.info(f"Started MCP server process: {self.server_name}")
            return True
        except Exception as e:
            logger.error(f"Failed to start MCP server {self.server_name}: {e}")
            return False

    async def initialize(self) -> bool:
        """Send MCP initialize request and wait for response."""
        if not self.process:
            logger.error(f"MCP server {self.server_name} process not available")
            return False

        request = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "kollab", "version": _get_version()},
            },
        }

        logger.debug(f"Sending initialize request to {self.server_name}")
        response = await self._send_request(request)

        if response is None:
            logger.warning(
                f"MCP server {self.server_name} initialization failed: "
                f"No response (timeout, connection lost, or process crashed)"
            )
            # Check if process is still running
            if self.process and self.process.returncode is not None:
                logger.error(
                    f"MCP server {self.server_name} process exited with code {self.process.returncode}. "
                    f"Check if the server command is correct and the MCP server binary exists."
                )
                # Try to read stderr for more details
                if self.process.stderr:
                    try:
                        stderr_output = await asyncio.wait_for(
                            self.process.stderr.read(), timeout=1
                        )
                        if stderr_output:
                            logger.error(
                                f"MCP server {self.server_name} stderr: {stderr_output.decode()}"
                            )
                    except Exception:
                        pass
            return False

        # Check for error response
        if "error" in response:
            error_info = response["error"]
            error_msg = error_info.get("message", "Unknown error")
            error_code = error_info.get("code", "unknown")
            logger.error(
                f"MCP server {self.server_name} returned error response: "
                f"[{error_code}] {error_msg}"
            )
            return False

        # Check for success response
        if "result" in response:
            self.initialized = True
            logger.info(f"MCP server {self.server_name} initialized successfully")

            # Send initialized notification
            notification = {"jsonrpc": "2.0", "method": "notifications/initialized"}
            await self._send_notification(notification)
            return True

        # Unexpected response format
        logger.warning(
            f"MCP server {self.server_name} initialization failed: "
            f"Unexpected response format (no 'result' or 'error' key): {response!r}"
        )
        return False

    async def list_tools(self) -> List[Dict[str, Any]]:
        """Request tools list from server."""
        if not self.initialized:
            return []

        request = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tools/list",
            "params": {},
        }

        response = await self._send_request(request)
        if response and "result" in response:
            tools = response["result"].get("tools", [])
            logger.info(f"Got {len(tools)} tools from {self.server_name}")
            return tools  # type: ignore[no-any-return]

        return []

    async def call_tool(
        self, tool_name: str, arguments: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Call a tool on the server."""
        if not self.initialized:
            return {"error": "Server not initialized"}

        request = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        }

        response = await self._send_request(request)
        if response:
            if "result" in response:
                return response["result"]  # type: ignore[no-any-return]
            elif "error" in response:
                return {"error": response["error"].get("message", "Unknown error")}  # type: ignore[no-any-return]

        return {"error": "No response from server"}

    async def _send_request(self, request: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Send JSON-RPC request and wait for response."""
        if not self.process or not self.process.stdin:
            return None

        try:
            message = json.dumps(request) + "\n"
            self.process.stdin.write(message.encode())
            await self.process.stdin.drain()

            # Read response with timeout — use 5 min to allow user permission dialogs
            response = await asyncio.wait_for(self._read_response(), timeout=300)
            return response
        except asyncio.TimeoutError:
            logger.warning(f"Timeout waiting for response from {self.server_name}")
            return None
        except Exception as e:
            logger.error(f"Error sending request to {self.server_name}: {e}")
            return None

    async def _send_notification(self, notification: Dict[str, Any]) -> None:
        """Send JSON-RPC notification (no response expected)."""
        if not self.process or not self.process.stdin:
            return

        try:
            message = json.dumps(notification) + "\n"
            self.process.stdin.write(message.encode())
            await self.process.stdin.drain()
        except Exception as e:
            logger.error(f"Error sending notification to {self.server_name}: {e}")

    async def _read_response(self) -> Optional[Dict[str, Any]]:
        """Read a JSON-RPC response from stdout."""
        if not self.process or not self.process.stdout:
            return None

        while True:
            # Check if we have a complete message in buffer
            if "\n" in self._read_buffer:
                line, self._read_buffer = self._read_buffer.split("\n", 1)
                if line.strip():
                    try:
                        return json.loads(line)  # type: ignore[no-any-return]
                    except json.JSONDecodeError:
                        continue

            # Read more data
            chunk = await self.process.stdout.read(4096)
            if not chunk:
                return None
            self._read_buffer += chunk.decode()

    async def close(self) -> None:
        """Close the server connection.

        Explicitly closes transports to prevent Python 3.12 asyncio
        __del__ warnings (BaseSubprocessTransport._closed AttributeError).
        """
        if self.process:
            try:
                # Close stdin first to signal server
                if self.process.stdin and not self.process.stdin.is_closing():
                    self.process.stdin.close()
            except Exception:
                pass

            try:
                self.process.terminate()
                await asyncio.wait_for(self.process.wait(), timeout=5)
            except asyncio.TimeoutError:
                try:
                    self.process.kill()
                    await self.process.wait()
                except Exception:
                    pass
            except Exception as e:
                logger.warning(f"Error closing MCP server {self.server_name}: {e}")
            finally:
                # Explicitly close transports to prevent __del__ gc warnings
                for transport in (
                    self.process.stdin,
                    self.process.stdout,
                    self.process.stderr,
                ):
                    if transport and hasattr(transport, "close"):
                        try:
                            transport.close()
                        except Exception:
                            pass
                self.process = None
            self.initialized = False
            logger.info(f"Closed MCP server connection: {self.server_name}")


class MCPIntegration:
    """MCP server and tool integration.

    Manages discovery, registration, and execution of MCP tools,
    bridging external services with the LLM core service.

    Uses proper MCP JSON-RPC protocol for server communication.
    """

    def __init__(
        self,
        event_bus: Optional["EventBus"] = None,
        workspace: Optional[str | Path] = None,
        user_token: Optional[str] = None,
        session_id: str = "",
        agent_manager: Any = None,
    ):
        """Initialize MCP integration.

        Args:
            event_bus: Optional event bus for emitting MCP lifecycle events
            workspace: Base workspace for local config, discovery, and server cwd
            user_token: Session JWT to inject as MENTIKO_SESSION_TOKEN into MCP subprocess env
            session_id: Engine session ID to inject as MENTIKO_SESSION_ID into MCP subprocess env
            agent_manager: Optional agent manager for bundle-scoped built-in tools
        """
        self.mcp_servers: Dict[str, Dict[str, Any]] = {}
        self.tool_registry: Dict[str, Dict[str, Any]] = {}
        self.server_connections: Dict[str, MCPServerConnection] = {}
        self.event_bus = event_bus
        self.user_token = user_token
        self.session_id = session_id
        self._agent_manager = agent_manager
        self.workspace = Path(workspace).expanduser().resolve() if workspace else Path.cwd().resolve()

        # MCP configuration directories (local project first, then global)
        self.local_mcp_dirs = [
            self.workspace / APP_CONFIG_DIR_NAME / "mcp",
        ]
        self.global_mcp_dirs = [
            directory / "mcp" for directory in get_config_directory_candidates()
        ]

        # Load from both local and global configs
        self._load_mcp_config()

        logger.info("MCP Integration initialized")

    def _load_mcp_config(self):
        """Load MCP configuration from Kollab config directories."""
        # Load from global config first (lower priority)
        for mcp_dir in self.global_mcp_dirs:
            self._load_config_from_dir(mcp_dir, "global")

        # Load from local config second (higher priority, can override)
        for mcp_dir in self.local_mcp_dirs:
            self._load_config_from_dir(mcp_dir, "local")

        logger.info(f"Loaded {len(self.mcp_servers)} total MCP server configurations")

    def _validate_server_config(
        self, server_name: str, config: Dict[str, Any]
    ) -> List[str]:
        """Validate MCP server configuration.

        Args:
            server_name: Name of the server for error messages
            config: Server configuration dictionary

        Returns:
            List of error messages (empty if valid)
        """
        errors = []

        # Check required fields
        if "type" not in config:
            errors.append(f"{server_name}: Missing 'type' field")
        elif config["type"] != "stdio":
            errors.append(
                f"{server_name}: Invalid type '{config.get('type')}' (only 'stdio' supported)"
            )

        if (
            "command" not in config
            or not config["command"]
            or not config["command"].strip()
        ):
            errors.append(f"{server_name}: Missing or empty 'command' field")
        else:
            # Validate command exists
            command = config["command"]
            try:
                # Extract the command executable (first word before any args)
                cmd_parts = shlex.split(command)
                if cmd_parts:
                    executable = cmd_parts[0]
                    # Check if executable exists in PATH
                    if not shutil.which(executable):
                        errors.append(
                            f"{server_name}: Command '{executable}' not found in PATH. "
                            f"Ensure the MCP server is installed and accessible."
                        )
            except ValueError as e:
                errors.append(f"{server_name}: Invalid command format: {e}")

        # Check optional field types
        if "enabled" in config and not isinstance(config["enabled"], bool):
            errors.append(
                f"{server_name}: 'enabled' field must be boolean (true/false)"
            )

        if "env" in config and not isinstance(config["env"], dict):
            errors.append(f"{server_name}: 'env' field must be an object")

        return errors

    def _load_config_from_dir(self, config_dir: Path, config_type: str):
        """Load MCP config from a specific directory.

        Args:
            config_dir: Directory to load config from
            config_type: Type of config (local/global) for logging
        """
        try:
            mcp_settings = config_dir / "mcp_settings.json"
            if mcp_settings.exists():
                with open(mcp_settings, "r") as f:
                    config = json.load(f)
                    servers = config.get("servers", {})

                    # Validate each server config before loading
                    valid_servers = {}
                    validation_errors = []
                    for server_name, server_config in servers.items():
                        errors = self._validate_server_config(
                            server_name, server_config
                        )
                        if errors:
                            validation_errors.extend(errors)
                            for error in errors:
                                logger.warning(f"MCP config error: {error}")
                            # Skip invalid servers
                            logger.warning(
                                f"MCP server '{server_name}' skipped due to validation errors"
                            )
                        else:
                            valid_servers[server_name] = server_config

                    # Only load valid servers
                    self.mcp_servers.update(valid_servers)
                    loaded_count = len(valid_servers)
                    skipped_count = len(servers) - loaded_count

                    if skipped_count > 0:
                        logger.warning(
                            f"Loaded {loaded_count} valid MCP servers from {config_type} config "
                            f"({skipped_count} skipped due to errors)"
                        )
                    else:
                        logger.info(
                            f"Loaded {loaded_count} MCP servers from {config_type} config"
                        )
        except json.JSONDecodeError as e:
            error_msg = f"Invalid JSON in {config_type} MCP config: {e}"
            logger.error(error_msg)
            logger.error(f"Config file: {config_dir / 'mcp_settings.json'}")
            logger.error(
                f"Hint: Run: python -m json.tool {config_dir / 'mcp_settings.json'}"
            )
        except Exception as e:
            logger.warning(f"Failed to load {config_type} MCP config: {e}")

    async def discover_mcp_servers(self) -> Dict[str, Any]:
        """Auto-discover available MCP servers and their tools.

        Connects to each configured stdio server using MCP protocol,
        initializes it, and queries available tools.

        Returns:
            Dictionary of discovered MCP servers and their capabilities
        """
        # Emit discovery start event
        if self.event_bus:
            await self.event_bus.emit_with_hooks(
                EventType.MCP_SERVER_DISCOVER,
                {"server_count": len(self.mcp_servers)},
                source="mcp_integration",
            )

        discovered: Dict[str, Any] = {}

        # Check for local MCP servers (manifest-based)
        await self._discover_local_servers(discovered)

        # Connect to discovered local servers
        for server_name, server_info in list(discovered.items()):
            if server_info.get("status") == "local":
                # Extract command from manifest
                manifest = server_info.get("manifest", {})
                command = manifest.get("command")

                if command:
                    # Connect and register tools
                    tools = await self._connect_and_list_tools(server_name, command)
                    # Update server info with connection results
                    discovered[server_name] = {
                        "name": server_name,
                        "type": "local",
                        "tools": [t.get("name") for t in tools],
                        "tool_count": len(tools),
                        "status": "connected" if tools else "no_tools",
                        "path": server_info.get("path"),
                        "manifest": manifest,
                    }
                    logger.info(
                        f"Connected to local MCP server: {server_name} with {len(tools)} tools"
                    )
                    self._push_env_mcp_connect(server_name, len(tools))
                    await self._auto_grant_mcp_tools(server_name, tools)
                else:
                    logger.warning(
                        f"Local MCP server {server_name} has no command in manifest"
                    )
                    discovered[server_name]["status"] = "invalid"

        # Connect to configured stdio servers using MCP protocol
        for server_name, server_config in self.mcp_servers.items():
            if not server_config.get("enabled", True):
                logger.debug(f"Skipping disabled MCP server: {server_name}")
                continue

            if server_config.get("type") == "stdio":
                command = server_config.get("command")
                if command:
                    tools = await self._connect_and_list_tools(server_name, command)
                    discovered[server_name] = {
                        "name": server_name,
                        "type": "stdio",
                        "tools": [t.get("name") for t in tools],
                        "tool_count": len(tools),
                        "status": "connected" if tools else "no_tools",
                    }
                    if tools:
                        self._push_env_mcp_connect(server_name, len(tools))
                        await self._auto_grant_mcp_tools(server_name, tools)

        # Emit discovery completed event
        if self.event_bus:
            await self.event_bus.emit_with_hooks(
                EventType.MCP_SERVER_DISCOVERED,
                {"discovered_count": len(discovered), "servers": discovered},
                source="mcp_integration",
            )

        return discovered

    async def reload_mcp_servers(self) -> Dict[str, int]:
        """Reload MCP configuration and reconnect enabled servers.

        This is the explicit hot-reload path used by `/mcp reload`. It
        closes active connections, clears stale in-memory configuration,
        reloads local/global config files, then runs normal discovery.

        Returns:
            Summary counts for configured, discovered, and reconnected
            servers after the reload.
        """
        await self.shutdown()
        self.mcp_servers.clear()
        self._load_mcp_config()
        discovered = await self.discover_mcp_servers()

        return {
            "configured": len(self.mcp_servers),
            "discovered": len(discovered) if isinstance(discovered, dict) else 0,
            "reconnected": len(self.server_connections),
        }

    def _push_env_mcp_connect(self, server_name: str, tool_count: int) -> None:
        """Push a capability env event for MCP server connection."""
        try:
            from kollabor_ai.notifications.producer import push_env

            push_env(
                self.event_bus,
                "capability",
                f"+mcp:{server_name} ({tool_count} tools)",
                kind="mcp_connect",
                collapse_key=f"mcp:{server_name}",
            )
        except Exception:
            pass

    async def _auto_grant_mcp_tools(
        self,
        server_name: str,
        tools: List[Dict[str, Any]],
    ) -> None:
        """Fire inject_tool_grant for each tool when conditions are met.

        Skips when:
          - event_bus is missing (isolated test setup)
          - llm_service is not registered yet (early boot)
          - _first_turn_complete is False (boot-time connect — tools are
            already in the initial system prompt; per-tool grants would
            be noise)
          - config key plugins.mcp.auto_grant_mcp_tools is false

        Each grant updates bundle scope + injects a [notification]
        system message + pushes a +tool:<name> env-queue event via the
        producer wired in kollab-8os.
        """
        if self.event_bus is None:
            return
        try:
            llm_service = self.event_bus.get_service("llm_service")
        except Exception:
            return
        if llm_service is None:
            return

        if not getattr(llm_service, "_first_turn_complete", False):
            return

        config = getattr(llm_service, "config", None)
        if config is not None:
            try:
                enabled = config.get("plugins.mcp.auto_grant_mcp_tools", True)
            except Exception:
                enabled = True
            if not enabled:
                return

        reason = f"mcp server {server_name} connected"
        for tool in tools:
            name = tool.get("name")
            if not name:
                continue
            try:
                await llm_service.inject_tool_grant(name, reason=reason)
            except Exception as e:
                logger.debug(
                    f"_auto_grant_mcp_tools: failed to grant {name}: {e}"
                )

    async def _connect_and_list_tools(
        self, server_name: str, command: str
    ) -> List[Dict[str, Any]]:
        """Connect to an MCP server and list its tools.

        Uses proper MCP JSON-RPC protocol:
        1. Start server process
        2. Send initialize request
        3. Send tools/list request
        4. Register discovered tools

        Args:
            server_name: Name of the server
            command: Command to start the server

        Returns:
            List of tool definitions from the server
        """
        # Emit server connection attempt event
        if self.event_bus:
            await self.event_bus.emit_with_hooks(
                EventType.MCP_SERVER_CONNECT,
                {"server_name": server_name, "command": command},
                source="mcp_integration",
            )

        # Close existing connection if any
        if server_name in self.server_connections:
            await self.server_connections[server_name].close()

        # Create new connection, injecting session auth into subprocess env
        connection = MCPServerConnection(
            server_name,
            command,
            cwd=self.workspace,
            extra_env={
                "MENTIKO_SESSION_TOKEN": self.user_token or "",
                "MENTIKO_SESSION_ID": self.session_id,
            },
        )

        if not await connection.connect():
            logger.warning(f"Failed to connect to MCP server: {server_name}")
            if self.event_bus:
                await self.event_bus.emit_with_hooks(
                    EventType.MCP_SERVER_ERROR,
                    {
                        "server_name": server_name,
                        "error": "Failed to start server process",
                    },
                    source="mcp_integration",
                )
            return []

        if not await connection.initialize():
            logger.warning(f"Failed to initialize MCP server: {server_name}")
            await connection.close()
            if self.event_bus:
                await self.event_bus.emit_with_hooks(
                    EventType.MCP_SERVER_ERROR,
                    {"server_name": server_name, "error": "Initialization failed"},
                    source="mcp_integration",
                )
            return []

        # Emit server connected event
        if self.event_bus:
            await self.event_bus.emit_with_hooks(
                EventType.MCP_SERVER_CONNECTED,
                {"server_name": server_name, "command": command},
                source="mcp_integration",
            )

        # List tools
        tools = await connection.list_tools()

        # Register tools
        for tool in tools:
            tool_name = tool.get("name")
            if tool_name:
                self.tool_registry[tool_name] = {
                    "server": server_name,
                    "definition": {
                        "name": tool_name,
                        "description": tool.get("description", ""),
                        "parameters": tool.get(
                            "inputSchema",
                            {"type": "object", "properties": {}, "required": []},
                        ),
                    },
                    "enabled": True,
                }
                logger.info(f"Registered MCP tool: {tool_name} from {server_name}")

                # Emit tool registration event
                if self.event_bus:
                    await self.event_bus.emit_with_hooks(
                        EventType.MCP_TOOL_REGISTER,
                        {
                            "tool_name": tool_name,
                            "server_name": server_name,
                            "definition": tool,
                        },
                        source="mcp_integration",
                    )

        # Keep connection open for tool calls
        self.server_connections[server_name] = connection

        return tools

    async def _discover_local_servers(self, discovered: Dict):
        """Discover locally running MCP servers."""
        # Check common MCP server locations
        common_paths = [
            Path.home() / ".mcp" / "servers",
            Path("/usr/local/mcp/servers"),
            self.workspace / ".mcp" / "servers",
        ]

        for path in common_paths:
            if path.exists():
                for server_dir in path.iterdir():
                    if server_dir.is_dir():
                        manifest = server_dir / "manifest.json"
                        if manifest.exists():
                            try:
                                with open(manifest, "r") as f:
                                    server_info = json.load(f)
                                    server_name = server_info.get(
                                        "name", server_dir.name
                                    )
                                    discovered[server_name] = {
                                        "name": server_name,
                                        "path": str(server_dir),
                                        "manifest": server_info,
                                        "status": "local",
                                    }
                                    logger.info(
                                        f"Discovered local MCP server: {server_name}"
                                    )
                            except Exception as e:
                                logger.warning(
                                    f"Failed to load manifest from {server_dir}: {e}"
                                )

    async def _validate_server(self, server_config: Dict) -> bool:
        """Validate that an MCP server is accessible.

        Args:
            server_config: Server configuration dictionary

        Returns:
            True if server is accessible, False otherwise
        """
        # Basic validation - can be extended with actual connection test
        required_fields = (
            ["command"] if server_config.get("type") == "stdio" else ["url"]
        )
        return all(field in server_config for field in required_fields)

    async def _get_server_capabilities(self, server_config: Dict) -> List[str]:
        """Get capabilities of an MCP server.

        Args:
            server_config: Server configuration dictionary

        Returns:
            List of server capabilities
        """
        capabilities = []

        # For stdio servers, we can query capabilities
        if server_config.get("type") == "stdio":
            try:
                result = await self._execute_server_command(
                    server_config.get("command", ""), "--list-tools"
                )
                if result:
                    # Parse tool list from output
                    tools = result.split("\n")
                    capabilities.extend([t.strip() for t in tools if t.strip()])
            except Exception as e:
                logger.warning(f"Failed to get server capabilities: {e}")

        return capabilities or ["unknown"]

    async def register_mcp_tool(
        self, tool_name: str, server: str, tool_definition: Optional[Dict] = None
    ) -> bool:
        """Register an MCP tool for LLM use.

        Args:
            tool_name: Name of the tool
            server: Server providing the tool
            tool_definition: Optional tool definition/schema

        Returns:
            True if registration successful
        """
        try:
            self.tool_registry[tool_name] = {
                "server": server,
                "definition": tool_definition or {},
                "enabled": True,
            }
            logger.info(f"Registered MCP tool: {tool_name} from {server}")
            return True
        except Exception as e:
            logger.error(f"Failed to register MCP tool {tool_name}: {e}")
            return False

    async def call_mcp_tool(
        self, tool_name: str, params: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute an MCP tool call using proper MCP protocol.

        Args:
            tool_name: Name of the tool to execute
            params: Parameters for the tool

        Returns:
            Tool execution result
        """
        if tool_name not in self.tool_registry:
            return {
                "error": f"Tool '{tool_name}' not found",
                "available_tools": list(self.tool_registry.keys()),
            }

        tool_info = self.tool_registry[tool_name]
        server_name = tool_info["server"]

        if not tool_info["enabled"]:
            return {"error": f"Tool '{tool_name}' is disabled"}

        # Emit tool call pre event
        if self.event_bus:
            await self.event_bus.emit_with_hooks(
                EventType.MCP_TOOL_CALL_PRE,
                {"tool_name": tool_name, "server_name": server_name, "params": params},
                source="mcp_integration",
            )

        # Get active connection
        connection = self.server_connections.get(server_name)
        if not connection or not connection.initialized:
            # Try to reconnect
            server_config = self.mcp_servers.get(server_name, {})
            command = server_config.get("command")
            if command:
                await self._connect_and_list_tools(server_name, command)
                connection = self.server_connections.get(server_name)

            if not connection or not connection.initialized:
                error_result = {
                    "error": f"No active connection to server '{server_name}'"
                }
                # Emit error event
                if self.event_bus:
                    await self.event_bus.emit_with_hooks(
                        EventType.MCP_SERVER_ERROR,
                        {"server_name": server_name, "error": "No active connection"},
                        source="mcp_integration",
                    )
                return error_result

        try:
            result = await connection.call_tool(tool_name, params)
            logger.info(f"Executed MCP tool: {tool_name}")

            # Emit tool call post event
            if self.event_bus:
                await self.event_bus.emit_with_hooks(
                    EventType.MCP_TOOL_CALL_POST,
                    {
                        "tool_name": tool_name,
                        "server_name": server_name,
                        "result": result,
                    },
                    source="mcp_integration",
                )

            return result
        except Exception as e:
            logger.error(f"Failed to execute MCP tool {tool_name}: {e}")

            error_result = {"error": str(e)}

            # Emit error event
            if self.event_bus:
                await self.event_bus.emit_with_hooks(
                    EventType.MCP_SERVER_ERROR,
                    {
                        "server_name": server_name,
                        "tool_name": tool_name,
                        "error": str(e),
                    },
                    source="mcp_integration",
                )

            return error_result

    async def _execute_stdio_tool(
        self, server_config: Dict, tool_name: str, params: Dict
    ) -> Dict[str, Any]:
        """Execute a tool via stdio MCP server.

        Args:
            server_config: Server configuration
            tool_name: Tool to execute
            params: Tool parameters

        Returns:
            Tool execution result
        """
        command = server_config.get("command", "")
        if not command:
            return {"error": "No command specified for stdio server"}

        # Validate tool_name to prevent command injection
        # Only allow alphanumeric, underscore, hyphen, and dot
        if not re.match(r"^[a-zA-Z0-9_\-\.]+$", tool_name):
            return {"error": f"Invalid tool name: {tool_name}"}

        # Build command as list (safer than shell=True)
        # Parse the command string and add tool arguments
        try:
            command_parts = shlex.split(command)
        except ValueError as e:
            return {"error": f"Invalid command format: {e}"}

        command_parts.extend(["--tool", tool_name])

        # Add parameters as JSON input
        input_json = json.dumps(params)

        def run_subprocess():
            """Run subprocess in thread to avoid blocking event loop."""
            return subprocess.run(
                command_parts,
                shell=False,
                input=input_json,
                capture_output=True,
                text=True,
                timeout=30,
            )

        try:
            # Run blocking subprocess in executor to not freeze event loop
            loop = _get_loop()
            result = await loop.run_in_executor(None, run_subprocess)

            if result.returncode == 0:
                # Try to parse JSON output
                try:
                    return json.loads(result.stdout)  # type: ignore[no-any-return]
                except json.JSONDecodeError:
                    return {"output": result.stdout}
            else:
                return {
                    "error": result.stderr
                    or f"Tool exited with code {result.returncode}"
                }

        except subprocess.TimeoutExpired:
            return {"error": "Tool execution timed out"}
        except Exception as e:
            return {"error": f"Failed to execute tool: {e}"}

    async def _execute_http_tool(
        self, server_config: Dict, tool_name: str, params: Dict
    ) -> Dict[str, Any]:
        """Execute a tool via HTTP MCP server.

        Args:
            server_config: Server configuration
            tool_name: Tool to execute
            params: Tool parameters

        Returns:
            Tool execution result
        """
        # This would implement HTTP-based MCP tool calls
        # For now, return a placeholder
        return {
            "status": "not_implemented",
            "message": "HTTP MCP servers not yet implemented",
        }

    async def _execute_server_command(self, command: str, *args) -> Optional[str]:
        """Execute a server command and return output.

        Args:
            command: Base command to execute
            *args: Additional arguments

        Returns:
            Command output or None if failed
        """
        try:
            full_command = [command] + list(args)
            result = subprocess.run(
                full_command, capture_output=True, text=True, timeout=5
            )
            if result.returncode == 0:
                return result.stdout
            return None
        except Exception as e:
            logger.warning(f"Failed to execute server command: {e}")
            return None

    def list_available_tools(self) -> List[Dict[str, Any]]:
        """List all available MCP tools.

        Returns:
            List of available tools with their information
        """
        tools = []
        for tool_name, tool_info in self.tool_registry.items():
            tools.append(
                {
                    "name": tool_name,
                    "server": tool_info["server"],
                    "enabled": tool_info["enabled"],
                    "definition": tool_info.get("definition", {}),
                }
            )
        return tools

    def get_tool_definitions_for_api(self) -> List[Dict[str, Any]]:
        """Convert registered MCP tools to API tool schema format.

        Returns generic format that adapters (OpenAI/Anthropic) auto-convert:
        - OpenAI wraps in: {type: "function", function: {...}}
        - Anthropic uses: {name, description, input_schema}

        Returns:
            List of tool definitions in generic API format
        """
        tools = []

        # Add MCP tools from registry
        for tool_name, tool_info in self.tool_registry.items():
            if not tool_info.get("enabled", True):
                continue

            definition = tool_info.get("definition", {})
            tools.append(
                {
                    "name": tool_name,
                    "description": definition.get(
                        "description", f"MCP tool: {tool_name}"
                    ),
                    "parameters": definition.get(
                        "parameters",
                        definition.get(
                            "inputSchema",
                            {"type": "object", "properties": {}, "required": []},
                        ),
                    ),
                }
            )

        # Add built-in tools from unified registry
        registry_tools = self._get_registry_tools()
        if registry_tools is not None:
            tools.extend(registry_tools)
        else:
            logger.warning("Registry tools unavailable and no fallback — skipping built-in tools")

        logger.debug(
            f"Prepared {len(tools)} tools for API ({len(self.tool_registry)} MCP + file ops)"
        )
        return tools

    def _get_bundle_tool_list(self) -> Optional[List[str]]:
        """Get the list of tool names allowed by the current agent bundle.

        Returns None if no bundle is active (legacy: grant all tools).
        Returns a list of canonical tool names (hyphenated) from the
        bundle's agent.json "tools" field.
        """
        # Try to get from agent_manager if available
        agent_manager = getattr(self, '_agent_manager', None)
        if agent_manager:
            active_agent = agent_manager.get_active_agent()
            tools = get_agent_tool_scope(active_agent)
            if tools:
                return tools

        # No bundle — all tools (legacy default)
        return None

    def _get_registry_tools(self) -> Optional[List[Dict[str, Any]]]:
        """Get built-in tools from the unified tool registry.

        Returns None if the registry is not available (falls back
        to hardcoded definitions). Returns a list of generic tool
        dicts compatible with OpenAI/Anthropic adapters.

        Enabled by default. Set config kollabor.tool_registry.use_registry
        to False to fall back to hardcoded definitions.
        """
        try:
            config = getattr(self, 'config', None)
            use_registry = (
                config.get("kollabor.tool_registry.use_registry", True)
                if config
                else True
            )
            if not use_registry:
                return None

            from kollabor_agent.tool_registry import get_registry

            registry = get_registry()

            # Scope to bundle tools if available
            allowed = self._get_bundle_tool_list()
            if allowed is not None:
                tools = registry.get_for_bundle(allowed)
            else:
                tools = registry.list()

            result = []
            for tool in tools:
                schema = tool.to_json_schema()
                result.append({
                    "name": schema["name"],
                    "description": schema["description"],
                    "parameters": schema["parameters"],
                })
            return result
        except Exception as e:
            logger.warning(f"Registry tools unavailable, falling back: {e}")
            return None


    # NOTE: _get_file_operation_tools() removed — all built-in tool
    # definitions now come from the unified tool registry via
    # _get_registry_tools(). See tool_definitions/ package.

    def enable_tool(self, tool_name: str) -> bool:
        """Enable an MCP tool.

        Args:
            tool_name: Name of the tool to enable

        Returns:
            True if tool was enabled
        """
        if tool_name in self.tool_registry:
            self.tool_registry[tool_name]["enabled"] = True
            logger.info(f"Enabled MCP tool: {tool_name}")
            return True
        return False

    def disable_tool(self, tool_name: str) -> bool:
        """Disable an MCP tool.

        Args:
            tool_name: Name of the tool to disable

        Returns:
            True if tool was disabled
        """
        if tool_name in self.tool_registry:
            self.tool_registry[tool_name]["enabled"] = False
            logger.info(f"Disabled MCP tool: {tool_name}")
            return True
        return False

    async def shutdown(self):
        """Shutdown MCP integration and close all server connections."""
        for server_name, connection in self.server_connections.items():
            try:
                # Emit MCP_SERVER_DISCONNECT for config hooks
                if self.event_bus:
                    await self.event_bus.emit_with_hooks(
                        EventType.MCP_SERVER_DISCONNECT,
                        {"server_name": server_name, "name": server_name},
                        source="mcp_integration",
                    )
                await connection.close()
                logger.debug(f"Closed MCP server connection: {server_name}")
            except Exception as e:
                logger.warning(f"Error closing MCP connection {server_name}: {e}")

        self.server_connections.clear()
        self.tool_registry.clear()
        logger.info("MCP Integration shutdown complete")
