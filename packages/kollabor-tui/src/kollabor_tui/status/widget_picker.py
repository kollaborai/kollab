"""Widget picker modal for adding widgets to status area.

Provides a searchable, filterable list of available widgets that users
can add to their status layout. Supports keyboard navigation, category
filtering, live widget preview, and displays widget descriptions.
"""

import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from kollabor_tui.design_system import T
from kollabor_tui.terminal_state import get_terminal_size

logger = logging.getLogger(__name__)


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return re.sub(r"\033\[[^m]*m", "", text)


@dataclass
class PickerState:
    """State for widget picker modal."""

    widgets: List[Any]  # List of StatusWidget
    filtered_widgets: List[Any]
    search_query: str
    selected_index: int
    category_filter: Optional[str]  # None, "core", "plugin", "script"
    scroll_offset: int


class WidgetPickerModal:
    """Modal for browsing and adding widgets to status layout.

    Features:
    - Search/filter widgets by name or description
    - Category filtering (core, plugin, script)
    - Keyboard navigation (arrows, Enter, Esc)
    - Live widget preview of selected widget
    - Widget description display

    Usage:
        picker = WidgetPickerModal(widget_registry, layout_manager, row_id, slot_index)
        await picker.show()
        # User interacts...
        selected_widget = picker.get_selected_widget()
    """

    def __init__(
        self,
        widget_registry: Any,
        layout_manager: Any,
        row_id: int,
        slot_index: int,
        renderer: Any = None,
    ):
        """Initialize the widget picker modal.

        Args:
            widget_registry: StatusWidgetRegistry instance
            layout_manager: StatusLayoutManager instance
            row_id: Row ID where widget will be added
            slot_index: Position in row where widget will be added
            renderer: Optional TerminalRenderer for direct rendering
        """
        self.widget_registry = widget_registry
        self.layout_manager = layout_manager
        self.row_id = row_id
        self.slot_index = slot_index
        self.renderer = renderer

        # Get all available widgets
        all_widgets = widget_registry.get_all()

        # Initialize picker state
        self.state = PickerState(
            widgets=all_widgets,
            filtered_widgets=all_widgets.copy(),
            search_query="",
            selected_index=0,
            category_filter=None,
            scroll_offset=0,
        )

        self._visible = False
        self._result = None  # Selected widget ID or None

        logger.debug(
            f"WidgetPickerModal initialized for row {row_id}, "
            f"slot {slot_index} with {len(all_widgets)} widgets"
        )

    def get_selected_widget(self) -> Optional[str]:
        """Get the selected widget ID after modal closes.

        Returns:
            Widget ID if selected, None if cancelled
        """
        return self._result

    def is_visible(self) -> bool:
        """Check if modal is currently visible."""
        return self._visible

    def show(self) -> None:
        """Show the modal."""
        self._visible = True
        logger.debug("WidgetPickerModal shown")

    def hide(self) -> None:
        """Hide the modal."""
        self._visible = False
        logger.debug("WidgetPickerModal hidden")

    def cancel(self) -> None:
        """Cancel widget selection (user pressed Esc)."""
        self._result = None
        self.hide()

    def confirm_selection(self) -> None:
        """Confirm the current selection (user pressed Enter)."""
        if 0 <= self.state.selected_index < len(self.state.filtered_widgets):
            selected_widget = self.state.filtered_widgets[self.state.selected_index]
            self._result = selected_widget.id
            logger.info(
                f"Widget selected: {selected_widget.id} ({selected_widget.name})"
            )
        else:
            self._result = None
        self.hide()

    def handle_keypress(self, key_press: Any) -> bool:
        """Handle keyboard input during modal display.

        Args:
            key_press: KeyPress object from input system

        Returns:
            True if key was handled, False otherwise
        """
        key_name = getattr(key_press, "name", None) or getattr(key_press, "key", None)
        key_char = getattr(key_press, "char", "")

        if not self._visible:
            return False

        # Navigation keys
        if key_name == "Escape":
            self.cancel()
            return True
        elif key_name == "Enter":
            self.confirm_selection()
            return True
        elif key_name == "Up":
            self._navigate_up()
            return True
        elif key_name == "Down":
            self._navigate_down()
            return True
        elif key_name == "PageUp":
            self._navigate_page_up()
            return True
        elif key_name == "PageDown":
            self._navigate_page_down()
            return True
        elif key_name == "Home":
            self.state.selected_index = 0
            self._update_scroll()
            return True
        elif key_name == "End":
            self.state.selected_index = len(self.state.filtered_widgets) - 1
            self._update_scroll()
            return True

        # Category filter shortcuts
        elif key_char == "1" or key_name == "F1":
            self._set_category_filter(None)  # All
            return True
        elif key_char == "2" or key_name == "F2":
            self._set_category_filter("core")
            return True
        elif key_char == "3" or key_name == "F3":
            self._set_category_filter("plugin")
            return True

        # Search/filter typing
        elif key_char and key_char.isprintable():
            self.state.search_query += key_char
            self._apply_filters()
            return True
        elif key_name == "Backspace" or key_name == "Delete":
            if self.state.search_query:
                self.state.search_query = self.state.search_query[:-1]
                self._apply_filters()
            return True
        elif key_name == "Ctrl+U":
            # Clear search
            self.state.search_query = ""
            self._apply_filters()
            return True

        return False

    def _navigate_up(self) -> None:
        """Move selection up."""
        if self.state.selected_index > 0:
            self.state.selected_index -= 1
            self._update_scroll()

    def _navigate_down(self) -> None:
        """Move selection down."""
        if self.state.selected_index < len(self.state.filtered_widgets) - 1:
            self.state.selected_index += 1
            self._update_scroll()

    def _navigate_page_up(self) -> None:
        """Move selection up by page."""
        page_size = self._get_page_size()
        self.state.selected_index = max(0, self.state.selected_index - page_size)
        self._update_scroll()

    def _navigate_page_down(self) -> None:
        """Move selection down by page."""
        page_size = self._get_page_size()
        max_index = len(self.state.filtered_widgets) - 1
        self.state.selected_index = min(
            max_index, self.state.selected_index + page_size
        )
        self._update_scroll()

    def _set_category_filter(self, category: Optional[str]) -> None:
        """Set category filter.

        Args:
            category: None (all), "core", "plugin", or "script"
        """
        self.state.category_filter = category
        self.state.selected_index = 0
        self.state.scroll_offset = 0
        self._apply_filters()
        logger.debug(f"Category filter set to: {category or 'all'}")

    def _apply_filters(self) -> None:
        """Apply search query and category filter to widget list."""
        filtered = []

        search_lower = self.state.search_query.lower()

        for widget in self.state.widgets:
            # Apply category filter
            if self.state.category_filter:
                widget_category = (
                    widget.category.value
                    if hasattr(widget.category, "value")
                    else str(widget.category)
                )
                if widget_category != self.state.category_filter:
                    continue

            # Apply search filter
            if search_lower:
                name_match = search_lower in widget.name.lower()
                desc_match = search_lower in widget.description.lower()
                id_match = search_lower in widget.id.lower()

                if not (name_match or desc_match or id_match):
                    continue

            filtered.append(widget)

        self.state.filtered_widgets = filtered

        # Reset selection if out of bounds
        if self.state.selected_index >= len(filtered):
            self.state.selected_index = max(0, len(filtered) - 1)

        self._update_scroll()

    def _update_scroll(self) -> None:
        """Update scroll offset to keep selection visible."""
        page_size = self._get_page_size()

        if self.state.selected_index < self.state.scroll_offset:
            self.state.scroll_offset = self.state.selected_index
        elif self.state.selected_index >= self.state.scroll_offset + page_size:
            self.state.scroll_offset = self.state.selected_index - page_size + 1

    def _get_page_size(self) -> int:
        """Get number of items that fit on a page based on terminal height."""
        _, term_height = get_terminal_size()
        # Reserve lines for: title(1) subtitle(1) blank(1) filter(1) search(1)
        # blank(1) scroll_info(1) blank(1) preview(1) preview_content(1)
        # blank(1) desc(2) meta(1) blank(1) footer(1) = ~16 lines overhead
        return max(5, term_height - 18)

    def _render_preview(self, widget: Any, width: int) -> str:
        """Render a live preview of the widget.

        Args:
            widget: StatusWidget to preview
            width: Available width for preview

        Returns:
            Rendered preview string (may contain ANSI codes)
        """
        try:
            rendered: str = widget.render(width, None)
            # Strip any newlines - widgets should be single line
            rendered = rendered.replace("\n", "").replace("\r", "")
            return rendered
        except Exception:
            return f"[{widget.id}]"

    def render(self) -> List[str]:
        """Render the widget picker modal with live preview.

        Returns:
            List of strings representing each line of modal content
        """
        term_width, term_height = get_terminal_size()

        # Use full terminal width with padding
        modal_width = term_width - 4
        modal_col = 3
        modal_row = 2

        lines = []

        # Helper to create a positioned line using ANSI cursor positioning
        def make_line(
            row: int, content: str, bg_color_rgb=None, fg_color_rgb=None, is_bold=False
        ) -> str:
            pos_code = f"\033[{row};{modal_col}H"
            line = content[:modal_width].ljust(modal_width)
            if fg_color_rgb:
                r, g, b = fg_color_rgb
                line = f"\033[38;2;{r};{g};{b}m{line}\033[39m"
            if is_bold:
                line = f"\033[1m{line}\033[22m"
            if bg_color_rgb:
                r, g, b = bg_color_rgb
                line = f"\033[48;2;{r};{g};{b}m{line}\033[49m"
            return pos_code + line

        # Colors
        primary_rgb = T().primary[0]
        dark_rgb = T().dark[0]
        text_rgb = T().text
        dim_rgb = T().text_dim

        # Clear screen
        lines.append("\033[2J\033[H")

        row = modal_row

        # Title bar
        lines.append(make_line(row, "  ADD WIDGET  ", primary_rgb, text_rgb, True))
        row += 1

        # Subtitle
        lines.append(
            make_line(
                row, f"  Row {self.row_id}, Slot {self.slot_index}  ", dark_rgb, dim_rgb
            )
        )
        row += 1
        lines.append(make_line(row, ""))
        row += 1

        # Category filter bar
        cat_map = {None: "All", "core": "Core", "plugin": "Plugins"}
        filter_parts = []
        for i, (cat_key, label) in enumerate(cat_map.items(), 1):
            cat_value = cat_key if cat_key else "all"
            current = self.state.category_filter or "all"
            if current == cat_value:
                filter_parts.append(f"[{i}] {label}")
            else:
                filter_parts.append(f" {i}  {label}")

        filter_bar = "  " + "    ".join(filter_parts)
        lines.append(make_line(row, filter_bar))
        row += 1

        # Search bar
        search_prompt = f"  Search: {self.state.search_query}_"
        lines.append(make_line(row, search_prompt))
        row += 1
        lines.append(make_line(row, ""))
        row += 1

        # Widget list
        page_size = self._get_page_size()
        total = len(self.state.filtered_widgets)
        start_idx = max(0, min(self.state.scroll_offset, total - page_size))
        visible_widgets = self.state.filtered_widgets[start_idx : start_idx + page_size]

        for i, widget in enumerate(visible_widgets):
            actual_idx = start_idx + i
            cat_badge = widget.category.value[0].upper()
            widget_name = widget.name

            is_selected = actual_idx == self.state.selected_index

            if is_selected:
                prefix = f"  > [{cat_badge}] "
                remaining = modal_width - len(prefix)
                content = prefix + widget_name[:remaining].ljust(remaining)
                lines.append(make_line(row, content, primary_rgb, text_rgb))
            else:
                prefix = f"    [{cat_badge}] "
                remaining = modal_width - len(prefix)
                content = prefix + widget_name[:remaining].ljust(remaining)
                lines.append(make_line(row, content))
            row += 1

        # Fill remaining widget lines
        for _ in range(page_size - len(visible_widgets)):
            lines.append(make_line(row, ""))
            row += 1

        # Scroll indicator
        if total > page_size:
            scroll_info = (
                f"  ({start_idx + 1}-{min(start_idx + page_size, total)} of {total})"
            )
            lines.append(make_line(row, scroll_info, None, dim_rgb))
        else:
            lines.append(make_line(row, ""))
        row += 1

        # Separator
        lines.append(make_line(row, ""))
        row += 1

        # Preview + description for selected widget
        if 0 <= self.state.selected_index < total:
            selected_widget = self.state.filtered_widgets[self.state.selected_index]

            # Preview header
            lines.append(make_line(row, "  Preview:", None, dim_rgb))
            row += 1

            # Live preview - render the widget at a reasonable width
            preview_width = modal_width - 8
            preview_str = self._render_preview(selected_widget, preview_width)

            # Show preview in a box-like container
            preview_clean = _strip_ansi(preview_str)
            pad_total = modal_width - len(preview_clean) - 6
            if pad_total < 0:
                pad_total = 0
            # Use raw preview with ANSI intact, padded in a frame
            preview_line = f"\033[{row};{modal_col}H    {preview_str}{' ' * pad_total}"
            lines.append(preview_line)
            row += 1

            lines.append(make_line(row, ""))
            row += 1

            # Description
            desc_lines = self._wrap_text(selected_widget.description, modal_width - 6)
            for desc_line in desc_lines[:2]:
                lines.append(make_line(row, f"  {desc_line}", None, dim_rgb))
                row += 1

            # Widget metadata
            meta_parts = [f"id: {selected_widget.id}"]
            if selected_widget.interactive:
                meta_parts.append(f"interactive: {selected_widget.interaction_type}")
            meta_line = "  " + "  |  ".join(meta_parts)
            lines.append(make_line(row, meta_line, None, dim_rgb))
            row += 1
        else:
            lines.append(make_line(row, "  No widgets found"))
            row += 1
            lines.append(make_line(row, ""))
            row += 1

        # Footer
        lines.append(make_line(row, ""))
        row += 1
        help_text = " Up/Down: Navigate | Enter: Add | Esc: Cancel | Type: Search "
        lines.append(make_line(row, f"  {help_text}", None, dim_rgb))

        return lines

    def _wrap_text(self, text: str, width: int) -> List[str]:
        """Wrap text to fit within width.

        Args:
            text: Text to wrap
            width: Maximum line width

        Returns:
            List of wrapped lines
        """
        if not text:
            return [""]

        words = text.split()
        lines = []
        current_line = ""

        for word in words:
            test_line = f"{current_line} {word}".strip()

            if len(test_line) <= width:
                current_line = test_line
            else:
                if current_line:
                    lines.append(current_line)
                current_line = word

        if current_line:
            lines.append(current_line)

        return lines if lines else [""]

    def get_modal_config(self) -> Dict[str, Any]:
        """Get modal configuration for use with modal system.

        Returns:
            Modal configuration dictionary
        """
        return {
            "title": "Add Widget",
            "type": "widget_picker",
            "width": 80,
            "height": 24,
            "picker": self,
        }
