"""Kollabor Agent - Tool execution, MCP integration, file operations, and shell commands.

This package provides the agent execution toolkit for running tools,
managing MCP server connections, executing file operations, and
running shell commands.
"""

from .agent_manager import Agent, AgentManager, Skill, SkillLibrary, validate_skill_name
from .background_task_manager import BackgroundTaskManager
from .file_operations_executor import FileOperationsExecutor
from .mcp_integration import MCPIntegration, MCPServerConnection
from .mcp_manager import MCPManager
from .native_tools_handler import NativeToolsHandler
from .process_manager import (
    CircuitBreaker,
    CircuitBreakerConfig,
    ManagedProcess,
    ProcessManager,
    ProcessState,
    ResourceSnapshot,
    SpawnRequest,
    SpawnResult,
    SpawnStrategy,
    SubprocessStrategy,
)
from .queue_processor import QueueProcessor
from .runtime import AgentLifecycle, AgentRuntime, LaunchStrategy
from .shell_command_service import ShellCommandService
from .shell_executor import ShellExecutor, ShellResult
from .shell_utils import (
    clear_alias_cache,
    detect_shell_aliases,
    format_aliases_for_prompt,
    get_cached_aliases,
    get_syntax_changing_aliases,
)
from .tool_executor import ToolExecutionResult, ToolExecutor

__all__ = [
    "ToolExecutor",
    "ToolExecutionResult",
    "FileOperationsExecutor",
    "MCPIntegration",
    "MCPServerConnection",
    "MCPManager",
    "ShellCommandService",
    "ShellExecutor",
    "ShellResult",
    "detect_shell_aliases",
    "get_syntax_changing_aliases",
    "format_aliases_for_prompt",
    "get_cached_aliases",
    "clear_alias_cache",
    "AgentManager",
    "Agent",
    "Skill",
    "SkillLibrary",
    "validate_skill_name",
    "NativeToolsHandler",
    "QueueProcessor",
    "BackgroundTaskManager",
    "AgentRuntime",
    "AgentLifecycle",
    "LaunchStrategy",
    "ProcessManager",
    "ManagedProcess",
    "ProcessState",
    "SpawnRequest",
    "SpawnResult",
    "SpawnStrategy",
    "SubprocessStrategy",
    "CircuitBreaker",
    "CircuitBreakerConfig",
    "ResourceSnapshot",
]
