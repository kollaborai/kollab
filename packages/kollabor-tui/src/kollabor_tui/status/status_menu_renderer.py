"""Status menu renderer for STATUS_TAKEOVER modals.

Mirrors CommandMenuRenderer but renders in status area."""

import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)


class StatusMenuRenderer:
    def __init__(self, renderer):
        self.renderer = renderer
        self.menu_items: List[Dict[str, Any]] = []
        self.selected_index: int = 0
        self.filter_text: str = ""
        self.visible: bool = False

    def show_menu(self, items: List[Dict], filter_text: str = ""):
        """Show status menu with items."""
        self.menu_items = items
        self.filter_text = filter_text
        self.selected_index = 0
        self.visible = True
        self._render_menu()

    def hide_menu(self):
        """Hide status menu."""
        self.visible = False
        self._clear_menu_area()

    def set_selected_index(self, index: int):
        """Set selected menu index."""
        self.selected_index = index
        if self.visible:
            self._render_menu()

    def get_selected_command(self) -> Dict[str, Any]:
        """Get currently selected command."""
        if 0 <= self.selected_index < len(self.menu_items):
            return self.menu_items[self.selected_index]
        return {}

    def _render_menu(self):
        """Render status menu in status area."""
        if not self.visible or not self.menu_items:
            return

        status_lines = []
        for i, item in enumerate(self.menu_items):
            prefix = " > " if i == self.selected_index else "   "
            status_lines.append(f"{prefix}{item.get('name', '')}")
            if "description" in item:
                status_lines.append(f"   {item['description'][:60]}")

        # Render in status area (delegate to renderer)
        self.renderer.status_renderer.render_status_lines(
            status_lines[:4]
        )  # Limit to status height

    def _clear_menu_area(self):
        """Clear status menu area."""
        self.renderer.status_renderer.clear_status_area()
