"""Modal base classes for Kollab UI system.

Provides base Modal class for creating custom modal dialogs.
"""

import logging
from typing import Any, List, Optional

logger = logging.getLogger(__name__)


class Modal:
    """Base class for modal dialogs.

    Provides common functionality for modal rendering and input handling.
    Subclasses should override render() and handle_keypress() methods.
    """

    def __init__(
        self,
        terminal_renderer: Any,
        title: str = "Modal",
        width: int = 60,
        height: int = 20,
    ):
        """Initialize modal.

        Args:
            terminal_renderer: TerminalRenderer instance for output.
            title: Modal title displayed in header.
            width: Modal width in characters.
            height: Modal height in lines.
        """
        self.terminal_renderer = terminal_renderer
        self.title = title
        self.width = width
        self.height = height
        self.visible = False
        self.result: bool | None = None

    def show(self) -> None:
        """Show the modal."""
        self.visible = True
        logger.debug(f"Modal '{self.title}' shown")

    def hide(self) -> None:
        """Hide the modal."""
        self.visible = False
        logger.debug(f"Modal '{self.title}' hidden")

    def render(self) -> List[str]:
        """Render modal content.

        Returns:
            List of strings representing each line of modal content.
        """
        return [f"Modal: {self.title}"]

    async def handle_keypress(self, key_press: Any) -> bool:
        """Handle keyboard input.

        Args:
            key_press: KeyPress object from input system.

        Returns:
            True if key was handled, False otherwise.
        """
        # Default: Escape closes modal
        if hasattr(key_press, "name") and key_press.name == "Escape":
            self.hide()
            return True
        return False

    def get_result(self) -> Any:
        """Get modal result after closing.

        Returns:
            Result value set during modal interaction.
        """
        return self.result


class ConfirmationModal(Modal):
    """Modal dialog for confirming actions.

    Presents a yes/no confirmation prompt to the user.
    """

    def __init__(
        self,
        terminal_renderer: Any,
        title: str = "Confirm",
        message: str = "",
        confirm_text: str = "Confirm",
        cancel_text: str = "Cancel",
        width: int = 60,
        height: int = 12,
    ):
        """Initialize confirmation modal.

        Args:
            terminal_renderer: TerminalRenderer instance for output.
            title: Modal title displayed in header.
            message: Confirmation message to display.
            confirm_text: Text for confirm button (e.g., "Delete", "Yes").
            cancel_text: Text for cancel button (e.g., "Cancel", "No").
            width: Modal width in characters.
            height: Modal height in lines.
        """
        super().__init__(terminal_renderer, title, width, height)
        self.message = message
        self.confirm_text = confirm_text
        self.cancel_text = cancel_text
        self.confirmed = False

    def render(self) -> List[str]:
        """Render confirmation modal content.

        Returns:
            List of strings representing each line of modal content.
        """
        from kollabor_tui.design_system import T, TagBox, solid, solid_fg

        lines = []

        # Top border (solid block style)
        lines.append(solid_fg("▄" * self.width, T().dark[0]))

        # Empty line for padding
        lines.append(solid(" " * self.width, T().dark[0], T().text))

        # Title section with tag
        title_line = f"  {self.title}"
        lines.append(solid(title_line, T().dark[0], T().text, self.width))

        lines.append(solid(" " * self.width, T().dark[0], T().text))

        # Message
        if self.message:
            # Wrap message if needed (simple word wrap)
            words = self.message.split()
            current_line = "  "
            for word in words:
                test_line = current_line + word + " "
                if len(test_line) > self.width - 4:
                    lines.append(solid(current_line, T().dark[0], T().text, self.width))
                    current_line = "  " + word + " "
                else:
                    current_line = test_line
            if current_line.strip():
                lines.append(solid(current_line, T().dark[0], T().text, self.width))

        lines.append(solid(" " * self.width, T().dark[0], T().text))

        # Instructions
        instruction = f"  Enter: {self.confirm_text}  •  Esc: {self.cancel_text}"
        lines.append(solid(instruction, T().dark[0], T().text_dim, self.width))

        lines.append(solid(" " * self.width, T().dark[0], T().text))

        # Bottom border (solid block style)
        lines.append(solid_fg("▀" * self.width, T().dark[0]))

        return lines

    async def handle_keypress(self, key_press: Any) -> bool:
        """Handle keyboard input.

        Args:
            key_press: KeyPress object from input system.

        Returns:
            True if key was handled, False otherwise.
        """
        key_name = getattr(key_press, "name", None)
        key_char = getattr(key_press, "char", None)

        # Enter confirms the action
        if key_name == "Enter":
            self.confirmed = True
            self.result = True
            self.hide()
            logger.info(f"ConfirmationModal '{self.title}': confirmed")
            return True

        # Escape or 'n' cancels
        if key_name == "Escape" or key_char == "n":
            self.confirmed = False
            self.result = False
            self.hide()
            logger.info(f"ConfirmationModal '{self.title}': cancelled")
            return True

        # 'y' confirms
        if key_char == "y":
            self.confirmed = True
            self.result = True
            self.hide()
            logger.info(f"ConfirmationModal '{self.title}': confirmed")
            return True

        return False

    def is_confirmed(self) -> bool:
        """Check if user confirmed the action.

        Returns:
            True if user confirmed, False otherwise.
        """
        return self.confirmed
