"""Tool execution engine for terminal commands, MCP tools, and file operations.

Provides unified execution interface for terminal commands, MCP tool calls, and
file operations with proper error handling, logging, and result processing.
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from kollabor_events.models import EventType

from .file_operations_executor import FileOperationsExecutor
from .mcp_integration import MCPIntegration
from .shell_executor import ShellExecutor

logger = logging.getLogger(__name__)


class ToolExecutionResult:
    """Result of tool execution."""

    def __init__(
        self,
        tool_id: str,
        tool_type: str,
        success: bool,
        output: str = "",
        error: str = "",
        execution_time: float = 0.0,
        metadata: Optional[Dict[str, Any]] = None,
    ):
        """Initialize tool execution result.

        Args:
            tool_id: Unique identifier for the tool
            tool_type: Type of tool (terminal, mcp_tool, file_edit, etc.)
            success: Whether execution was successful
            output: Tool output/result
            error: Error message if failed
            execution_time: Execution time in seconds
            metadata: Additional metadata (e.g., diff_info for file edits)
        """
        self.tool_id = tool_id
        self.tool_type = tool_type
        self.success = success
        self.output = output
        self.error = error
        self.execution_time = execution_time
        self.metadata = metadata or {}
        self.timestamp = time.time()

    def to_dict(self) -> Dict[str, Any]:
        """Convert result to dictionary."""
        return {
            "tool_id": self.tool_id,
            "tool_type": self.tool_type,
            "success": self.success,
            "output": self.output,
            "error": self.error,
            "execution_time": self.execution_time,
            "timestamp": self.timestamp,
        }

    def __str__(self) -> str:
        """String representation of result."""
        status = "SUCCESS" if self.success else "FAILED"
        return (
            f"[{status}] {self.tool_type}:{self.tool_id} ({self.execution_time:.2f}s)"
        )


class ToolExecutor:
    """Execute tools with unified interface for terminal, MCP, and file operations.

    Handles execution of terminal commands, MCP tool calls, and file operations
    with proper error handling, timeouts, and result logging.
    """

    def __init__(
        self,
        mcp_integration: MCPIntegration,
        event_bus,
        terminal_timeout: int = 90,
        mcp_timeout: int = 180,
        config=None,
        renderer=None,
        tmux_plugin=None,
        workspace=None,
    ):
        """Initialize tool executor.

        Args:
            mcp_integration: MCP integration instance
            event_bus: Event bus for hook emissions
            terminal_timeout: Timeout for terminal commands in seconds
            mcp_timeout: Timeout for MCP tool calls in seconds
            config: Configuration manager (optional)
            renderer: Terminal renderer for tool execution state (optional)
            tmux_plugin: TmuxPlugin instance for terminal execution (optional)
            workspace: Default working directory for terminal and file tools
        """
        self.mcp_integration = mcp_integration
        self.event_bus = event_bus
        self.terminal_timeout = terminal_timeout
        self.mcp_timeout = mcp_timeout
        self.renderer = renderer  # Store renderer for tool execution state
        self.tmux_plugin = tmux_plugin  # Store tmux plugin reference
        self._config = config
        self.workspace = Path(workspace).expanduser().resolve() if workspace else None

        # Create own shell executor instance (fallback if tmux_plugin not available)
        # Check config for interactive shell mode (sources .zshrc/.bashrc for aliases)
        interactive_shell = False
        if config:
            interactive_shell = config.get("terminal.interactive_shell", False)
        self.shell_executor = ShellExecutor(interactive=interactive_shell)

        # True while a tool is genuinely executing. The TurnWatchdog reads this
        # (like an in-flight API call) so a long-running foreground tool — e.g.
        # a 10-minute build — is never mistaken for a wedged session and
        # interrupted. Independent of the renderer so it works headless.
        self.tool_executing = False

        # File operations executor
        self.file_ops_executor = FileOperationsExecutor(
            config=config,
            workspace=self.workspace,
        )
        self.file_ops_executor.event_bus = event_bus

        # Plugin-registered tool handlers (populated via register_plugin_handler)
        self._plugin_handlers: Dict[
            str, Callable[[Dict[str, Any]], Awaitable["ToolExecutionResult"]]
        ] = {}

        # Bundle scope enforcement
        self._bundle_tools: Optional[List[str]] = None
        """If set, only these tool names are allowed. None = all tools allowed."""
        self._enforce_bundle_scope: bool = True
        """Whether to enforce bundle scope. Can be toggled via config."""

        # Execution statistics
        self.stats = {
            "total_executions": 0,
            "successful_executions": 0,
            "failed_executions": 0,
            "terminal_executions": 0,
            "mcp_executions": 0,
            "file_op_executions": 0,
            "total_execution_time": 0.0,
        }

        # Cancellation callback - checked between tool executions
        self._cancel_callback = None

        # Track active shell executor so ESC can cancel running subprocesses
        self._active_shell_executor: Optional[ShellExecutor] = None

        logger.info(
            "Tool executor initialized with terminal, MCP, and file operations support"
        )

    def set_cancel_callback(self, callback):
        """Set callback to check for cancellation requests.

        Args:
            callback: Callable that returns True if cancellation is requested
        """
        self._cancel_callback = callback

    def set_bundle_scope(self, allowed_tools: Optional[List[str]]) -> None:
        """Set the bundle scope for tool access control.

        Args:
            allowed_tools: List of registry tool names (e.g. ['file-read',
                'terminal', 'hub-msg']) this agent has access to.
                None = all tools allowed (legacy default).
        """
        self._bundle_tools = allowed_tools
        logger.debug(
            f"Bundle scope set: {len(allowed_tools) if allowed_tools else 'all'} tools"
        )

    def clear_bundle_scope(self) -> None:
        """Clear bundle scope, allowing all tools."""
        self._bundle_tools = None

    # Internal dispatch types that bypass scope checks.
    # These are infrastructure types, not agent-facing tools.
    _INTERNAL_TYPES = frozenset({
        "terminal_status",
        "terminal_output",
        "terminal_kill",
        "mcp_tool",
        "malformed_file_op",
        "malformed_tool",
        "unknown",
    })

    def _tool_type_to_registry_name(self, tool_type: str) -> Optional[str]:
        """Convert a tool_type (from dispatch) to a registry tool name.

        Uses the registry's reverse lookup (get_by_native_name,
        get_by_xml_tag) to find the canonical name. Falls back to
        simple underscore-to-hyphen conversion.

        Args:
            tool_type: The 'type' field from parsed tool data.

        Returns:
            Registry-compatible name, or None if it's an internal type.
        """
        # Internal dispatch types — not agent-facing tools
        if tool_type in self._INTERNAL_TYPES:
            return None

        # Try registry reverse lookups
        try:
            from .tool_registry import get_registry
            registry = get_registry()

            # Try native name lookup (handles file_mkdir -> directory)
            tool = registry.get_by_native_name(tool_type)
            if tool:
                return tool.name

            # Try xml tag lookup (handles hub_msg -> hub-msg)
            tool = registry.get_by_xml_tag(tool_type)
            if tool:
                return tool.name
        except Exception:
            pass  # Registry not available — fall through

        # Fallback: simple underscore-to-hyphen
        return tool_type.replace("_", "-")

    def _check_bundle_scope(self, tool_type: str) -> Optional[str]:
        """Check if a tool_type is allowed by the bundle scope.

        Args:
            tool_type: The dispatch tool type to check.

        Returns:
            None if allowed, error message string if denied.
        """
        if not self._enforce_bundle_scope:
            return None

        if self._bundle_tools is None:
            return None  # Legacy: all tools allowed

        # Internal dispatch types bypass scope
        if tool_type in self._INTERNAL_TYPES:
            return None

        registry_name = self._tool_type_to_registry_name(tool_type)

        # Internal type (returned None)
        if registry_name is None:
            return None

        # Direct match against bundle tools
        if registry_name in self._bundle_tools:
            return None

        # Also check the original tool_type (some tools like 'terminal'
        # are the same in both forms)
        if tool_type in self._bundle_tools:
            return None

        # Tool not in scope
        sorted_tools = sorted(set(self._bundle_tools))
        return (
            f"[{tool_type}] this agent does not have access to the "
            f"{registry_name} tool. available tools: "
            f"{', '.join(sorted_tools)}"
        )

    def register_plugin_handler(
        self,
        tool_type: str,
        handler: Callable[[Dict[str, Any]], Awaitable["ToolExecutionResult"]],
    ) -> None:
        """Register a plugin tool handler.

        When execute_tool encounters this tool_type, it routes to
        the registered handler instead of the built-in if/elif chain.

        Args:
            tool_type: matches tool_type from parser registration
            handler: async function that executes the tool and returns ToolExecutionResult
        """
        self._plugin_handlers[tool_type] = handler
        logger.debug(f"Registered plugin handler for tool type: {tool_type}")

    @property
    def plugin_handlers(self) -> Dict[str, Any]:
        """Return registered plugin handlers dict (for native_tools_handler routing)."""
        return self._plugin_handlers

    def is_cancelled(self) -> bool:
        """Check if cancellation has been requested."""
        if self._cancel_callback:
            return self._cancel_callback()
        return False

    async def cancel_running_tool(self) -> None:
        """Cancel any currently running shell subprocess.

        Called by the ESC/cancel handler to interrupt a long-running
        terminal command that is stuck inside asyncio.wait_for().
        """
        if self._active_shell_executor is not None:
            logger.info("Cancelling active shell executor subprocess")
            await self._active_shell_executor.cancel()
            self._active_shell_executor = None

        # Also cancel via tmux plugin if it has a running foreground process
        if self.tmux_plugin and hasattr(self.tmux_plugin, "_active_executor"):
            active = getattr(self.tmux_plugin, "_active_executor", None)
            if active is not None:
                logger.info("Cancelling tmux plugin foreground subprocess")
                await active.cancel()
                self.tmux_plugin._active_executor = None

    async def execute_tool(self, tool_data: Dict[str, Any]) -> ToolExecutionResult:
        """Execute a single tool (terminal, MCP, or file operation).

        Args:
            tool_data: Tool information from ResponseParser

        Returns:
            Tool execution result
        """
        tool_type = tool_data.get("type", "unknown")
        tool_id = tool_data.get("id", "unknown")

        # Check for cancellation before starting
        if self.is_cancelled():
            logger.info(f"Tool {tool_id} skipped - cancellation requested")
            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type=tool_type,
                success=False,
                error="Cancelled by user",
                metadata={"cancelled": True},
            )

        # Check bundle scope — reject tools not in the agent's allowed set
        scope_error = self._check_bundle_scope(tool_type)
        if scope_error:
            logger.warning(f"Tool {tool_id} rejected by bundle scope: {tool_type}")
            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type=tool_type,
                success=False,
                error=scope_error,
                metadata={"scope_denied": True},
            )

        # Set tool executing state for spinner animation
        tool_name = self._get_display_name(tool_data)
        self.tool_executing = True  # watchdog: real work in flight
        if self.renderer:
            self.renderer.set_tool_executing(True, tool_name)

        start_time = time.time()

        try:
            # Emit pre-execution hook (includes permission check)
            logger.info(f"[DIAG] Emitting TOOL_CALL_PRE for {tool_id}")
            pre_call_results = await self.event_bus.emit_with_hooks(
                EventType.TOOL_CALL_PRE, {"tool_data": tool_data}, "tool_executor"
            )
            logger.info(f"[DIAG] TOOL_CALL_PRE completed for {tool_id}")

            # Check if event was cancelled (permission denied)
            if pre_call_results and pre_call_results.get("cancelled", False):
                # Get permission decision details from the final data
                final_data = pre_call_results.get("main", {}).get("final_data", {})
                permission_decision = final_data.get("permission_decision", {})
                reason = permission_decision.get("reason", "Permission denied")

                logger.warning(f"Tool {tool_id} execution denied: {reason}")
                return ToolExecutionResult(
                    tool_id=tool_id,
                    tool_type=tool_type,
                    success=False,
                    error=reason,
                    execution_time=time.time() - start_time,
                    metadata={"permission_denied": True},
                )

            # Execute based on tool type
            try:
                logger.debug(f"Executing tool {tool_id} of type {tool_type}")
                try:
                    # Check plugin handlers BEFORE built-in types.
                    # Normalize hyphen->underscore: native tools use hyphens
                    # (hub-msg) but handlers register with underscores (hub_msg).
                    _plugin_key = tool_type
                    if _plugin_key not in self._plugin_handlers:
                        _plugin_key = tool_type.replace("-", "_")
                    if _plugin_key in self._plugin_handlers:
                        logger.debug(
                            f"Routing to plugin handler for {_plugin_key}"
                        )
                        result = await self._plugin_handlers[_plugin_key](tool_data)
                    elif tool_type in (
                        "terminal",
                        "terminal_status",
                        "terminal_output",
                        "terminal_kill",
                    ):
                        logger.debug(
                            f"About to call _execute_terminal_command for {tool_id}"
                        )
                        result = await self._execute_terminal_command(tool_data)
                        logger.debug(
                            f"_execute_terminal_command completed for {tool_id}"
                        )
                    elif tool_type == "mcp_tool":
                        logger.debug(f"About to call _execute_mcp_tool for {tool_id}")
                        result = await self._execute_mcp_tool(tool_data)
                        logger.debug(f"_execute_mcp_tool completed for {tool_id}")
                    elif tool_type in ("web_fetch", "web-fetch"):
                        logger.debug(f"About to call _execute_web_fetch for {tool_id}")
                        result = await self._execute_web_fetch(tool_data)
                        logger.debug(f"_execute_web_fetch completed for {tool_id}")
                    elif tool_type in ("web_search", "web-search"):
                        logger.debug(f"About to call _execute_web_search for {tool_id}")
                        result = await self._execute_web_search(tool_data)
                        logger.debug(f"_execute_web_search completed for {tool_id}")
                    elif (
                        tool_type.startswith("file_")
                        or tool_type == "malformed_file_op"
                    ):
                        # File operation (including malformed ops for error reporting)
                        logger.debug(
                            f"About to call _execute_file_operation for {tool_id}"
                        )
                        result = await self._execute_file_operation(tool_data)
                        logger.debug(f"_execute_file_operation completed for {tool_id}")
                    elif tool_type in ("workspace_set", "workspace-set"):
                        logger.debug(
                            f"About to call _execute_workspace_set for {tool_id}"
                        )
                        result = await self._execute_workspace_set(tool_data)
                        logger.debug(
                            f"_execute_workspace_set completed for {tool_id}"
                        )
                    elif tool_type in ("mcp_reload", "mcp-reload"):
                        logger.debug(
                            f"About to call _execute_mcp_reload for {tool_id}"
                        )
                        result = await self._execute_mcp_reload(tool_data)
                        logger.debug(
                            f"_execute_mcp_reload completed for {tool_id}"
                        )
                    else:
                        result = ToolExecutionResult(
                            tool_id=tool_id,
                            tool_type=tool_type,
                            success=False,
                            error=f"Unknown tool type: {tool_type}",
                        )
                    logger.debug(
                        f"Tool {tool_id} execution result: success={result.success}"
                    )
                except Exception as inner_e:
                    import traceback

                    inner_trace = traceback.format_exc()
                    logger.error(f"Inner execution error for {tool_id}: {str(inner_e)}")
                    logger.error(
                        f"Inner execution traceback for {tool_id}: {inner_trace}"
                    )
                    raise  # Re-raise for outer handler
            except Exception as e:
                import traceback

                error_details = f"Tool execution exception: {str(e)}\nTraceback: {traceback.format_exc()}"
                logger.error(
                    f"Critical error during tool {tool_id} execution: {error_details}"
                )
                result = ToolExecutionResult(
                    tool_id=tool_id,
                    tool_type=tool_type,
                    success=False,
                    error=f"Tool execution error: {str(e)}",
                )

            # Update execution time
            result.execution_time = time.time() - start_time

            # Emit post-execution hook
            await self.event_bus.emit_with_hooks(
                EventType.TOOL_CALL_POST,
                {"tool_data": tool_data, "result": result.to_dict()},
                "tool_executor",
            )

            # Update statistics
            self._update_stats(result)

            logger.info(f"Tool execution completed: {result}")
            return result

        except Exception as e:
            execution_time = time.time() - start_time
            error_result = ToolExecutionResult(
                tool_id=tool_id,
                tool_type=tool_type,
                success=False,
                error=f"Execution error: {str(e)}",
                execution_time=execution_time,
            )

            self._update_stats(error_result)
            logger.error(f"Tool execution failed: {e}")
            return error_result
        finally:
            # Always clear tool executing state, even if exception occurs
            self.tool_executing = False
            if self.renderer:
                self.renderer.set_tool_executing(False)

    async def execute_all_tools(
        self, tools: List[Dict[str, Any]]
    ) -> List[ToolExecutionResult]:
        """Execute multiple tools in sequence.

        Args:
            tools: List of tool data from ResponseParser

        Returns:
            List of execution results in order
        """
        if not tools:
            return []

        logger.info(f"Executing {len(tools)} tools in sequence")
        results = []

        for i, tool_data in enumerate(tools):
            # Check for cancellation between tools
            if self.is_cancelled():
                logger.info(f"Tool batch cancelled at {i+1}/{len(tools)}")
                # Add cancelled results for remaining tools
                for remaining in tools[i:]:
                    results.append(
                        ToolExecutionResult(
                            tool_id=remaining.get("id", "unknown"),
                            tool_type=remaining.get("type", "unknown"),
                            success=False,
                            error="Cancelled by user",
                            metadata={"cancelled": True},
                        )
                    )
                break

            logger.info(
                f"[DIAG] About to execute tool {i+1}/{len(tools)}: {tool_data.get('id', 'unknown')}"
            )

            result = await self.execute_tool(tool_data)
            logger.info(
                f"[DIAG] Completed tool {i+1}/{len(tools)}: success={result.success}"
            )
            results.append(result)

            # Log intermediate result
            if result.success:
                logger.debug(f"Tool {i+1} succeeded: {len(result.output)} chars output")
            else:
                logger.warning(f"Tool {i+1} failed: {result.error}")
                # Continue executing remaining tools even if one fails

        cancelled_count = sum(1 for r in results if r.metadata.get("cancelled"))
        if cancelled_count:
            logger.info(
                f"Tool execution batch: {cancelled_count} cancelled, "
                f"{sum(1 for r in results if r.success)}/{len(results)} successful"
            )
        else:
            logger.info(
                f"Tool execution batch completed: "
                f"{sum(1 for r in results if r.success)}/{len(results)} successful"
            )

        return results

    async def _execute_terminal_command(
        self, tool_data: Dict[str, Any]
    ) -> ToolExecutionResult:
        """Execute a terminal command using TmuxPlugin or fallback to ShellExecutor.

        Routes to appropriate handler based on tool_type:
        - terminal: Regular execution (foreground or background)
        - terminal_status: Get session status
        - terminal_output: Capture session output
        - terminal_kill: Kill session

        Args:
            tool_data: Terminal tool data with command and attributes

        Returns:
            Execution result
        """
        tool_id = tool_data.get("id", "unknown")
        tool_type = tool_data.get("type", "terminal")

        # Route to specific handler based on type
        if tool_type == "terminal_status":
            return await self._execute_terminal_status(tool_data)
        elif tool_type == "terminal_output":
            return await self._execute_terminal_output(tool_data)
        elif tool_type == "terminal_kill":
            return await self._execute_terminal_kill(tool_data)

        # Regular terminal execution (foreground or background)
        command = tool_data.get("command", "").strip()
        background = tool_data.get("background", False)

        if not command:
            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type=tool_type,
                success=False,
                error="Empty command",
            )

        # Block agents from spawning kollab processes via terminal.
        # Agents MUST use hub_spawn to spawn peers -- direct spawning
        # bypasses the hub mesh and creates invisible rogue processes.
        import re as _re

        _spawn_pat = _re.compile(
            r"(?:python3?\s+main\.py|kollab)\s+.*(?:--detached|--agent|&\s*$)",
            _re.IGNORECASE,
        )
        if _spawn_pat.search(command):
            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type=tool_type,
                success=False,
                error=(
                    "spawning agents via terminal is blocked. "
                    "use <hub_spawn name=\"agent-name\">task</hub_spawn> instead."
                ),
            )

        logger.debug(
            f"Executing terminal command: {command[:100]}... (background={background})"
        )

        cwd = self._resolve_terminal_cwd(tool_data.get("cwd"))

        # Use TmuxPlugin if available, otherwise fallback to ShellExecutor
        if self.tmux_plugin:
            try:
                if background:
                    # Persistent background session via subprocess
                    result = await self.tmux_plugin.execute_background(
                        command=command,
                        name=tool_data.get("name"),
                        timeout=tool_data.get("timeout"),
                        cwd=str(cwd) if cwd else None,
                    )
                    return ToolExecutionResult(
                        tool_id=tool_id,
                        tool_type=tool_type,
                        success=result.get("success", False),
                        output=result.get("message", "Background session started"),
                        error=(
                            ""
                            if result.get("success")
                            else result.get("message", "Unknown error")
                        ),
                        metadata={"session_name": result.get("session_name")},
                    )
                else:
                    # Foreground execution via temporary subprocess
                    result = await self.tmux_plugin.execute_foreground(
                        command=command,
                        timeout=self.terminal_timeout,
                        cwd=str(cwd) if cwd else None,
                    )
                    return ToolExecutionResult(
                        tool_id=tool_id,
                        tool_type=tool_type,
                        success=result.get("success", False),
                        output=result.get("output", ""),
                        error=result.get("error", ""),
                        metadata={"exit_code": result.get("exit_code", -1)},
                    )
            except Exception as e:
                logger.error(f"Error executing terminal command via subprocess: {e}")
                # Fallback to ShellExecutor
                logger.debug("Falling back to ShellExecutor for command execution")

        # Fallback: Use ShellExecutor (no terminal plugin available or it failed)
        self._active_shell_executor = self.shell_executor
        try:
            result = await self.shell_executor.run(
                command,
                timeout=self.terminal_timeout,
                cwd=cwd,
            )
        finally:
            self._active_shell_executor = None

        if result.error:
            return ToolExecutionResult(
                tool_id=tool_id, tool_type=tool_type, success=False, error=result.error
            )

        output = result.stdout if result.success else result.stderr
        error = (
            "" if result.success else f"Exit code {result.exit_code}: {result.stderr}"
        )

        return ToolExecutionResult(
            tool_id=tool_id,
            tool_type=tool_type,
            success=result.success,
            output=output,
            error=error,
        )

    async def _execute_terminal_status(
        self, tool_data: Dict[str, Any]
    ) -> ToolExecutionResult:
        """Handle <terminal-status>session_name</terminal-status>

        Args:
            tool_data: Tool data with session_name

        Returns:
            Execution result with session status
        """
        tool_id = tool_data.get("id", "unknown")
        session_name = tool_data.get("session_name", "*")

        if not self.tmux_plugin:
            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type="terminal_status",
                success=False,
                error="Tmux plugin not available",
            )

        try:
            result = await self.tmux_plugin.get_session_status(session_name)

            if result.get("success"):
                if session_name == "*":
                    # Format list of sessions
                    sessions = result.get("sessions", [])
                    if not sessions:
                        output = "No sessions found"
                    else:
                        lines = ["Active sessions:"]
                        for session in sessions:
                            lines.append(
                                f"  - {session['name']}: {session['status']} ({session['command']})"
                            )
                        output = "\n".join(lines)
                else:
                    # Single session status
                    output = f"Session '{session_name}': {result['status']} (command: {result['command']})"
            else:
                output = result.get("message", "Failed to get session status")

            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type="terminal_status",
                success=result.get("success", False),
                output=output,
                error="" if result.get("success") else result.get("message", ""),
            )
        except Exception as e:
            logger.error(f"Error getting terminal status: {e}")
            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type="terminal_status",
                success=False,
                error=str(e),
            )

    async def _execute_terminal_output(
        self, tool_data: Dict[str, Any]
    ) -> ToolExecutionResult:
        """Handle <terminal-output lines="50">session_name</terminal-output>

        Args:
            tool_data: Tool data with session_name and lines

        Returns:
            Execution result with captured output
        """
        tool_id = tool_data.get("id", "unknown")
        session_name = tool_data.get("session_name", "")
        lines = tool_data.get("lines", 50)

        if not self.tmux_plugin:
            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type="terminal_output",
                success=False,
                error="Tmux plugin not available",
            )

        if not session_name:
            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type="terminal_output",
                success=False,
                error="Session name required",
            )

        try:
            result = await self.tmux_plugin.capture_session_output(
                session_name, lines=lines
            )

            if result.get("success"):
                output_lines = result.get("output", [])
                output = f"Output from session '{session_name}' (last {lines} lines):\n"
                output += "\n".join(output_lines)
            else:
                output = result.get("message", "Failed to capture output")

            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type="terminal_output",
                success=result.get("success", False),
                output=output,
                error="" if result.get("success") else result.get("message", ""),
            )
        except Exception as e:
            logger.error(f"Error capturing terminal output: {e}")
            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type="terminal_output",
                success=False,
                error=str(e),
            )

    async def _execute_terminal_kill(
        self, tool_data: Dict[str, Any]
    ) -> ToolExecutionResult:
        """Handle <terminal-kill>session_name</terminal-kill>

        Args:
            tool_data: Tool data with session_name

        Returns:
            Execution result
        """
        tool_id = tool_data.get("id", "unknown")
        session_name = tool_data.get("session_name", "")

        if not self.tmux_plugin:
            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type="terminal_kill",
                success=False,
                error="Tmux plugin not available",
            )

        if not session_name:
            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type="terminal_kill",
                success=False,
                error="Session name required",
            )

        try:
            result = await self.tmux_plugin.kill_background_session(session_name)

            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type="terminal_kill",
                success=result.get("success", False),
                output=result.get("message", ""),
                error="" if result.get("success") else result.get("message", ""),
            )
        except Exception as e:
            logger.error(f"Error killing terminal session: {e}")
            return ToolExecutionResult(
                tool_id=tool_id, tool_type="terminal_kill", success=False, error=str(e)
            )

    async def _execute_mcp_tool(self, tool_data: Dict[str, Any]) -> ToolExecutionResult:
        """Execute an MCP tool call.

        Args:
            tool_data: MCP tool data with name and arguments

        Returns:
            Execution result
        """
        tool_name = tool_data.get("name", "")
        tool_arguments = tool_data.get("arguments", {})
        tool_id = tool_data.get("id", "unknown")

        if not tool_name:
            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type="mcp_tool",
                success=False,
                error="Missing tool name",
            )

        logger.debug(f"Executing MCP tool: {tool_name} with args {tool_arguments}")

        try:
            # MCPIntegration owns call timeout cleanup so it can close and
            # reconnect poisoned stdio connections before later tool calls.
            mcp_result = await self.mcp_integration.call_mcp_tool(
                tool_name,
                tool_arguments,
                timeout=self.mcp_timeout,
            )

            # Process MCP result
            if "error" in mcp_result:
                return ToolExecutionResult(
                    tool_id=tool_id,
                    tool_type="mcp_tool",
                    success=False,
                    error=mcp_result["error"],
                )
            else:
                # Format MCP output for display
                output = self._format_mcp_output(mcp_result)

                return ToolExecutionResult(
                    tool_id=tool_id, tool_type="mcp_tool", success=True, output=output
                )

        except Exception as e:
            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type="mcp_tool",
                success=False,
                error=f"MCP execution error: {str(e)}",
            )

    def _format_mcp_output(self, mcp_result: Dict[str, Any]) -> str:
        """Format MCP tool result for display.

        Args:
            mcp_result: Raw MCP result dictionary

        Returns:
            Formatted output string
        """
        # Handle different MCP result formats
        if "content" in mcp_result:
            # Standard MCP content format
            content = mcp_result["content"]
            if isinstance(content, list) and content:
                # Multiple content blocks
                parts = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            parts.append(block.get("text", ""))
                        else:
                            parts.append(str(block))
                    else:
                        parts.append(str(block))
                return "\n".join(parts)
            else:
                return str(content)

        elif "output" in mcp_result:
            # Simple output format
            return str(mcp_result["output"])

        elif "result" in mcp_result:
            # JSON-RPC result format
            return str(mcp_result["result"])

        else:
            # Fallback: stringify entire result
            return str(mcp_result)

    async def _execute_file_operation(
        self, tool_data: Dict[str, Any]
    ) -> ToolExecutionResult:
        """Execute a file operation.

        Args:
            tool_data: File operation data from parser

        Returns:
            Tool execution result
        """
        tool_id = tool_data.get("id", "unknown")
        tool_type = tool_data.get("type", "unknown")

        logger.debug(f"Executing file operation: {tool_type}")

        # Run file operation synchronously (file I/O is blocking anyway)
        # Use asyncio.to_thread to avoid blocking the event loop
        try:
            result_dict = await asyncio.to_thread(
                self.file_ops_executor.execute_operation, tool_data
            )

            # Convert to ToolExecutionResult, preserving metadata (e.g., diff_info)
            metadata = {}
            if "diff_info" in result_dict:
                metadata["diff_info"] = result_dict["diff_info"]
            # Propagate file path for context-service ledger ingestion
            file_path = (
                tool_data.get("file")
                or tool_data.get("file_path")
                or tool_data.get("arguments", {}).get("path")
                or tool_data.get("arguments", {}).get("file_path")
                or tool_data.get("from")  # move/copy source
            )
            if file_path:
                metadata["file_path"] = self._resolve_workspace_path(file_path)
            to_path = tool_data.get("to")  # move/copy destination
            if to_path:
                metadata["to_path"] = self._resolve_workspace_path(to_path)

            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type=tool_type,
                success=result_dict.get("success", False),
                output=result_dict.get("output", ""),
                error=result_dict.get("error", ""),
                metadata=metadata,
            )

        except Exception as e:
            import traceback

            error_trace = traceback.format_exc()
            logger.error(f"File operation execution failed: {error_trace}")
            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type=tool_type,
                success=False,
                error=f"File operation error: {str(e)}",
            )

    def _resolve_workspace_path(self, file_path: str) -> str:
        """Resolve relative file metadata against the executor workspace."""
        try:
            path = Path(file_path).expanduser()
            if path.is_absolute() or self.workspace is None:
                return str(path)
            return str((self.workspace / path).resolve())
        except Exception:
            return file_path

    def _resolve_terminal_cwd(self, cwd_value: Optional[str]) -> Optional[Path]:
        """Resolve terminal cwd relative to the executor workspace when present."""
        if not cwd_value:
            return self.workspace

        path = Path(cwd_value).expanduser()
        if not path.is_absolute() and self.workspace is not None:
            path = self.workspace / path
        return path.resolve()

    async def _execute_workspace_set(
        self, tool_data: Dict[str, Any]
    ) -> ToolExecutionResult:
        """Switch the agent's working directory.

        Updates self.workspace and calls os.chdir() so the status bar
        cwd widget reflects the change immediately.

        Args:
            tool_data: Tool information containing the path parameter.

        Returns:
            ToolExecutionResult with confirmation or error.
        """
        import os

        tool_id = tool_data.get("id", "unknown")

        # Extract path from tool_data — support both nested XML and flat params
        params = tool_data.get("parameters", tool_data)
        raw_path = (
            params.get("path")
            or params.get("Path")
            or tool_data.get("path")
        )

        if not raw_path:
            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type="workspace_set",
                success=False,
                error="Missing required parameter: path",
            )

        try:
            # Expand ~ and resolve relative to current workspace
            expanded = Path(raw_path).expanduser()
            if not expanded.is_absolute() and self.workspace is not None:
                expanded = self.workspace / expanded
            resolved = expanded.resolve()

            if not resolved.exists():
                return ToolExecutionResult(
                    tool_id=tool_id,
                    tool_type="workspace_set",
                    success=False,
                    error=f"Directory not found: {raw_path}",
                )

            if not resolved.is_dir():
                return ToolExecutionResult(
                    tool_id=tool_id,
                    tool_type="workspace_set",
                    success=False,
                    error=f"Not a directory: {raw_path}",
                )

            # Update workspace + process cwd
            old_workspace = str(self.workspace) if self.workspace else str(Path.cwd())
            self.workspace = resolved
            os.chdir(str(resolved))

            logger.info(f"Workspace switched: {old_workspace} -> {resolved}")

            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type="workspace_set",
                success=True,
                output=f"Workspace changed to: {resolved}",
                metadata={
                    "old_workspace": old_workspace,
                    "new_workspace": str(resolved),
                },
            )

        except PermissionError:
            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type="workspace_set",
                success=False,
                error=f"Permission denied: {raw_path}",
            )
        except Exception as e:
            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type="workspace_set",
                success=False,
                error=f"Failed to switch workspace: {e}",
            )

    async def _execute_mcp_reload(
        self, tool_data: Dict[str, Any]
    ) -> ToolExecutionResult:
        """Reload MCP server connections and rediscover tools.

        Calls mcp_integration.reload_mcp_servers() to close active
        connections, reload config files, and reconnect.

        Args:
            tool_data: Tool information (no required params).

        Returns:
            ToolExecutionResult with reload summary or error.
        """
        import time

        tool_id = tool_data.get("id", "unknown")
        start_time = time.time()

        if self.mcp_integration is None:
            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type="mcp_reload",
                success=False,
                error="MCP integration is not available in this session.",
            )

        try:
            counts = await self.mcp_integration.reload_mcp_servers()
            elapsed = time.time() - start_time

            configured = counts.get("configured", 0)
            discovered = counts.get("discovered", 0)
            reconnected = counts.get("reconnected", 0)

            # Count total tools discovered
            total_tools = 0
            if hasattr(self.mcp_integration, "get_tool_definitions_for_api"):
                total_tools = len(
                    self.mcp_integration.get_tool_definitions_for_api()
                )

            output = (
                f"MCP servers reloaded.\n"
                f"  Configured: {configured}\n"
                f"  Discovered: {discovered}\n"
                f"  Reconnected: {reconnected}\n"
                f"  Tools available: {total_tools}"
            )

            logger.info(
                f"MCP reload complete: {reconnected}/{configured} servers, "
                f"{total_tools} tools ({elapsed:.1f}s)"
            )

            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type="mcp_reload",
                success=True,
                output=output,
                metadata={
                    "configured": configured,
                    "discovered": discovered,
                    "reconnected": reconnected,
                    "total_tools": total_tools,
                    "elapsed": round(elapsed, 2),
                },
            )

        except Exception as e:
            logger.error(f"MCP reload failed: {e}")
            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type="mcp_reload",
                success=False,
                error=f"MCP reload failed: {e}",
            )

    def _update_stats(self, result: ToolExecutionResult):
        """Update execution statistics.

        Args:
            result: Tool execution result
        """
        self.stats["total_executions"] += 1
        self.stats["total_execution_time"] += result.execution_time

        if result.success:
            self.stats["successful_executions"] += 1
        else:
            self.stats["failed_executions"] += 1

        if result.tool_type == "terminal":
            self.stats["terminal_executions"] += 1
        elif result.tool_type == "mcp_tool":
            self.stats["mcp_executions"] += 1
        elif result.tool_type.startswith("file_"):
            self.stats["file_op_executions"] += 1
        elif result.tool_type in ("web_fetch", "web_search"):
            self.stats.setdefault("web_executions", 0)
            self.stats["web_executions"] += 1
        elif result.tool_type in ("mcp_reload", "mcp-reload"):
            self.stats.setdefault("mcp_reload_executions", 0)
            self.stats["mcp_reload_executions"] += 1

    def get_execution_stats(self) -> Dict[str, Any]:
        """Get execution statistics.

        Returns:
            Dictionary of execution statistics
        """
        total = self.stats["total_executions"]
        if total == 0:
            return {**self.stats, "success_rate": 0.0, "average_time": 0.0}

        return {
            **self.stats,
            "success_rate": self.stats["successful_executions"] / total,
            "average_time": self.stats["total_execution_time"] / total,
        }

    def format_result_for_conversation(self, result: ToolExecutionResult) -> str:
        """Format tool result for conversation history.

        Args:
            result: Tool execution result

        Returns:
            Formatted string for conversation logging
        """
        if result.success:
            return f"[{result.tool_type}] {result.output}"
        else:
            return f"[{result.tool_type}] ERROR: {result.error}"

    def reset_stats(self):
        """Reset execution statistics."""
        self.stats = {
            "total_executions": 0,
            "successful_executions": 0,
            "failed_executions": 0,
            "terminal_executions": 0,
            "mcp_executions": 0,
            "file_op_executions": 0,
            "total_execution_time": 0.0,
        }
        logger.info("Tool execution statistics reset")

    # ------------------------------------------------------------------
    # WEB TOOLS (web-fetch, web-search)
    # ------------------------------------------------------------------

    async def _execute_web_fetch(self, tool_data: Dict[str, Any]) -> ToolExecutionResult:
        """Fetch a URL and return cleaned text content.

        Uses aiohttp with a 30-second timeout. Content extraction follows
        a priority chain: JSON-LD → meta tags → semantic HTML5 → readability
        heuristic. If the extracted text is suspiciously sparse (<500 chars
        from a 200 OK), retries with Playwright headless browser (if installed)
        to handle JS-rendered pages.

        Output is truncated to max_chars (default 5000).

        Args:
            tool_data: Dict with 'url' (required), 'max_chars' (optional),
                       and 'extract_main' (optional, default true).

        Returns:
            ToolExecutionResult with the cleaned text or an error message.
        """
        import aiohttp

        tool_id = tool_data.get("id", "unknown")
        url = tool_data.get("url", "").strip()
        max_chars = int(tool_data.get("max_chars", 5000))
        extract_main = tool_data.get("extract_main", True)

        if not url:
            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type="web_fetch",
                success=False,
                error="No URL provided",
            )

        # Basic URL validation
        from urllib.parse import urlparse

        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type="web_fetch",
                success=False,
                error=f"Invalid URL: {url}",
            )
        if parsed.scheme not in ("http", "https"):
            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type="web_fetch",
                success=False,
                error=f"Unsupported scheme: {parsed.scheme} (only http/https)",
            )

        try:
            timeout = aiohttp.ClientTimeout(total=30)
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (compatible; KollabAgent/1.0; "
                    "+https://github.com/kollaborai/kollab)"
                ),
            }
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers, allow_redirects=True) as resp:
                    if resp.status >= 400:
                        return ToolExecutionResult(
                            tool_id=tool_id,
                            tool_type="web_fetch",
                            success=False,
                            error=f"HTTP {resp.status} {resp.reason}",
                        )

                    # Content-Length check: warn on very large pages
                    try:
                        content_length = resp.headers.get("Content-Length", "")
                        if content_length and int(content_length) > 500_000:
                            logger.warning(
                                f"web_fetch: large page {url} ({content_length} bytes)"
                            )
                    except (ValueError, TypeError):
                        pass

                    final_url = str(resp.url)
                    raw = await resp.text(errors="replace")

            # Extract main content using priority chain
            text = self._extract_main_content(raw, extract_main)

            # Playwright fallback: if aiohttp got suspiciously little text
            # from a valid response, the page may be JS-rendered.
            if len(text) < 500:
                pw_text = await self._playwright_fetch(url)
                if pw_text and len(pw_text) > len(text):
                    text = pw_text
                    logger.info(f"web_fetch: used Playwright fallback for {url}")

            # Truncate to max_chars (accounting for the URL header)
            header = f"URL: {final_url}\n\n"
            available = max(100, max_chars - len(header))
            if len(text) > available:
                text = text[:available] + "\n...[truncated]"

            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type="web_fetch",
                success=True,
                output=header + text,
                metadata={"url": final_url, "chars": len(text)},
            )

        except asyncio.TimeoutError:
            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type="web_fetch",
                success=False,
                error="Request timeout after 30 seconds",
            )
        except aiohttp.ClientError as e:
            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type="web_fetch",
                success=False,
                error=f"Network error: {e}",
            )
        except Exception as e:
            logger.error(f"web_fetch error for {url}: {e}", exc_info=True)
            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type="web_fetch",
                success=False,
                error=f"Fetch error: {e}",
            )

    async def _execute_web_search(self, tool_data: Dict[str, Any]) -> ToolExecutionResult:
        """Search the web using DuckDuckGo's HTML endpoint and return results.

        No API key required. Parses the top N results (title, URL, snippet)
        from the DuckDuckGo HTML search page.

        Args:
            tool_data: Dict with 'query' (required) and 'max_results' (optional).

        Returns:
            ToolExecutionResult with formatted search results.
        """
        import aiohttp

        tool_id = tool_data.get("id", "unknown")
        query = tool_data.get("query", "").strip()
        max_results = int(tool_data.get("max_results", 5))

        if not query:
            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type="web_search",
                success=False,
                error="No search query provided",
            )

        search_url = "https://html.duckduckgo.com/html/"
        params = {"q": query}
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (compatible; KollabAgent/1.0; "
                "+https://github.com/kollaborai/kollab)"
            ),
        }

        try:
            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    search_url, data=params, headers=headers, allow_redirects=True
                ) as resp:
                    if resp.status >= 400:
                        return ToolExecutionResult(
                            tool_id=tool_id,
                            tool_type="web_search",
                            success=False,
                            error=f"Search failed: HTTP {resp.status}",
                        )
                    html = await resp.text(errors="replace")

            results = self._parse_ddg_results(html, max_results)

            if not results:
                return ToolExecutionResult(
                    tool_id=tool_id,
                    tool_type="web_search",
                    success=True,
                    output=f"No results found for: {query}",
                    metadata={"query": query, "count": 0},
                )

            lines = [f"Search results for: {query}", ""]
            for i, r in enumerate(results, 1):
                lines.append(f"{i}. {r['title']}")
                lines.append(f"   {r['url']}")
                if r.get("snippet"):
                    lines.append(f"   {r['snippet']}")
                lines.append("")

            lines.append("Use web-fetch to get full content from any result.")

            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type="web_search",
                success=True,
                output="\n".join(lines).strip(),
                metadata={"query": query, "count": len(results)},
            )

        except asyncio.TimeoutError:
            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type="web_search",
                success=False,
                error="Search timed out after 30 seconds",
            )
        except aiohttp.ClientError as e:
            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type="web_search",
                success=False,
                error=f"Network error: {e}",
            )
        except Exception as e:
            logger.error(f"web_search error for '{query}': {e}", exc_info=True)
            return ToolExecutionResult(
                tool_id=tool_id,
                tool_type="web_search",
                success=False,
                error=f"Search error: {e}",
            )

    # ------------------------------------------------------------------
    # HTML / search-result helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _html_to_text(html: str) -> str:
        """Backward-compatible alias for _extract_main_content."""
        return ToolExecutor._extract_main_content(html)

    @staticmethod
    def _extract_main_content(html: str, extract_main: bool = True) -> str:
        """Extract main content from HTML using a priority chain.

        Priority order:
        1. JSON-LD structured data (articleBody / description)
        2. Semantic HTML5 (<article>, <main>, <section>)
        3. Readability heuristic (highest text-to-tag ratio element)
        4. Fallback: strip non-content tags, extract remaining text

        Uses beautifulsoup4 with lxml parser. Falls back to regex-based
        extraction if bs4 is not available.
        """
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "lxml")
        except ImportError:
            # bs4 not available — fall back to regex-based extraction
            return ToolExecutor._html_to_text_regex(html)

        # Remove non-content elements entirely
        for tag in soup.find_all(["script", "style", "nav", "footer", "aside",
                                   "header", "noscript", "svg", "form",
                                   "button", "iframe"]):
            tag.decompose()

        if extract_main:
            # Priority 1: JSON-LD structured data
            text = ToolExecutor._extract_json_ld(soup)
            if text and len(text) > 200:
                return text

            # Priority 2: Semantic HTML5 — <article>, <main>
            for selector in ["article", "main", "[role='main']"]:
                element = soup.select_one(selector)
                if element:
                    text = ToolExecutor._soup_to_text(element)
                    if len(text) > 200:
                        return text

            # Priority 3: Readability heuristic — find densest content block
            text = ToolExecutor._readability_extract(soup)
            if text and len(text) > 200:
                return text

        # Priority 4: Fallback — extract all remaining text
        return ToolExecutor._soup_to_text(soup)

    @staticmethod
    def _extract_json_ld(soup) -> str:
        """Extract articleBody or description from JSON-LD script tags."""
        import json

        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                # Handle both single objects and arrays
                items = data if isinstance(data, list) else [data]
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    # Look for articleBody first (richest content)
                    body = item.get("articleBody") or item.get("text", "")
                    if body and len(body) > 200:
                        # Collapse whitespace
                        lines = [l.strip() for l in body.split("\n") if l.strip()]
                        return "\n".join(lines)
                    # Fall back to description
                    desc = item.get("description", "")
                    if desc and len(desc) > 100:
                        return desc.strip()
            except (json.JSONDecodeError, TypeError):
                continue
        return ""

    @staticmethod
    def _readability_extract(soup) -> str:
        """Find the element with the highest text-to-tag ratio.

        Scores each <div> and <section> by text length / number of child
        tags. Returns the text from the highest-scoring element.
        """
        best_text = ""
        best_score = 0

        for element in soup.find_all(["div", "section"]):
            # Skip tiny elements
            text = element.get_text(separator=" ", strip=True)
            if len(text) < 200:
                continue

            # Score: text length weighted by text-to-tag ratio
            tag_count = len(element.find_all())
            if tag_count == 0:
                continue
            ratio = len(text) / tag_count
            # Prefer elements with more text, but penalize high tag density
            score = len(text) * min(ratio / 10, 1.0)

            if score > best_score:
                best_score = score
                best_text = text

        if best_text:
            lines = [l.strip() for l in best_text.split("\n") if l.strip()]
            return "\n".join(lines)
        return ""

    @staticmethod
    def _soup_to_text(element) -> str:
        """Extract clean text from a BeautifulSoup element.

        Inserts newlines for block-level tags, decodes entities,
        collapses whitespace.
        """
        # Insert newlines for block-level elements
        for tag in element.find_all(["p", "div", "br", "h1", "h2", "h3",
                                      "h4", "h5", "h6", "li", "tr", "blockquote",
                                      "pre"]):
            tag.append("\n")

        text = element.get_text()
        lines = [line.strip() for line in text.split("\n")]
        lines = [line for line in lines if line]
        return "\n".join(lines)

    @staticmethod
    def _html_to_text_regex(html: str) -> str:
        """Regex-based HTML to text fallback (no bs4 dependency).

        Used when beautifulsoup4 is not installed.
        """
        import html as html_mod
        import re

        # Remove non-content tags entirely (tag + inner content)
        non_content = re.compile(
            r"<(script|style|nav|footer|aside|header|noscript|svg|form|iframe)\b[^>]*>.*?</\1>",
            re.DOTALL | re.IGNORECASE,
        )
        html = non_content.sub("", html)

        # Remove HTML comments
        html = re.sub(r"<!--.*?-->", "", html, flags=re.DOTALL)

        # Insert newlines for block-level tags before stripping
        block_tags = re.compile(
            r"</?(p|div|br|h[1-6]|li|tr|td|th|section|article|blockquote|pre)\b[^>]*>",
            re.IGNORECASE,
        )
        html = block_tags.sub("\n", html)

        # Strip all remaining tags
        html = re.sub(r"<[^>]+>", "", html)

        # Decode HTML entities
        html = html_mod.unescape(html)

        # Collapse whitespace
        lines = [line.strip() for line in html.split("\n")]
        lines = [line for line in lines if line]

        return "\n".join(lines)

    async def _playwright_fetch(self, url: str) -> str:
        """Fetch a URL using Playwright headless browser (optional).

        Used as a fallback for JS-rendered pages where aiohttp returns
        sparse content. Returns extracted text or empty string if
        Playwright is not available or fails.
        """
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            return ""

        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page()
                await page.goto(url, timeout=20000, wait_until="networkidle")
                html = await page.content()
                await browser.close()

            return self._extract_main_content(html)
        except Exception as e:
            logger.debug(f"Playwright fetch failed for {url}: {e}")
            return ""

    @staticmethod
    def _parse_ddg_results(html: str, max_results: int) -> list:
        """Parse DuckDuckGo HTML search results into structured data.

        Extracts result links and snippets from the DDG HTML endpoint.
        Returns a list of dicts: {title, url, snippet}.
        """
        import re

        results = []

        # DDG HTML results have result blocks in <div class="result ...">
        # Each contains an <a class="result__a" href="...">title</a>
        # and an <a class="result__snippet">snippet</a>

        # Extract result blocks
        result_blocks = re.findall(
            r'<a[^>]+class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
            html,
            re.DOTALL,
        )

        # Extract snippets
        snippets = re.findall(
            r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
            html,
            re.DOTALL,
        )

        # Also try the newer DDG layout with result__url
        if not result_blocks:
            result_blocks = re.findall(
                r'<a[^>]+rel="nofollow"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
                html,
                re.DOTALL,
            )

        for i, (raw_url, raw_title) in enumerate(result_blocks[:max_results]):
            # DDG wraps URLs in a redirect; extract actual URL
            # Format: //duckduckgo.com/l/?uddg=ENCODED_URL&...
            if "uddg=" in raw_url:
                from urllib.parse import parse_qs, urlparse

                parsed = urlparse(raw_url)
                qs = parse_qs(parsed.query)
                actual_url = qs.get("uddg", [raw_url])[0]
            elif raw_url.startswith("//"):
                actual_url = "https:" + raw_url
            else:
                actual_url = raw_url

            # Strip HTML from title
            title = re.sub(r"<[^>]+>", "", raw_title).strip()
            if not title:
                title = actual_url

            snippet = ""
            if i < len(snippets):
                snippet = re.sub(r"<[^>]+>", "", snippets[i]).strip()

            results.append({"title": title, "url": actual_url, "snippet": snippet})

        return results

    def _get_display_name(self, tool_data: Dict[str, Any]) -> str:
        """Get a human-readable display name for the tool.

        Args:
            tool_data: Tool data dictionary

        Returns:
            Display name for the tool
        """
        tool_type = tool_data.get("type", "tool")

        if tool_type == "terminal":
            command = tool_data.get("command", "")
            # Collapse multi-line commands (heredocs, chained commands) to first line
            if "\n" in command:
                first_line = command.split("\n")[0]
                return f"terminal: {first_line}..."
            return f"terminal: {command}"

        elif tool_type == "mcp_tool":
            name = tool_data.get("name", "mcp_tool")
            # Clean up malformed tool names
            if "<" in name or ">" in name:
                import re

                match = re.search(r"<([^<]+)", name)
                if match:
                    name = match.group(1).strip()
                else:
                    words = re.findall(r"\b([a-z_]+)\b", name.lower())
                    name = words[-1] if words else "mcp_tool"
            return str(name)

        elif tool_type.startswith("file_"):
            # For file operations, show the operation type
            return str(tool_type)

        elif tool_type in ("web_fetch", "web-fetch"):
            url = tool_data.get("url", "")
            return f"web-fetch: {url[:60]}" if url else "web-fetch"

        elif tool_type in ("web_search", "web-search"):
            query = tool_data.get("query", "")
            return f"web-search: {query[:60]}" if query else "web-search"

        elif tool_type in ("mcp_reload", "mcp-reload"):
            return "mcp-reload"

        return str(tool_type)
