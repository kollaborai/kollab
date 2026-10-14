"""Permission status view for inline confirmation dialogs."""

import logging
from typing import TYPE_CHECKING, Any, Callable, Dict, List

from kollabor_tui.design_system import S, T
from kollabor_tui.terminal_state import get_global_width

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class PermissionStatusView:
    """
    Status view for permission confirmation.

    Renders in the status/activity area where "Thinking..." and
    "Executing..." normally appear. Uses Box() style rendering.

    The confirmation_response_enum parameter receives the ConfirmationResponse
    enum class from kollabor.llm.permissions.models at construction time,
    avoiding a circular import (kollabor_tui -> kollabor).
    """

    def __init__(
        self,
        confirmation_details: Dict[str, Any],
        on_response: Callable,
        confirmation_response_enum: Any = None,
    ):
        """Initialize permission status view.

        Args:
            confirmation_details: Details about the tool requiring permission
            on_response: Callback when user responds
            confirmation_response_enum: The ConfirmationResponse enum class
        """
        self._details = confirmation_details
        self._on_response = on_response
        self._response_enum = confirmation_response_enum
        self._options = self._build_options()

    def _build_options(self) -> List[tuple]:
        """Build response options while preserving opaque ACP IDs."""
        R = self._response_enum
        if R is None:
            return []

        acp_options = self._details.get("acp_options")
        if isinstance(acp_options, list) and acp_options:
            parsed = [
                option
                for option in acp_options
                if isinstance(option, dict) and option.get("optionId")
            ]
            source_acp = str(self._details.get("source") or "").lower() == "acp"
            if not source_acp:
                return [
                    (
                        str(index + 1),
                        str(
                            option.get("name")
                            or option.get("label")
                            or option.get("kind")
                            or option["optionId"]
                        ),
                        str(option["optionId"]),
                    )
                    for index, option in enumerate(parsed)
                ]

            once = [option for option in parsed if option.get("kind") == "allow_once"]
            options: List[tuple] = []
            once_keys = ["a"] if len(once) == 1 else [
                str(index + 1) for index in range(min(len(once), 9))
            ]
            once_index = 0
            has_deny = False
            for option in parsed:
                kind = str(option.get("kind") or "")
                option_id = str(option["optionId"])
                label = str(option.get("name") or option.get("label") or kind or option_id)
                if kind == "allow_once":
                    key = once_keys[once_index] if once_index < len(once_keys) else option_id
                    once_index += 1
                elif kind == "reject_once":
                    key = "d"
                    has_deny = True
                elif kind == "reject_always":
                    key = "R"
                elif kind == "allow_always":
                    key = "v"
                else:
                    key = option_id
                options.append((key, label, option_id))

            operation = self._details.get("operation")
            operation_kind = operation.get("kind") if isinstance(operation, dict) else None
            structured_operation = isinstance(operation, dict) and bool(operation_kind)
            if len(once) == 1 and structured_operation:
                options.extend([
                    ("s", "session", R.APPROVE_SESSION),
                    ("p", "project", R.APPROVE_PROJECT),
                ])
                if operation_kind == "edit":
                    options.append(("A", "always edits", R.APPROVE_ALWAYS))
            if not has_deny and not any(key == "d" for key, _, _ in options):
                # Keep a distinct native denial control when the adapter offers
                # no reject_once option; this must not collide with an opaque ID.
                options.append(("d", "deny", R.CANCEL))
            return options

        tool_type = self._details.get("tool_type", "unknown")
        project_label = "all reads" if tool_type == "file_read" else (
            "this cmd" if tool_type == "terminal" else "project"
        )
        options = [
            ("a", "approve", R.APPROVE_ONCE),
            ("s", "session", R.APPROVE_SESSION),
            ("p", project_label, R.APPROVE_PROJECT),
        ]
        if tool_type in ("file_write", "file_edit"):
            options.append(("A", "always edits", R.APPROVE_ALWAYS))
        elif tool_type in ("mcp_tool", "mcp"):
            options.append(("t", "trust tool", R.APPROVE_TOOL_ALWAYS))
        options.append(("d", "deny", R.DENY))
        if tool_type == "terminal":
            options.append(("c", "cancel", R.CANCEL))
        return options

    def render(self) -> List[str]:
        """
        Render the permission prompt for the status area.

        Returns:
            List of formatted lines to display
        """
        from kollabor_tui.design_system import TagBox

        # Reserve space for tag column (3) + content leading space (1)
        width = get_global_width() - 4

        # Build content lines
        content_lines = []

        # Header line: "PERMISSION REQUIRED" + risk level
        risk_level = self._details.get("risk_level", "UNKNOWN")
        header = f"{S.BOLD}PERMISSION REQUIRED{S.RESET_BOLD}"
        risk_text = f"{S.BOLD}{risk_level}{S.RESET_BOLD}"
        # Calculate padding for header (within content_width)
        visible_header = "PERMISSION REQUIRED"
        padding = width - len(visible_header) - len(risk_level) - 2
        header_line = f"{header}{' ' * max(1, padding)}{risk_text}"
        content_lines.append(header_line)

        # Tool description line
        tool_line = self._get_tool_description()
        content_lines.append(tool_line)
        acp_options = self._details.get("acp_options") or ()
        vendor_offered = any(
            isinstance(option, dict)
            and str(option.get("kind")) in {"allow_always", "reject_always"}
            for option in acp_options
        )
        if self._details.get("vendor_saved") or self._details.get("warning") or vendor_offered:
            content_lines.append(
                str(self._details.get("warning") or "Saved by the ACP provider; Kollab cannot undo it.")
            )

        # Options line with bold keys (no brackets)
        options_parts = []
        for key, label, _ in self._options:
            options_parts.append(f"{S.BOLD}{key}{S.RESET_BOLD} {label}")
        options_text = "   ".join(options_parts)
        content_lines.append(options_text)

        # Render using TagBox with warning colors for visibility
        rendered = TagBox.render(
            lines=content_lines,
            tag_bg=T().warning[0],  # Warning color for tag
            tag_fg=None,
            tag_width=3,
            content_colors=T().warning,  # Warning gradient for content
            content_fg=T().text_dark,
            content_width=width,
            tag_chars=[" ! ", "   ", "   "],  # ! for header only
        )

        return rendered.split("\n")

    def _get_tool_description(self) -> str:
        """Get single-line tool description."""
        tool_type = self._details.get("tool_type", "unknown")
        width = get_global_width()

        operation = self._details.get("operation")
        if tool_type == "acp" or isinstance(operation, dict):
            kind = str(operation.get("kind") if isinstance(operation, dict) else tool_type)
            value = ""
            if isinstance(operation, dict):
                command = operation.get("command")
                paths = operation.get("paths") or operation.get("path")
                if isinstance(command, list):
                    value = " ".join(str(part) for part in command)
                elif command:
                    value = str(command)
                elif isinstance(paths, list):
                    value = ", ".join(str(path) for path in paths)
                elif paths:
                    value = str(paths)
                else:
                    value = str(operation.get("value") or "")
            title = str(self._details.get("title") or self._details.get("tool_name") or "Tool")
            description = f"{title} [{kind}]"
            if value:
                description += f"({value})"
            max_len = width - 20
            return description if len(description) <= max_len else description[: max_len - 3] + "..."

        if tool_type == "terminal":
            command = self._details.get("command", "")
            if not command:
                command = "(no command)"
            # Truncate long commands (account for "terminal()" wrapper)
            max_len = width - 20
            if len(command) > max_len:
                command = command[: max_len - 3] + "..."
            return f"terminal({command})"

        elif tool_type == "file_write":
            file_path = self._details.get("file_path", "")
            if not file_path:
                file_path = "(no path)"
            max_len = width - 20
            if len(file_path) > max_len:
                file_path = "..." + file_path[-(max_len - 3) :]
            return f"Write({file_path})"

        elif tool_type == "file_edit":
            file_path = self._details.get("file_path", "")
            if not file_path:
                file_path = "(no path)"
            max_len = width - 20
            if len(file_path) > max_len:
                file_path = "..." + file_path[-(max_len - 3) :]
            return f"Edit({file_path})"

        elif tool_type == "file_read":
            file_path = self._details.get("file_path", "")
            if not file_path:
                file_path = "(no path)"
            max_len = width - 20
            if len(file_path) > max_len:
                file_path = "..." + file_path[-(max_len - 3) :]
            return f"Read({file_path})"

        elif tool_type in ("mcp_tool", "mcp"):
            server = self._details.get("server_name", "")
            tool = self._details.get("mcp_tool_name", "")
            if not server or not tool:
                return f"MCP({tool or server or 'unknown'})"
            return f"MCP({server}/{tool})"

        # Fallback for unknown tool types - use tool_type as last resort
        tool_name = self._details.get("tool_name") or self._details.get("name")
        if tool_name:
            return f"Tool({tool_name})"

        # No explicit name, derive from tool_type and available details
        tool_type = self._details.get("tool_type", "unknown")
        if tool_type == "terminal":
            cmd = self._details.get("command", "")
            if len(cmd) > 30:
                cmd = cmd[:27] + "..."
            return f"terminal({cmd})" if cmd else "terminal(unknown)"
        elif tool_type.startswith("file_"):
            path = self._details.get("file_path", "")
            if len(path) > 30:
                path = "..." + path[-27:]
            return f"{tool_type}({path})" if path else f"{tool_type}(unknown)"
        elif tool_type in ("mcp_tool", "mcp"):
            server = self._details.get("server_name", "")
            tool = self._details.get("mcp_tool_name", "")
            return f"MCP({server}/{tool})" if server and tool else "MCP(unknown)"

        return f"Tool({tool_type})"

    def _get_risk_color(self, risk_level: str) -> tuple:
        """Get color tuple for risk level."""
        colors = {
            "HIGH": T().error,
            "MEDIUM": T().warning,
            "LOW": T().success,
            "UNKNOWN": T().text_dim,
        }
        return colors.get(risk_level, T().text)  # type: ignore[no-any-return]

    def handle_keypress(self, key_char: str) -> bool:
        """
        Handle single keypress for option selection.

        Args:
            key_char: The character pressed

        Returns:
            True if keypress was handled, False otherwise
        """
        for key, _, response in self._options:
            if key_char == key:
                self._on_response(response)
                return True

        # Escape always cancels
        if key_char == "\x1b":  # ESC
            R = self._response_enum
            if R is not None:
                self._on_response(R.CANCEL)
            return True

        return False

    @property
    def options(self) -> List[tuple]:
        """Get available options."""
        return self._options
