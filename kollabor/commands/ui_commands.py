"""UI-related slash commands for Kollab."""

import asyncio
from typing import Optional

from kollabor_events.models import CommandCategory, CommandDefinition
from kollabor_tui.modals import Modal
from kollabor_tui.widget_showcase import get_widget_showcase


class WidgetShowcaseModal(Modal):
    """Interactive modal for browsing widget showcase."""

    def __init__(self, terminal_renderer, widget_name: Optional[str] = None):
        """Initialize widget showcase modal.

        Args:
            terminal_renderer: TerminalRenderer instance.
            widget_name: Specific widget to show, or None for gallery.
        """
        super().__init__(
            terminal_renderer, title="Widget Showcase", width=80, height=24
        )
        self.widget_name = widget_name
        self.showcase = get_widget_showcase()
        self.current_category = "all"
        self.categories = ["all"] + self.showcase.get_categories()
        self.current_widget_index = 0
        self.widgets = self.showcase.get_all_widgets()

    def render(self) -> list:
        """Render modal content.

        Returns:
            List of content lines.
        """
        lines = []

        # Header
        lines.append(f"╔═══ Widget Showcase {'=' * 60}╗")
        lines.append(f"║{'Terminal UI Component Gallery':^78}║")
        lines.append(f"╠{'═' * 78}╣")

        if self.widget_name:
            # Show specific widget details
            widget = self.showcase.get_widget(self.widget_name)
            if widget:
                lines.append(f"║ {widget.name}")
                lines.append("║")
                lines.append(f"║ Category: {widget.category.title()}")
                lines.append(f"║ {widget.description}")
                lines.append("║")
                lines.append("║ Features:")
                for feature in widget.features:
                    lines.append(f"║   • {feature}")
                lines.append("║")
                lines.append("║ [Esc] Back to Gallery  [Enter] Try Widget  [q] Close")
            else:
                lines.append(f"║ Widget '{self.widget_name}' not found")
                lines.append("║")
                lines.append("║ [Esc] Back to Gallery  [q] Close")
        else:
            # Show gallery of all widgets
            lines.append(f"║ Category: {self.current_category}")
            lines.append("║")

            # Filter widgets by category
            if self.current_category == "all":
                display_widgets = self.widgets
            else:
                display_widgets = self.showcase.get_widgets_by_category(
                    self.current_category
                )

            for i, widget in enumerate(display_widgets):
                cursor = "▶" if i == self.current_widget_index else " "
                category_tag = f"[{widget.category[0].upper()}]"
                lines.append(
                    f"║ {cursor} {category_tag} {widget.name:<25} {widget.description[:35]}"
                )

            lines.append("║")
            lines.append(f"║ {'=' * 76}")
            lines.append("║")
            lines.append("║ Controls:")
            lines.append("║   ↑↓    Navigate widgets")
            lines.append("║   Tab   Switch category (all/core/advanced)")
            lines.append("║   Enter View widget details")
            lines.append("║   q     Close showcase")

        lines.append(f"╚{'═' * 78}╝")

        return lines

    async def handle_input(self, key_press) -> bool:
        """Handle keyboard input.

        Args:
            key_press: Key press event.

        Returns:
            True if modal should close, False otherwise.
        """

        # Close with q or Escape
        if key_press.key == "q" or (key_press.key == "escape" and not self.widget_name):
            return True

        # Back to gallery
        if key_press.key == "escape" and self.widget_name:
            self.widget_name = None
            return False

        # Navigate widgets
        if key_press.key == "up" and key_press.is_cursor_key:
            if self.current_widget_index > 0:
                self.current_widget_index -= 1
            return False

        if key_press.key == "down" and key_press.is_cursor_key:
            filtered_widgets = self._get_filtered_widgets()
            if self.current_widget_index < len(filtered_widgets) - 1:
                self.current_widget_index += 1
            return False

        # Switch category with Tab
        if key_press.key == "tab":
            current_idx = self.categories.index(self.current_category)
            self.current_category = self.categories[
                (current_idx + 1) % len(self.categories)
            ]
            self.current_widget_index = 0
            return False

        # View widget details
        if key_press.key == "enter":
            filtered_widgets = self._get_filtered_widgets()
            if filtered_widgets:
                widget = filtered_widgets[self.current_widget_index]
                self.widget_name = widget.name
            return False

        return False

    def _get_filtered_widgets(self):
        """Get widgets filtered by current category."""
        if self.current_category == "all":
            return self.widgets
        else:
            return self.showcase.get_widgets_by_category(self.current_category)


async def cmd_widget_showcase(handler, args: str = ""):
    """Display interactive widget showcase (terminal Storybook).

    Usage:
        /widgets                    - Show all widgets in gallery
        /widgets MultiSelectWidget  - Show specific widget details

    Examples:
        /widgets
        /widgets TextAreaWidget
        /widgets SearchableDropdownWidget
    """
    app = handler.app

    # Parse arguments
    widget_name = args.strip() if args.strip() else None

    # Create and show modal
    modal = WidgetShowcaseModal(app.terminal_renderer, widget_name=widget_name)

    # Enter modal mode
    await app.terminal_renderer.message_coordinator.enter_alternate_buffer()

    try:
        # Modal interaction loop
        while True:
            # Render modal
            modal_content = modal.render()
            for line in modal_content:
                app.terminal_renderer._print_to_active_area(line)

            app.terminal_renderer._print_to_active_area("")  # Blank line
            app.terminal_renderer.refresh_display()

            # Wait for input
            key_press = await asyncio.wait_for(
                app.input_handler.get_key_press(), timeout=1.0
            )

            # Handle input in modal
            should_close = await modal.handle_input(key_press)

            if should_close:
                break

    except asyncio.TimeoutError:
        pass
    finally:
        # Exit modal mode and restore UI
        await app.terminal_renderer.message_coordinator.exit_alternate_buffer()


