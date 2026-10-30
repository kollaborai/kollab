"""Data models and enums for the permission system."""

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Pattern


class ApprovalMode(Enum):
    """Approval mode for tool execution."""

    # Default: require confirmation for HIGH risk tools
    DEFAULT = auto()

    # Confirm all: require confirmation for all tool executions
    CONFIRM_ALL = auto()

    # Auto-approve: automatically approve file edit/write operations
    # Shell commands still require confirmation
    AUTO_APPROVE_EDITS = auto()

    # Trust all: automatically approve ALL tool executions (dangerous)
    TRUST_ALL = auto()


class ToolRiskLevel(Enum):
    """Risk classification for tool operations."""

    # Always requires confirmation - destructive operations
    HIGH = auto()

    # Requires confirmation in strict mode - modifying operations
    MEDIUM = auto()

    # Never requires confirmation - read-only operations
    LOW = auto()

    # Unknown tool - treat as HIGH until assessed
    UNKNOWN = auto()


class ConfirmationType(Enum):
    """Type of confirmation dialog to show."""

    # Shell command execution
    SHELL_COMMAND = auto()

    # File write (new file)
    FILE_WRITE = auto()

    # File edit (existing file)
    FILE_EDIT = auto()

    # MCP tool execution
    MCP_TOOL = auto()

    # Generic confirmation
    GENERIC = auto()


class ConfirmationResponse(Enum):
    """User response to confirmation dialog."""

    # Approve this execution once
    APPROVE_ONCE = auto()

    # Approve and remember for this session
    APPROVE_SESSION = auto()

    # Approve and remember for this project (persistent)
    APPROVE_PROJECT = auto()

    # Approve all similar operations (e.g., all file edits)
    APPROVE_ALWAYS = auto()

    # Approve this specific tool/server always (MCP)
    APPROVE_TOOL_ALWAYS = auto()

    # Deny this execution
    DENY = auto()

    # Cancel and abort the entire operation
    CANCEL = auto()


@dataclass
class RiskAssessmentRules:
    """Rules for assessing tool risk levels."""

    # HIGH risk patterns (regex) - always block without confirmation
    high_risk_patterns: List[Pattern] = field(
        default_factory=lambda: [
            re.compile(r"rm\s+(-[rf]+\s+)*[/~]"),  # rm -rf /
            re.compile(r"rm\s+.*--no-preserve-root"),  # rm --no-preserve-root
            re.compile(r"mkfs\."),  # mkfs operations
            re.compile(r"dd\s+.*of=/dev"),  # dd to device
            re.compile(r"chmod\s+(-R\s+)?777"),  # chmod 777
            re.compile(r">\s*/dev/sd[a-z]"),  # write to disk
            re.compile(r"curl.*\|\s*(ba)?sh"),  # curl | bash
            re.compile(r"wget.*\|\s*(ba)?sh"),  # wget | bash
            re.compile(r"sudo\s+rm"),  # sudo rm
            re.compile(r":\(\)\s*\{"),  # fork bomb
            # network kill switch: the CLI (quoted verb, module form), the slash commands, and its files
            re.compile(r"""--hub\s+['"]?(halt|resume)\b"""),
            re.compile(r"/connect\s+(revoke|halt|resume)\b"),
            re.compile(
                r"(\brm|\bmv|\bcp|\btee|\bln|\bsed\s+-i|\bdd\b[^|;&]*of=|\btruncate|\bchmod|\bchown"
                r"|\bshred|\bunlink|>)[^|;&]*\.kollab[\\/]private\b"
            ),
            re.compile(r"\bcd\s+\S*\.kollab[\\/]private\b.*(\brm|\bmv|\bcp|\btee|\btruncate|\bchmod|\bunlink|>)"),
            re.compile(r"\bfind\b[^|;&]*\.kollab[\\/]private\b[^|;&]*(-delete|-exec\s+rm)"),
            re.compile(r"\bpython[\d.]*\s+-c\b.*\.kollab[\\/]private\b"),
            # Windows shells (#438): PowerShell and cmd syntax; cmdlets match in any case
            re.compile(
                r"(?i)\b(irm|iwr|invoke-restmethod|invoke-webrequest|curl(\.exe)?|wget)\b"
                r"[^|;&]*\|\s*(iex|invoke-expression)\b"
            ),
            re.compile(
                r"(?i)\b(remove-item|ri|rd|rmdir|del|erase)\b[^|;&]*?(?<![\w\\/])['\"]?"
                r"[a-z]:[\\/]?\*?['\"]?(?=\s|$)"
            ),
            re.compile(r"(?i)\b(rd|rmdir)\s+(/[sq]\s+){2}"),
            re.compile(r"(?i)\bformat\s+[a-z]:"),
            re.compile(r"(?i)\b(stop-computer|restart-computer|format-volume|clear-disk)\b"),
            re.compile(
                r"(?i)\b(remove-item|ri|del|erase|rd|rmdir|move-item|mi"
                r"|copy-item|cpi|set-content|sc"
                r"|add-content|ac|out-file|clear-content|copy|move)\b[^|;&]*\.kollab[\\/]private\b"
            ),
        ]
    )

    # MEDIUM risk patterns - modifying operations
    medium_risk_patterns: List[Pattern] = field(
        default_factory=lambda: [
            re.compile(r"git\s+push"),  # git push
            re.compile(r"git\s+reset\s+--hard"),  # git reset --hard
            re.compile(r"npm\s+publish"),  # npm publish
            re.compile(r"pip\s+install"),  # pip install
            re.compile(r"docker\s+build"),  # docker build
            re.compile(r"kubectl\s+apply"),  # kubectl apply
        ]
    )

    # Tool types and their default risk levels
    tool_type_risks: Dict[str, ToolRiskLevel] = field(
        default_factory=lambda: {
            "terminal": ToolRiskLevel.MEDIUM,  # Shell commands
            "mcp_tool": ToolRiskLevel.MEDIUM,  # MCP tools (unknown behavior)
            "mcp": ToolRiskLevel.MEDIUM,  # MCP tools (alternate naming)
            "file_write": ToolRiskLevel.MEDIUM,  # Writing files
            "file_create": ToolRiskLevel.MEDIUM,  # Creating new files
            "file_create_overwrite": ToolRiskLevel.MEDIUM,  # Creating/overwriting files
            "file_edit": ToolRiskLevel.LOW,  # Editing existing files
            "file_read": ToolRiskLevel.LOW,  # Reading files
            "search": ToolRiskLevel.LOW,  # Search operations
        }
    )

    # Trusted tool names (always LOW risk)
    trusted_tools: List[str] = field(
        default_factory=lambda: [
            "read_file",
            "list_directory",
            "search_file_content",
            "glob",
        ]
    )

    # Blocked tools (always denied, no confirmation offered)
    blocked_tools: List[str] = field(default_factory=list)


@dataclass
class RiskAssessmentResult:
    """Result of risk assessment for a tool."""

    level: ToolRiskLevel
    reason: str
    matched_pattern: Optional[str] = None
    tool_type: Optional[str] = None
    requires_confirmation: bool = False
    is_blocked: bool = False


@dataclass
class PermissionDecision:
    """Result of a permission check."""

    allowed: bool
    reason: str
    risk_level: ToolRiskLevel
    requires_user_confirmation: bool = False
    confirmation_details: Optional[Dict[str, Any]] = None
    timestamp: datetime = field(default_factory=datetime.now)
    # Not run yet: the call waits for the user's own yes (kollabor_agent.yes_approval).
    needs_yes: bool = False


@dataclass
class ApprovalRecord:
    """Record of a user approval."""

    tool_type: str
    tool_name: str
    pattern: Optional[str]  # For "always approve" matching
    approved_at: datetime
    expires_at: Optional[datetime]  # None = session-scoped
    scope: str  # "once", "session", "always"


@dataclass
class ToolData:
    """Tool data extracted from LLM response."""

    id: str  # Unique identifier for this tool call
    type: str  # terminal, mcp_tool, file_write, file_edit, etc.
    name: str  # Tool name
    command: Optional[str] = None  # For terminal commands
    arguments: Dict[str, Any] = field(default_factory=dict)  # Tool arguments
    server_name: Optional[str] = None  # For MCP tools
    raw_response: Dict[str, Any] = field(default_factory=dict)  # Original response data


@dataclass
class PermissionAuditEntry:
    """Audit log entry for permission decisions."""

    timestamp: datetime
    tool_id: str
    tool_type: str
    tool_name: str
    command: Optional[str]
    risk_level: str
    decision: str  # approved, denied, blocked
    reason: str
    approval_mode: str
    user_response: Optional[str]  # If user was prompted
    session_id: str


@dataclass
class ShellCommandConfirmation:
    """Confirmation details for shell commands."""

    type: ConfirmationType = ConfirmationType.SHELL_COMMAND
    command: str = ""
    root_command: str = ""
    working_directory: str = ""
    risk_level: str = ""
    risk_reason: str = ""
    on_confirm: Optional[Any] = None  # Callable will be stored as weak ref


@dataclass
class FileOperationConfirmation:
    """Confirmation details for file operations."""

    type: ConfirmationType = ConfirmationType.FILE_WRITE
    file_path: str = ""
    file_name: str = ""
    operation: str = ""  # "write", "edit", "delete"
    content_preview: str = ""
    diff_preview: Optional[str] = None
    original_content: Optional[str] = None
    new_content: str = ""
    risk_level: str = ""
    on_confirm: Optional[Any] = None  # Callable will be stored as weak ref


@dataclass
class MCPToolConfirmation:
    """Confirmation details for MCP tools."""

    type: ConfirmationType = ConfirmationType.MCP_TOOL
    server_name: str = ""
    tool_name: str = ""
    tool_display_name: str = ""
    arguments: Dict[str, Any] = field(default_factory=dict)
    risk_level: str = ""
    on_confirm: Optional[Any] = None  # Callable will be stored as weak ref
