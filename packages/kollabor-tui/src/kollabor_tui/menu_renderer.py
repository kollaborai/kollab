"""Command menu renderer for interactive slash command display.

Uses the modern design system (TagBox, solid, solid_fg, T) for consistent styling.
"""

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

# Design system imports
from kollabor_tui.design_system import S, T, solid, solid_fg  # noqa: E402

# Regex to strip ANSI escape codes for logging
ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[[0-9;]*m")

logger = logging.getLogger(__name__)


ANSI_CSI_PATTERN = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

# Use design system style codes
BOLD = S.BOLD
DIM = S.DIM
RESET = S.RESET

# Menu symbols
ARROW_RIGHT = "▶"
ARROW_DOWN = "▼"
ARROW_UP = "▲"
DOT = "·"
GLOW = "◆"

# Category display order and icons (Unicode symbols)
CATEGORY_CONFIG = {
    "system": {"name": "SYS", "icon": "⚙", "full": "System"},
    "conversation": {"name": "CHAT", "icon": "⌘", "full": "Conversation"},
    "agent": {"name": "AGENT", "icon": "◈", "full": "Agent"},
    "development": {"name": "DEV", "icon": "⌥", "full": "Development"},
    "file": {"name": "FILE", "icon": "≡", "full": "Files"},
    "task": {"name": "TASK", "icon": "☰", "full": "Tasks"},
    "custom": {"name": "PLUG", "icon": "⊕", "full": "Plugins"},
}
CATEGORY_ORDER = [
    "system",
    "conversation",
    "agent",
    "development",
    "file",
    "task",
    "custom",
]


def _make_bg(color: Tuple[int, int, int]) -> str:
    """Make background color ANSI code from RGB tuple."""
    return f"\033[48;2;{color[0]};{color[1]};{color[2]}m"


def _make_fg(color: Tuple[int, int, int]) -> str:
    """Make foreground color ANSI code from RGB tuple."""
    return f"\033[38;2;{color[0]};{color[1]};{color[2]}m"


class CommandMenuRenderer:
    """Renders interactive command menu overlay.

    Provides a command menu that appears when the user
    types '/' and allows filtering and selection of available commands.
    """

    def __init__(self, terminal_renderer, max_visible_items: int = 5) -> None:
        """Initialize the command menu renderer.

        Args:
            terminal_renderer: Terminal renderer for display operations.
            max_visible_items: Maximum number of menu items to show at once.
        """
        self.renderer = terminal_renderer
        self.logger = logger
        self.menu_active = False
        self.current_commands: List[Dict[str, Any]] = []
        self.menu_items: List[Dict[str, Any]] = (
            []
        )  # Flattened list: commands + subcommands as selectable items
        self.selected_index = 0
        self.filter_text = ""
        self.current_menu_lines: List[str] = []  # Store menu content for event system
        # Ensure max_visible_items is always valid (defensive programming)
        self.max_visible_items = (
            max_visible_items if max_visible_items and max_visible_items > 0 else 5
        )
        self.scroll_offset = 0  # First visible item index

    def _get_menu_width(self) -> int:
        """Get menu width to match input box width.

        Uses same calculation as modern_input plugin:
        terminal_width - 4, clamped between min_width and max_width from config.

        Returns:
            Menu width in characters.
        """
        from kollabor_tui.terminal_state import get_terminal_width

        terminal_width = get_terminal_width()

        config = getattr(self.renderer, "_app_config", None)
        if config and hasattr(config, "get"):
            min_width = config.get("plugins.modern_input.min_width", 40)
            max_width = config.get("plugins.modern_input.max_width", 80)
        else:
            min_width = 40
            max_width = 80

        # Defensive: ensure width values are valid integers
        if min_width is None or not isinstance(min_width, int):
            min_width = 40
        if max_width is None or not isinstance(max_width, int):
            max_width = 80

        # Ensure mypy knows these are int after guards
        min_width = int(min_width)
        max_width = int(max_width)

        # Match input box: terminal_width - 4, with constraints
        proposed_width = terminal_width - 4
        return max(min_width, min(max_width, proposed_width))

    def show_command_menu(
        self, commands: List[Dict[str, Any]], filter_text: str = ""
    ) -> None:
        """Display command menu when user types '/'.

        Args:
            commands: List of available commands to display.
            filter_text: Current filter text (excluding the leading '/').
        """
        try:
            self.menu_active = True
            self.current_commands = (
                list(commands)
                if filter_text.strip()
                else self._sort_commands_by_category(commands)
            )
            self.filter_text = filter_text
            self.selected_index = 0
            self.scroll_offset = 0  # Reset scroll when menu opens

            # Build flattened menu items (commands + subcommands)
            self.menu_items = self._build_menu_items()

            # Render the menu
            self._render_menu()

            self.logger.info(
                f"Command menu shown with {len(commands)} commands, {len(self.menu_items)} items"
            )

        except Exception as e:
            self.logger.error(f"Error showing command menu: {e}")

    def set_selected_index(self, index: int) -> None:
        """Set the selected menu item index for navigation.

        Args:
            index: Index of the item to select (in menu_items list).
        """
        if 0 <= index < len(self.menu_items):
            self.selected_index = index
            self._ensure_selection_visible()
            # Note: No auto-render here - caller will trigger render to avoid duplicates
            logger.debug(f"Selected menu item index set to: {index}")

    def hide_menu(self) -> None:
        """Hide command menu and return to normal input."""
        try:
            if self.menu_active:
                self.menu_active = False
                self.current_commands = []
                self.selected_index = 0
                self.filter_text = ""
                self.scroll_offset = 0

                # Clear menu from display
                self._clear_menu()

                self.logger.info("Command menu hidden")

        except Exception as e:
            self.logger.error(f"Error hiding command menu: {e}")

    def filter_commands(
        self,
        commands: List[Dict[str, Any]],
        filter_text: str,
        reset_selection: bool = True,
    ) -> None:
        """Filter visible commands as user types.

        Args:
            commands: Filtered list of commands to display.
            filter_text: Current filter text.
            reset_selection: Whether to reset selection to top (True for typing, False for navigation).
        """
        try:
            if not self.menu_active:
                return

            self.current_commands = (
                list(commands)
                if filter_text.strip()
                else self._sort_commands_by_category(commands)
            )
            self.filter_text = filter_text

            # Build flattened menu items (commands + subcommands)
            self.menu_items = self._build_menu_items()

            # Only reset selection when filtering by typing, not during navigation
            if reset_selection:
                self.selected_index = 0  # Reset selection to top
                self.scroll_offset = 0  # Reset scroll when filtering
            else:
                # Ensure selected index is still valid after filtering
                if self.selected_index >= len(self.menu_items):
                    self.selected_index = max(0, len(self.menu_items) - 1)
                # Adjust scroll if needed
                self._ensure_selection_visible()

            # Re-render with filtered commands
            self._render_menu()

            self.logger.debug(
                f"Filtered to {len(commands)} commands with '{filter_text}', reset_selection={reset_selection}"
            )

        except Exception as e:
            self.logger.error(f"Error filtering commands: {e}")

    def navigate_selection(self, direction: str) -> bool:
        """Handle arrow key navigation in menu.

        Args:
            direction: Direction to navigate ("up" or "down").

        Returns:
            True if navigation was handled, False otherwise.
        """
        try:
            if not self.menu_active or not self.menu_items:
                return False

            if direction == "up":
                self.selected_index = max(0, self.selected_index - 1)
            elif direction == "down":
                self.selected_index = min(
                    len(self.menu_items) - 1, self.selected_index + 1
                )
            else:
                return False

            # Adjust scroll to keep selection visible
            self._ensure_selection_visible()

            # Re-render with new selection
            self._render_menu()
            return True

        except Exception as e:
            self.logger.error(f"Error navigating menu: {e}")
            return False

    def _ensure_selection_visible(self) -> None:
        """Adjust scroll offset to keep selected item visible."""
        if not self.menu_items:
            return

        # Defensive check: ensure max_visible_items is valid
        if self.max_visible_items is None or self.max_visible_items <= 0:
            self.max_visible_items = 5
            self.logger.warning(
                "max_visible_items was invalid in _ensure_selection_visible, reset to 5"
            )

        # Defensive check: ensure scroll_offset is valid
        if self.scroll_offset is None:
            self.scroll_offset = 0
            self.logger.warning(
                "scroll_offset was None in _ensure_selection_visible, reset to 0"
            )

        # Defensive check: ensure selected_index is valid
        if self.selected_index is None:
            self.selected_index = 0
            self.logger.warning(
                "selected_index was None in _ensure_selection_visible, reset to 0"
            )

        # If selection is above visible area, scroll up
        if self.selected_index < self.scroll_offset:
            self.scroll_offset = self.selected_index

        # If selection is below visible area, scroll down
        elif self.selected_index >= self.scroll_offset + self.max_visible_items:
            self.scroll_offset = self.selected_index - self.max_visible_items + 1

        # Clamp scroll offset to valid range
        max_scroll = max(0, len(self.menu_items) - self.max_visible_items)
        self.scroll_offset = max(0, min(self.scroll_offset, max_scroll))

    def get_selected_command(self) -> Optional[Dict[str, Any]]:
        """Get currently selected menu item (command or subcommand).

        Returns:
            Selected item dictionary with keys:
            - For commands: name, description, aliases, category, etc.
            - For subcommands: is_subcommand=True, parent_name, subcommand_name, subcommand_args
            Returns None if no selection.
        """
        if (
            self.menu_active
            and self.menu_items
            and self.selected_index is not None
            and 0 <= self.selected_index < len(self.menu_items)
        ):
            return self.menu_items[self.selected_index]
        return None

    def _build_menu_items(self) -> List[Dict[str, Any]]:
        """Build flattened list of menu items including subcommands.

        Creates a flat list where each command is followed by its subcommands.
        Subcommands only appear when filtered to a single command (not in main menu).

        Returns:
            List of menu item dicts, each with is_subcommand flag.
        """
        items = []
        # Only show subcommands when filtered to a single command
        show_subcommands = len(self.current_commands) == 1

        for i, cmd in enumerate(self.current_commands):
            # Add the command itself
            cmd_item = cmd.copy()
            cmd_item["is_subcommand"] = False
            cmd_item["_cmd_index"] = i
            items.append(cmd_item)

            # Add subcommands only when this is the only command showing
            if show_subcommands:
                subcommands = cmd.get("subcommands", [])
                if subcommands:
                    for sub in subcommands:
                        sub_item = {
                            "is_subcommand": True,
                            "parent_name": cmd["name"],
                            "parent_category": cmd.get("category", "custom"),
                            "subcommand_name": sub.get("name", ""),
                            "subcommand_args": sub.get("args", ""),
                            "subcommand_desc": sub.get("description", ""),
                            "_cmd_index": i,
                        }
                        items.append(sub_item)

        return items

    def _render_menu(self) -> None:
        """Render the command menu overlay."""
        try:
            if not self.menu_active:
                return

            # Create menu content
            menu_lines = self._create_menu_lines()

            # Display menu overlay
            self._display_menu_overlay(menu_lines)

        except Exception as e:
            import traceback

            self.logger.error(f"Error rendering menu: {e}\n{traceback.format_exc()}")

    def _create_menu_lines(self) -> List[str]:
        """Create lines for menu display with category grouping and scroll support.

        Returns:
            List of formatted menu lines (limited to max_visible_items).
        """
        lines = []

        # If no items, show empty state
        if not self.menu_items:
            lines.append(self._make_empty_state())
            return lines

        # Defensive check: ensure max_visible_items is valid
        if self.max_visible_items is None or self.max_visible_items <= 0:
            self.max_visible_items = 5
            self.logger.warning("max_visible_items was invalid, reset to 5")

        # Defensive check: ensure scroll_offset is valid
        if self.scroll_offset is None:
            self.scroll_offset = 0
            self.logger.warning("scroll_offset was None, reset to 0")

        total_items = len(self.menu_items)
        has_more_above = self.scroll_offset > 0
        has_more_below = self.scroll_offset + self.max_visible_items < total_items

        # Scroll up indicator
        if has_more_above:
            lines.append(self._make_scroll_indicator("up", self.scroll_offset))

        # Get visible items slice
        visible_start = self.scroll_offset
        visible_end = min(self.scroll_offset + self.max_visible_items, total_items)

        # Track current category for headers
        current_category = None

        # Render visible items (commands and subcommands)
        for i in range(visible_start, visible_end):
            item = self.menu_items[i]
            is_selected = i == self.selected_index

            if item.get("is_subcommand"):
                # Render subcommand item
                line = self._format_subcommand_item(item, is_selected)
                lines.append(line)
            else:
                # Render command item
                cmd_category = item.get("category", "custom")

                # Convert CommandCategory enum to string if needed
                if hasattr(cmd_category, "value"):
                    cmd_category = cmd_category.value

                # Insert category header when category changes
                if cmd_category != current_category:
                    current_category = cmd_category
                    header = self._format_category_header(cmd_category)
                    lines.append(header)

                item["_is_selected"] = is_selected
                item["_index"] = i
                line = self._format_command_line(item, cmd_category)
                lines.append(line)

        # Scroll down indicator
        if has_more_below:
            remaining = total_items - visible_end
            lines.append(self._make_scroll_indicator("down", remaining))

        # Footer with keybind hints
        lines.extend(self._make_footer())

        return lines

    def _make_empty_state(self) -> str:
        """Create empty state message using design system."""
        return self._normalize_line_width(
            str(solid(f" {GLOW} No matches ", T().dark[0], T().text_dim, 20))
        )

    def _make_scroll_indicator(self, direction: str, count: int) -> str:
        """Create scroll indicator using design system.

        Args:
            direction: "up" or "down"
            count: Number of items in that direction

        Returns:
            Formatted scroll indicator string.
        """
        arrow = ARROW_UP if direction == "up" else ARROW_DOWN
        line_width = self._get_menu_width()

        # Use secondary color for scroll indicators
        text = f" {arrow} {count} more "
        return self._normalize_line_width(
            str(solid(text.ljust(line_width), T().secondary[0], T().text, line_width))
        )

    def _make_footer(self) -> List[str]:
        """Create footer with keybind hints using design system.

        Returns:
            List of formatted footer lines.
        """
        hint_text = "↑↓ navigate   ⏎ select   esc cancel"
        return [self._normalize_line_width(solid_fg(f"    {hint_text}", T().text_dim))]

    def _format_subcommand_item(self, item: Dict[str, Any], is_selected: bool) -> str:
        """Format a single subcommand as a selectable menu item using design system.

        Args:
            item: Subcommand item dict with subcommand_name, subcommand_args, subcommand_desc.
            is_selected: Whether this item is currently selected.

        Returns:
            Formatted subcommand line.
        """
        name = item.get("subcommand_name", "")
        args = item.get("subcommand_args", "")
        desc = item.get("subcommand_desc", "")

        # Format: "      new <name> <cmd>  Create session..."
        cmd_part = f"{name}"
        if args:
            cmd_part += f" {args}"

        # Pad command part to align descriptions
        cmd_padded = cmd_part.ljust(20)

        if is_selected:
            # Selected subcommand - highlighted with ai_tag color
            line = (
                f"    {solid_fg(GLOW, T().ai_tag)} "
                f"{_make_fg(T().user_tag)}{BOLD}{cmd_padded}{RESET} "
                f"{_make_fg(T().text)}{desc}{RESET}"
            )
        else:
            text = f"      {cmd_padded} {desc}"
            width = self._get_menu_width()
            line = solid(text.ljust(width), T().dark[0], T().text, width)

        return self._normalize_line_width(line)

    def _normalize_line_width(self, line: str) -> str:
        """Pad or truncate a rendered line to the menu width.

        Uses ANSI-aware visible width handling so redraws do not leave stale
        text tails when a newly rendered line is shorter than the previous one.
        """
        target_width = self._get_menu_width()
        visible_text = ANSI_CSI_PATTERN.sub("", line)
        visible_len = len(visible_text)

        if visible_len < target_width:
            return f"{line}{' ' * (target_width - visible_len)}"
        if visible_len == target_width:
            return line

        trimmed_chars = []
        visible_count = 0
        i = 0
        while i < len(line) and visible_count < target_width:
            match = ANSI_CSI_PATTERN.match(line, i)
            if match:
                trimmed_chars.append(match.group(0))
                i = match.end()
                continue

            trimmed_chars.append(line[i])
            visible_count += 1
            i += 1

        trimmed_chars.append(RESET)
        return "".join(trimmed_chars)

    def _format_category_header(self, category: str) -> str:
        """Format category header using design system status_v2 style.

        Args:
            category: Category identifier.

        Returns:
            Formatted category header string.
        """
        config = CATEGORY_CONFIG.get(
            category, {"name": "???", "icon": "?", "full": category.title()}
        )
        line_width = self._get_menu_width()

        # Build category header as a mini status bar
        icon_text = f" {config['icon']} "
        name_text = f" {config['full']} "

        # Icon segment (ai_tag) + name segment (dark)
        icon_part = solid(icon_text, T().ai_tag, T().text_dark, 4)
        name_part = solid(
            name_text.ljust(line_width - 4), T().dark[0], T().text, line_width - 4
        )

        return self._normalize_line_width(f"{icon_part}{name_part}")

    def _sort_commands_by_category(
        self, commands: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Sort commands by category order for grouped display.

        Args:
            commands: List of command dictionaries.

        Returns:
            Sorted list of commands.
        """

        def get_category_order(cmd):
            category = cmd.get("category", "custom")
            # Handle CommandCategory enum
            if hasattr(category, "value"):
                category = category.value
            try:
                return CATEGORY_ORDER.index(category)
            except ValueError:
                return len(CATEGORY_ORDER)  # Unknown categories go last

        return sorted(commands, key=get_category_order)

    def _group_commands_by_category(self) -> Dict[str, List[Dict[str, Any]]]:
        """Group commands by category for organized display.

        Returns:
            Dictionary mapping category names to command lists.
        """
        categorized: Dict[str, List[Dict[str, Any]]] = {}

        for i, cmd in enumerate(self.current_commands):
            category = cmd.get("category", "custom")
            if category not in categorized:
                categorized[category] = []

            # Add selection info to command
            cmd_with_selection = cmd.copy()
            cmd_with_selection["_is_selected"] = i == self.selected_index
            cmd_with_selection["_index"] = i

            categorized[category].append(cmd_with_selection)

        return categorized

    def _format_category_name(self, category: str) -> str:
        """Format category name for display.

        Args:
            category: Category identifier.

        Returns:
            Formatted category name.
        """
        category_names = {
            "system": "Core System",
            "conversation": "Conversation Management",
            "agent": "Agent Management",
            "development": "Development Tools",
            "file": "File Management",
            "task": "Task Management",
            "custom": "Plugin Commands",
        }
        return category_names.get(category, category.title())

    def _format_command_line(
        self, cmd: Dict[str, Any], category: str = "custom"
    ) -> str:
        """Format a single command line using design system.

        Args:
            cmd: Command dictionary with display info.
            category: Category for color theming.

        Returns:
            Formatted command line string.
        """
        is_selected = cmd.get("_is_selected", False)
        name = cmd["name"]
        description = cmd.get("description", "")
        aliases = cmd.get("aliases", [])
        line_width = self._get_menu_width()

        if is_selected:
            # SELECTED: one solid surface so slash search does not look segmented.
            name_part = f"/{name}"

            alias_hint = ""
            alias_len = 0
            if aliases:
                alias_str = " ".join(f"/{a}" for a in aliases[:2])
                alias_hint = f" also: {alias_str}"
                alias_len = len(f" also: {alias_str}")

            desc_area = line_width - len(name_part) - alias_len - 8
            if len(description) > desc_area:
                description = description[: desc_area - 2] + ".."

            text = f" {GLOW} {name_part}  {description.ljust(desc_area)}{alias_hint}"
            line = solid(text, T().input_bg[1], T().text, line_width)
            return self._normalize_line_width(line)
        else:
            # NOT SELECTED: keep a solid surface so terminal wallpaper never
            # bleeds through and lowers contrast.
            name_str = f"/{name}"
            name_col_width = 14

            # Calculate description area
            desc_area = line_width - name_col_width - 8
            if len(description) > desc_area:
                description = description[: desc_area - 2] + ".."

            # Dot leader
            dots = DOT * max(2, name_col_width - len(name_str))

            text = (
                f"   {name_str.ljust(name_col_width)} "
                f"{dots} {description}"
            )
            line = solid(text.ljust(line_width), T().dark[0], T().text, line_width)
            return self._normalize_line_width(line)

    def _display_menu_overlay(self, menu_lines: List[str]) -> None:
        """Display menu as overlay on terminal.

        Args:
            menu_lines: Formatted menu lines to display.
        """
        try:
            # Store menu content for INPUT_RENDER event response
            self.current_menu_lines = menu_lines

            # Log menu for debugging (strip ANSI codes for readability)
            self.logger.info("=== COMMAND MENU ===")
            for line in menu_lines:
                clean_line = ANSI_ESCAPE_PATTERN.sub("", line)
                self.logger.info(clean_line)
            self.logger.info("=== END MENU ===")

        except Exception as e:
            self.logger.error(f"Error preparing menu display: {e}")

    def _clear_menu(self) -> None:
        """Clear menu from display."""
        try:
            # Clear overlay if renderer supports it
            if hasattr(self.renderer, "hide_overlay"):
                self.renderer.hide_overlay()
            elif hasattr(self.renderer, "clear_menu"):
                self.renderer.clear_menu()
            else:
                # Fallback: log clear
                self.logger.info("Command menu cleared")

        except Exception as e:
            self.logger.error(f"Error clearing menu: {e}")

    def get_menu_stats(self) -> Dict[str, Any]:
        """Get menu statistics for debugging.

        Returns:
            Dictionary with menu statistics.
        """
        return {
            "active": self.menu_active,
            "command_count": len(self.current_commands),
            "selected_index": self.selected_index,
            "filter_text": self.filter_text,
            "selected_command": self.get_selected_command(),
            "scroll_offset": self.scroll_offset,
            "max_visible_items": self.max_visible_items,
            "visible_range": (
                f"{self.scroll_offset}-"
                f"{min(self.scroll_offset + self.max_visible_items, len(self.current_commands))}"
            ),
        }
