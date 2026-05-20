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
        result = await self.shell_executor.run(
            command,
            timeout=self.terminal_timeout,
            cwd=cwd,
        )

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

        return str(tool_type)
