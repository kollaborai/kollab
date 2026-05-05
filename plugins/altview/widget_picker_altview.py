"""Widget picker as an AltView plugin.

Full-screen searchable, filterable widget browser for adding widgets
to status bar slots. Replaces the modal approach with a proper AltView
that renders via the framework's renderer and input pipeline.
"""

import logging
import re
from typing import Any, List, Optional

from kollabor_tui.altview.base import AltView, AltViewMetadata
from kollabor_tui.design_system import C, T, solid, solid_fg
from kollabor_tui.key_parser import KeyPress

logger = logging.getLogger(__name__)


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences from text."""
    return re.sub(r"\033\[[^m]*m", "", text)


def _wrap_text(text: str, width: int) -> List[str]:
    """Wrap text to fit within width."""
    if not text:
        return [""]
    words = text.split()
    lines: List[str] = []
    current = ""
    for word in words:
        test = f"{current} {word}".strip()
        if len(test) <= width:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines if lines else [""]


class WidgetPickerAltView(AltView):
    """AltView for browsing and selecting widgets to add to status layout.

    Features:
        - Full terminal width rendering
        - Category filtering (All / Core / Plugins)
        - Incremental search
        - Scrollable widget list with category badges
        - Live preview of selected widget
        - Widget description and metadata display

    Usage:
        picker = WidgetPickerAltView()
        picker.set_context(widget_registry, layout_manager, row_id, slot_index)
        # push onto altview stack -- after exit, read picker.selected_widget_id
    """

    def __init__(self) -> None:
        metadata = AltViewMetadata(
            plugin_type="widget-picker",
            description="Browse and add widgets to status layout",
            version="1.0.0",
            author="Kollabor",
            category="system",
            icon="[+]",
            aliases=["widget-add"],
            supports_named_sessions=False,
            supports_background=False,
        )
        super().__init__(metadata)

        self.target_fps: float = 15.0

        # Context -- set by caller before push
        self._widget_registry: Any = None
        self._layout_manager: Any = None
        self._row_id: int = 0
        self._slot_index: int = 0

        # Widget data
        self._all_widgets: List[Any] = []
        self._filtered_widgets: List[Any] = []

        # UI state
        self._search_query: str = ""
        self._selected_index: int = 0
        self._category_filter: Optional[str] = None  # None | "core" | "plugin"
        self._scroll_offset: int = 0

        # Result
        self._result: Optional[str] = None

    # -- public API --

    def set_context(
        self,
        widget_registry: Any,
        layout_manager: Any,
        row_id: int,
        slot_index: int,
    ) -> None:
        """Set required context before pushing onto the stack.

        Args:
            widget_registry: StatusWidgetRegistry with available widgets.
            layout_manager: StatusLayoutManager for target layout.
            row_id: Row ID where the widget will be inserted.
            slot_index: Slot position within the row.
        """
        self._widget_registry = widget_registry
        self._layout_manager = layout_manager
        self._row_id = row_id
        self._slot_index = slot_index

    @property
    def selected_widget_id(self) -> Optional[str]:
        """The ID of the widget the user selected, or None if cancelled."""
        return self._result

    # -- lifecycle --

    async def on_enter(self, renderer: Any) -> None:
        """Initialize state and load widgets."""
        self._renderer = renderer
        logger.info(
            "WidgetPickerAltView entered (row=%d slot=%d)",
            self._row_id,
            self._slot_index,
        )

        # Reset UI state
        self._search_query = ""
        self._selected_index = 0
        self._category_filter = None
        self._scroll_offset = 0
        self._result = None

        # Load widgets
        if self._widget_registry:
            self._all_widgets = list(self._widget_registry.get_all())
        else:
            self._all_widgets = []
            logger.warning("WidgetPickerAltView: no widget registry")

        self._filtered_widgets = self._all_widgets.copy()

    # -- rendering --

    async def render_frame(self, delta_time: float) -> bool:
        """Render the widget picker UI."""
        if not self.renderer:
            return False

        width, height = self.renderer.get_terminal_size()
        theme = T()

        row = 0

        # -- title bar --
        self.renderer.write_at(
            0, row, solid_fg(C["half_bottom"] * width, theme.primary[0]), ""
        )
        row += 1

        title = f"  ADD WIDGET  --  Row {self._row_id}, Slot {self._slot_index}"
        self.renderer.write_at(
            0,
            row,
            solid(title.ljust(width), theme.primary[0], theme.text_dark, width),
            "",
        )
        row += 1

        # -- category filter bar --
        cat_map = {None: "All", "core": "Core", "plugin": "Plugins"}
        parts = []
        for i, (cat_key, label) in enumerate(cat_map.items(), 1):
            cat_val = cat_key if cat_key else "all"
            current = self._category_filter or "all"
            if current == cat_val:
                parts.append(f"[{i}] {label}")
            else:
                parts.append(f" {i}  {label}")
        filter_line = ("  " + "    ".join(parts)).ljust(width)
        self.renderer.write_at(
            0, row, solid(filter_line, theme.dark[0], theme.text_dim, width), ""
        )
        row += 1

        # -- search bar --
        search_line = f"  Search: {self._search_query}_"
        self.renderer.write_at(
            0,
            row,
            solid(search_line.ljust(width), theme.dark[0], theme.text, width),
            "",
        )
        row += 1

        # -- blank separator --
        self.renderer.write_at(
            0, row, solid(" " * width, theme.dark[0], theme.text, width), ""
        )
        row += 1

        # -- widget list --
        # Reserve lines for: preview(4) + desc(2) + meta(1) + footer(3) = 10
        list_height = max(3, height - row - 10)
        page_size = list_height
        total = len(self._filtered_widgets)

        start_idx = max(0, min(self._scroll_offset, max(0, total - page_size)))
        visible = self._filtered_widgets[start_idx : start_idx + page_size]

        for i, widget in enumerate(visible):
            actual_idx = start_idx + i
            cat_badge = self._get_category_badge(widget)
            name = widget.name
            is_selected = actual_idx == self._selected_index

            prefix = (
                f"  {C['arrow_right']} [{cat_badge}] "
                if is_selected
                else f"    [{cat_badge}] "
            )
            remaining = width - len(_strip_ansi(prefix))
            content = prefix + name[:remaining]

            if is_selected:
                self.renderer.write_at(
                    0,
                    row,
                    solid(
                        content.ljust(width), theme.primary[0], theme.text_dark, width
                    ),
                    "",
                )
            else:
                self.renderer.write_at(
                    0,
                    row,
                    solid(content.ljust(width), theme.dark[0], theme.text_dim, width),
                    "",
                )
            row += 1

        # fill remaining list rows
        for _ in range(page_size - len(visible)):
            self.renderer.write_at(
                0, row, solid(" " * width, theme.dark[0], theme.text, width), ""
            )
            row += 1

        # -- scroll indicator --
        if total > page_size:
            scroll_info = (
                f"  ({start_idx + 1}-{min(start_idx + page_size, total)} of {total})"
            )
        else:
            scroll_info = f"  {total} widget{'s' if total != 1 else ''}"
        self.renderer.write_at(
            0,
            row,
            solid(scroll_info.ljust(width), theme.dark[0], theme.text_dim, width),
            "",
        )
        row += 1

        # -- blank separator --
        self.renderer.write_at(
            0, row, solid(" " * width, theme.dark[0], theme.text, width), ""
        )
        row += 1

        # -- preview + description --
        if 0 <= self._selected_index < total:
            selected_widget = self._filtered_widgets[self._selected_index]

            # preview header
            self.renderer.write_at(
                0,
                row,
                solid("  Preview:".ljust(width), theme.dark[0], theme.text_dim, width),
                "",
            )
            row += 1

            # live preview
            preview_width = width - 8
            preview_str = self._render_widget_preview(selected_widget, preview_width)
            preview_clean = _strip_ansi(preview_str)
            pad_right = max(0, width - len(preview_clean) - 6)
            preview_line = f"    {preview_str}{' ' * pad_right}"
            self.renderer.write_at(0, row, preview_line, "")
            row += 1

            # description (up to 2 lines)
            desc_lines = _wrap_text(selected_widget.description, width - 6)
            for dl in desc_lines[:2]:
                self.renderer.write_at(
                    0,
                    row,
                    solid(f"  {dl}".ljust(width), theme.dark[0], theme.text_dim, width),
                    "",
                )
                row += 1

            # metadata
            meta_parts = [f"id: {selected_widget.id}"]
            if selected_widget.interactive:
                meta_parts.append(f"interactive: {selected_widget.interaction_type}")
            meta_line = "  " + "  |  ".join(meta_parts)
            self.renderer.write_at(
                0,
                row,
                solid(meta_line.ljust(width), theme.dark[0], theme.text_dim, width),
                "",
            )
            row += 1
        else:
            self.renderer.write_at(
                0,
                row,
                solid(
                    "  No widgets found".ljust(width),
                    theme.dark[0],
                    theme.text_dim,
                    width,
                ),
                "",
            )
            row += 1
            # fill remaining preview area
            for _ in range(3):
                self.renderer.write_at(
                    0, row, solid(" " * width, theme.dark[0], theme.text, width), ""
                )
                row += 1

        # fill gap before footer
        while row < height - 2:
            self.renderer.write_at(
                0, row, solid(" " * width, theme.dark[0], theme.text, width), ""
            )
            row += 1

        # -- footer --
        self.renderer.write_at(
            0, row, solid_fg(C["half_bottom"] * width, theme.dark[1]), ""
        )
        row += 1
        footer = " Up/Down: Navigate | Enter: Add | Esc: Cancel | 1/2/3: Filter | Type: Search"
        self.renderer.write_at(
            0,
            row,
            solid(footer[:width].ljust(width), theme.dark[1], theme.text_dim, width),
            "",
        )

        return True

    # -- input handling --

    async def handle_input(self, key_press: KeyPress) -> bool:
        """Handle keyboard input. Returns True to exit the view."""
        name = key_press.name or ""
        char = key_press.char or ""

        # exit
        if name == "Escape":
            self._result = None
            return True

        # confirm selection
        if name == "Enter":
            if 0 <= self._selected_index < len(self._filtered_widgets):
                self._result = self._filtered_widgets[self._selected_index].id
                logger.info("Widget selected: %s", self._result)
            else:
                self._result = None
            return True

        # navigation
        if name == "ArrowUp":
            if self._selected_index > 0:
                self._selected_index -= 1
                self._update_scroll()
            return False

        if name == "ArrowDown":
            if self._selected_index < len(self._filtered_widgets) - 1:
                self._selected_index += 1
                self._update_scroll()
            return False

        if name == "PageUp":
            page = self._get_page_size()
            self._selected_index = max(0, self._selected_index - page)
            self._update_scroll()
            return False

        if name == "PageDown":
            page = self._get_page_size()
            max_idx = len(self._filtered_widgets) - 1
            self._selected_index = min(max_idx, self._selected_index + page)
            self._update_scroll()
            return False

        if name == "Home":
            self._selected_index = 0
            self._update_scroll()
            return False

        if name == "End":
            self._selected_index = max(0, len(self._filtered_widgets) - 1)
            self._update_scroll()
            return False

        # category filters
        if char == "1":
            self._set_category_filter(None)
            return False
        if char == "2":
            self._set_category_filter("core")
            return False
        if char == "3":
            self._set_category_filter("plugin")
            return False

        # search typing
        if name == "Backspace" or name == "Delete":
            if self._search_query:
                self._search_query = self._search_query[:-1]
                self._apply_filters()
            return False

        if char and char.isprintable() and len(char) == 1:
            # digits 1-3 handled above as category, let other digits through for search
            self._search_query += char
            self._apply_filters()
            return False

        return False

    # -- filtering --

    def _set_category_filter(self, category: Optional[str]) -> None:
        """Set category filter and re-apply."""
        self._category_filter = category
        self._selected_index = 0
        self._scroll_offset = 0
        self._apply_filters()
        logger.debug("Category filter: %s", category or "all")

    def _apply_filters(self) -> None:
        """Apply search query and category filter."""
        search_lower = self._search_query.lower()
        filtered = []

        for widget in self._all_widgets:
            # category filter
            if self._category_filter:
                widget_cat = (
                    widget.category.value
                    if hasattr(widget.category, "value")
                    else str(widget.category)
                )
                if widget_cat != self._category_filter:
                    continue

            # search filter
            if search_lower:
                name_match = search_lower in widget.name.lower()
                desc_match = search_lower in widget.description.lower()
                id_match = search_lower in widget.id.lower()
                if not (name_match or desc_match or id_match):
                    continue

            filtered.append(widget)

        self._filtered_widgets = filtered

        if self._selected_index >= len(filtered):
            self._selected_index = max(0, len(filtered) - 1)

        self._update_scroll()

    def _update_scroll(self) -> None:
        """Keep selected item visible within the scroll window."""
        page_size = self._get_page_size()
        if self._selected_index < self._scroll_offset:
            self._scroll_offset = self._selected_index
        elif self._selected_index >= self._scroll_offset + page_size:
            self._scroll_offset = self._selected_index - page_size + 1

    def _get_page_size(self) -> int:
        """Number of widget list items visible based on terminal height."""
        if not self.renderer:
            return 10
        _, height = self.renderer.get_terminal_size()
        # header(2) + filter(1) + search(1) + sep(1) + scroll(1) + sep(1)
        # + preview(1) + preview_content(1) + desc(2) + meta(1) + gap + footer(2)
        return max(3, int(height) - 15)

    # -- helpers --

    def _get_category_badge(self, widget: Any) -> str:
        """Single-char badge for the widget's category."""
        cat = (
            widget.category.value
            if hasattr(widget.category, "value")
            else str(widget.category)
        )
        return cat[0].upper() if cat else "?"

    def _render_widget_preview(self, widget: Any, width: int) -> str:
        """Render a live preview of the widget."""
        try:
            rendered = widget.render(width, None)
            return str(rendered.replace("\n", "").replace("\r", ""))
        except Exception:
            return f"[{widget.id}]"
