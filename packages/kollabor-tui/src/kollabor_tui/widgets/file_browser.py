"""File browser widget for filesystem navigation and path selection."""

from pathlib import Path
from typing import Any, List

from kollabor_tui.design_system import T, TagBox
from kollabor_tui.key_parser import KeyPress
from kollabor_tui.visual_effects import ColorPalette

from .base_widget import BaseWidget


class FileBrowserWidget(BaseWidget):
    """Widget for browsing filesystem and selecting files or directories.

    Supports navigation, filtering, and path selection.
    Ideal for import/export paths, conversation save locations, and file selection.

    Example config:
    {
        "label": "Select Export Path",
        "start_dir": "/home/user/projects",
        "file_filter": "*.json",
        "show_hidden": False,
        "select_dirs_only": False
    }
    """

    def __init__(self, config: dict, config_path: str, config_service=None):
        """Initialize file browser widget.

        Args:
            config: Widget configuration with path and filter options.
            config_path: Dot-notation path to config value.
            config_service: ConfigService instance for reading/writing values.
        """
        super().__init__(config, config_path, config_service)

        # Navigation
        start_dir = self.config.get("start_dir", ".")
        self.current_path = Path(start_dir).expanduser().resolve()
        self.cursor_index = 0
        self.scroll_row = 0

        # Display options
        self.file_filter = self.config.get("file_filter", "*")
        self.show_hidden = self.config.get("show_hidden", False)
        self.select_dirs_only = self.config.get("select_dirs_only", False)
        self.max_visible = self.config.get("max_visible", 15)

        # Icons
        self.dir_icon = self.config.get("dir_icon", "📁")
        self.file_icon = self.config.get("file_icon", "📄")
        self.parent_icon = self.config.get("parent_icon", "⬆️")

        # Colors
        self.colors = ColorPalette()

        # Load initial directory contents
        self.contents = self._load_directory()

    def render(self) -> List[str]:
        """Render file browser widget.

        Returns:
            List of strings representing widget display lines.
        """
        lines = []

        # Label line
        label = self.get_label()
        if label:
            label_color = (
                self.colors.accent_color if self.focused else self.colors.primary_color
            )
            lines.append(f"{label_color}{label}{self.colors.reset}")

        # Current path display
        path_str = str(self.current_path)
        if len(path_str) > 50:
            # Truncate long paths showing start and end
            path_str = f"...{path_str[-47:]}"
        path_display = f"{self.colors.accent_color}{path_str}{self.colors.reset}"
        lines.append(f"📍 {path_display}")

        # Separator
        lines.append(f"{self.colors.muted_color}{'─' * 50}{self.colors.reset}")

        # Directory contents
        visible_contents = self.contents[
            self.scroll_row : self.scroll_row + self.max_visible
        ]

        for i, item in enumerate(visible_contents):
            actual_index = self.scroll_row + i
            is_cursor = actual_index == self.cursor_index

            # Build item line
            cursor = "▶ " if is_cursor and self.focused else "  "

            # Icon
            if item["is_parent"]:
                icon = self.parent_icon
            elif item["is_dir"]:
                icon = self.dir_icon
            else:
                icon = self.file_icon

            # Name
            name = item["name"]

            # Highlight cursor
            if is_cursor and self.focused:
                name = f"{self.colors.highlight}{self.colors.background}{name}{self.colors.reset}"
            elif item["is_dir"]:
                name = f"{self.colors.primary_color}{name}{self.colors.reset}"

            # Size info for files
            size_info = ""
            if not item["is_dir"] and not item["is_parent"]:
                size = item.get("size", 0)
                size_info = self._format_size(size)
                size_info = f"{self.colors.muted_color}{size_info}{self.colors.reset}"

            lines.append(f"{cursor}{icon} {name:<30} {size_info}")

        # Scroll indicator
        if len(self.contents) > self.max_visible:
            show_start = self.scroll_row + 1
            show_end = min(self.scroll_row + self.max_visible, len(self.contents))
            scroll_info = (
                f"{self.colors.muted_color}Showing {show_start}-{show_end} of "
                f"{len(self.contents)}{self.colors.reset}"
            )
            lines.append(scroll_info)

        # Help text when focused
        if self.focused:
            help_text = (
                f"{self.colors.muted_color}↑↓: Navigate | Enter: Select | "
                f"Backspace: Parent | Esc: Cancel{self.colors.reset}"
            )
            lines.append(help_text)

        return lines

    def render_modern(self, width: int = 50) -> List[str]:  # type: ignore[override]
        """Render file browser with modern design system styling.

        Uses TagBox for label and file/directory items with icons.

        Args:
            width: Total width of file browser widget (default: 50).

        Returns:
            List containing file browser display lines with modern styling.
        """
        lines = []
        label = self.get_label()
        tag_width = 3
        content_width = width - tag_width

        # Label line with folder icon
        if label:
            icon = " 📁 " if self.focused else "   "
            tag_bg = T().primary[0] if self.focused else T().dark[0]
            tag_fg = T().text_dark if self.focused else T().text_dim
            content_colors = T().input_bg if self.focused else T().dark[0]
            content_fg = T().text if self.focused else T().text_dim

            content = f" {label}"
            label_line = TagBox.render(
                lines=[content],
                tag_bg=tag_bg,
                tag_fg=tag_fg,
                tag_width=tag_width,
                content_colors=content_colors,
                content_fg=content_fg,
                content_width=content_width,
                tag_chars=[icon],
                use_gradient=self.focused,
            )
            lines.append(label_line)

        # Current path display with TagBox
        path_str = str(self.current_path)
        if len(path_str) > width - 10:
            # Truncate long paths showing start and end
            path_str = f"...{path_str[-(width - 13):]}"

        icon = " 📍 "
        content = f" {path_str}"
        path_line = TagBox.render(
            lines=[content],
            tag_bg=T().secondary[0],
            tag_fg=T().text,
            tag_width=tag_width,
            content_colors=T().dark[0],
            content_fg=T().text,
            content_width=content_width,
            tag_chars=[icon],
            use_gradient=False,
        )
        lines.append(path_line)

        # Separator
        lines.append(f"{T().fg(T().dim[0])}{'─' * width}{T().reset}")

        # Directory contents
        visible_contents = self.contents[
            self.scroll_row : self.scroll_row + self.max_visible
        ]

        for i, item in enumerate(visible_contents):
            actual_index = self.scroll_row + i
            is_cursor = actual_index == self.cursor_index

            # Build item line
            if is_cursor and self.focused:
                tag_icon = " ▶ "
                tag_bg = T().primary[0]
                tag_fg = T().text_dark
                content_colors = T().input_bg
                content_fg = T().text
            else:
                tag_icon = "   "
                tag_bg = T().dark[0]
                tag_fg = T().text_dim
                content_colors = T().dark[0] if not item["is_dir"] else T().primary
                content_fg = T().text_dim if not item["is_dir"] else T().text

            # Icon
            if item["is_parent"]:
                item_icon = self.parent_icon
            elif item["is_dir"]:
                item_icon = self.dir_icon
            else:
                item_icon = self.file_icon

            # Name
            name = item["name"]

            # Size info for files
            size_info = ""
            if not item["is_dir"] and not item["is_parent"]:
                size = item.get("size", 0)
                size_info = self._format_size(size)

            content = f" {item_icon} {name:<30} {size_info}"

            item_line = TagBox.render(
                lines=[content],
                tag_bg=tag_bg,
                tag_fg=tag_fg,
                tag_width=tag_width,
                content_colors=content_colors,
                content_fg=content_fg,
                content_width=content_width,
                tag_chars=[tag_icon],
                use_gradient=is_cursor and self.focused,
            )
            lines.append(item_line)

        # Scroll indicator
        if len(self.contents) > self.max_visible:
            show_start = self.scroll_row + 1
            show_end = min(self.scroll_row + self.max_visible, len(self.contents))
            scroll_info = (
                f"     {T().fg(T().dim[0])}Showing {show_start}-{show_end} of "
                f"{len(self.contents)}{T().reset}"
            )
            lines.append(scroll_info)

        # Help text when focused
        if self.focused:
            help_text = (
                f"     {T().fg(T().dim[0])}↑↓: Navigate | Enter: Select | "
                f"Backspace: Parent | Esc: Cancel{T().reset}"
            )
            lines.append(help_text)

        return lines

    def handle_input(self, key_press: KeyPress) -> bool:
        """Handle keyboard input.

        Args:
            key_press: Key press event to handle.

        Returns:
            True if key press was handled by this widget.
        """
        # Navigation
        if key_press.key == "up" and key_press.is_cursor_key:
            if self.cursor_index > 0:
                self.cursor_index -= 1
                self._scroll_to_cursor()
            return True

        if key_press.key == "down" and key_press.is_cursor_key:
            if self.cursor_index < len(self.contents) - 1:
                self.cursor_index += 1
                self._scroll_to_cursor()
            return True

        # Page up/down
        if key_press.key == "pageup" and key_press.is_cursor_key:
            self.cursor_index = max(0, self.cursor_index - self.max_visible)
            self._scroll_to_cursor()
            return True

        if key_press.key == "pagedown" and key_press.is_cursor_key:
            self.cursor_index = min(
                len(self.contents) - 1, self.cursor_index + self.max_visible
            )
            self._scroll_to_cursor()
            return True

        # Navigate to parent with backspace
        if key_press.key == "backspace":
            self._navigate_to_parent()
            return True

        # Enter to select or enter directory
        if key_press.key == "enter":
            if self.contents:
                item = self.contents[self.cursor_index]
                if item["is_parent"]:
                    self._navigate_to_parent()
                elif item["is_dir"]:
                    self._enter_directory(item["path"])
                else:
                    # Select file
                    self.set_value(str(item["path"]))
            return True

        # Go to home with Ctrl+~
        if key_press.key == "~" and key_press.ctrl:
            self._navigate_to_home()
            return True

        # Toggle hidden files with Ctrl+H
        if key_press.key == "h" and key_press.ctrl:
            self.show_hidden = not self.show_hidden
            self.contents = self._load_directory()
            return True

        # Create new directory with Ctrl+N
        if key_press.key == "n" and key_press.ctrl:
            # Would need text input widget for directory name
            # For now, just show a message
            return True

        return False

    def get_value(self) -> Any:
        """Get selected path.

        Returns:
            Selected file/directory path as string.
        """
        # If we have a pending value, use it
        if self._pending_value is not None:
            return self._pending_value

        # Otherwise return current path
        return str(self.current_path)

    def _load_directory(self) -> List[dict]:
        """Load directory contents.

        Returns:
            List of directory items with metadata.
        """
        contents = []

        # Add parent directory entry (if not at root)
        if self.current_path.parent != self.current_path:
            contents.append(
                {
                    "name": "..",
                    "path": self.current_path.parent,
                    "is_dir": True,
                    "is_parent": True,
                }
            )

        try:
            # List directory contents
            for item in self.current_path.iterdir():
                # Skip hidden files if not showing them
                if not self.show_hidden and item.name.startswith("."):
                    continue

                is_dir = item.is_dir()

                # Skip files if only selecting directories
                if self.select_dirs_only and not is_dir:
                    continue

                # Apply file filter
                if not is_dir and self.file_filter != "*":
                    import fnmatch

                    if not fnmatch.fnmatch(item.name, self.file_filter):
                        continue

                entry = {
                    "name": item.name,
                    "path": item,
                    "is_dir": is_dir,
                    "is_parent": False,
                }

                # Add size for files
                if not is_dir:
                    try:
                        entry["size"] = item.stat().st_size
                    except OSError:
                        entry["size"] = 0

                contents.append(entry)

        except PermissionError:
            # Handle permission errors gracefully
            pass

        # Sort: directories first, then alphabetically
        contents.sort(key=lambda x: (not x["is_dir"], str(x["name"]).lower()))

        return contents

    def _enter_directory(self, path: Path):
        """Enter a directory.

        Args:
            path: Directory path to enter.
        """
        self.current_path = path
        self.cursor_index = 0
        self.scroll_row = 0
        self.contents = self._load_directory()

    def _navigate_to_parent(self):
        """Navigate to parent directory."""
        if self.current_path.parent != self.current_path:
            self.current_path = self.current_path.parent
            self.cursor_index = 0
            self.scroll_row = 0
            self.contents = self._load_directory()

    def _navigate_to_home(self):
        """Navigate to home directory."""
        home = Path.home()
        self.current_path = home
        self.cursor_index = 0
        self.scroll_row = 0
        self.contents = self._load_directory()

    def _scroll_to_cursor(self):
        """Scroll view to keep cursor visible."""
        if self.cursor_index < self.scroll_row:
            self.scroll_row = self.cursor_index
        elif self.cursor_index >= self.scroll_row + self.max_visible:
            self.scroll_row = self.cursor_index - self.max_visible + 1

    def _format_size(self, size: int | float) -> str:
        """Format file size for display.

        Args:
            size: Size in bytes.

        Returns:
            Formatted size string.
        """
        for unit in ["B", "KB", "MB", "GB"]:
            if size < 1024.0:
                return f"{size:.1f} {unit}"
            size = float(size) / 1024.0
        return f"{size:.1f} TB"

    def is_valid_value(self, value: Any) -> bool:
        """Validate if path exists and meets criteria.

        Args:
            value: Path string to validate.

        Returns:
            True if path is valid.
        """
        try:
            path = Path(value)
            if not path.exists():
                return False

            if self.select_dirs_only:
                return path.is_dir()

            return True
        except (OSError, TypeError):
            return False
