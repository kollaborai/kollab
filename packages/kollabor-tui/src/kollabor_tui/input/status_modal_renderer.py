"""Status modal rendering component.

Responsible for generating formatted lines for status modal display.
Uses the modern design system for consistent styling.
"""

import logging
from typing import Any, List

from kollabor_tui.design_system import T, solid, solid_fg
from kollabor_tui.terminal_state import get_terminal_width

logger = logging.getLogger(__name__)


class StatusModalRenderer:
    """Renders status modal content with modern design system styling.

    This component handles the visual presentation of status modals,
    using the application's design system for consistent theming.

    Attributes:
        renderer: Terminal renderer for accessing terminal state.
    """

    def __init__(self, renderer: Any) -> None:
        """Initialize the status modal renderer.

        Args:
            renderer: Terminal renderer instance for accessing terminal state.
        """
        self.renderer = renderer

    def _get_terminal_width(self) -> int:
        """Get terminal width using global terminal state.

        Returns:
            Terminal width in columns.
        """
        return get_terminal_width()

    def generate_status_modal_lines(self, ui_config: Any) -> List[str]:
        """Generate formatted lines for status modal display using design system.

        Args:
            ui_config: UI configuration for the status modal.

        Returns:
            List of formatted lines for display.
        """
        try:
            # Get dynamic terminal width
            terminal_width = self._get_terminal_width()
            # Modal width = terminal_width - 2 (matching config modal)
            box_width = terminal_width - 2
            # Content width = box width - 6 (for padding on each side)
            content_width = box_width - 6

            content_lines = []

            # Modal content based on config
            modal_config = ui_config.modal_config or {}

            if "sections" in modal_config:
                for section in modal_config["sections"]:
                    commands = section.get("commands", [])
                    for cmd in commands:
                        name = cmd.get("name", "")
                        description = cmd.get("description", "")

                        # Format command line with alignment
                        # Name gets 30 chars, rest is description
                        name_width = min(30, content_width // 3)
                        desc_width = content_width - name_width - 1

                        # Truncate if needed
                        if len(name) > name_width:
                            name = name[: name_width - 3] + "..."
                        if len(description) > desc_width:
                            description = description[: desc_width - 3] + "..."

                        cmd_line = f"{name:<{name_width}} {description}"
                        content_lines.append(cmd_line)

            # Get footer
            footer = modal_config.get(
                "footer",
                "Esc/Enter close • /help <command> for details",
            )

            # Build the box using design system
            lines = []
            theme = T()
            bg_color = theme.dark[0]
            fg_color = theme.text

            # Top edge - solid block
            lines.append(solid_fg("▄" * box_width, bg_color))

            # Content lines with solid background
            for line in content_lines:
                # Pad line to content width with 3-char indent
                padded = f"   {line:<{content_width}}"
                lines.append(solid(padded, bg_color, fg_color, box_width))

            # Empty line before footer
            lines.append(solid(" " * box_width, bg_color, fg_color, box_width))

            # Footer line (dimmed)
            footer_padded = f"   {footer:<{content_width}}"
            # Use dim text for footer
            footer_line = solid(footer_padded, bg_color, theme.text_dim, box_width)
            lines.append(footer_line)

            # Bottom edge - solid block
            lines.append(solid_fg("▀" * box_width, bg_color))

            return lines

        except Exception as e:
            logger.error(f"Error generating status modal lines: {e}")
            import traceback

            logger.error(traceback.format_exc())
            return [f"Error displaying status modal: {e}"]
