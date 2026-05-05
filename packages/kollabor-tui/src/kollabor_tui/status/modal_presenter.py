"""Modal presentation for status widget navigation.

Extracted from StatusNavigationManager to keep the navigation manager
focused on keyboard routing and state management.
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ModalPresenterMixin:
    """Mixin providing modal UI operations for StatusNavigationManager.

    Methods here handle showing widget picker modals, modal results,
    inline editors, and help overlays.

    Type annotations below declare attributes provided by the host class
    (StatusNavigationManager) at runtime via multi-inheritance.
    """

    # Instance attributes provided by host class
    coordinator: Any
    renderer: Any
    state: Any
    event_bus: Any

    # Methods provided by host class or sibling mixins
    render_navigation_state: Any

    def _pause_input_handler(self) -> None:
        """Pause async stdin polling for exclusive modal input.

        Must be followed by _resume_input_handler() in a finally block.
        Chain: self.renderer (TerminalRenderer, wired by application.py)
               -> .input_handler (InputN/InputHandler)
               -> ._input_loop_manager (InputLoopManager)
        """
        try:
            ih = getattr(self.renderer, "input_handler", None)
            if ih:
                ilm = getattr(ih, "_input_loop_manager", None)
                if ilm and hasattr(ilm, "pause_stdin"):
                    ilm.pause_stdin()
                    return
            logger.debug("Could not pause input handler for modal")
        except Exception as e:
            logger.debug(f"Error pausing input handler: {e}")

    def _resume_input_handler(self) -> None:
        """Resume async stdin polling after modal completes."""
        try:
            ih = getattr(self.renderer, "input_handler", None)
            if ih:
                ilm = getattr(ih, "_input_loop_manager", None)
                if ilm and hasattr(ilm, "resume_stdin"):
                    ilm.resume_stdin()
                    return
            logger.debug("Could not resume input handler after modal")
        except Exception as e:
            logger.debug(f"Error resuming input handler: {e}")

    async def _show_widget_picker_modal_simple(
        self, widgets: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """Show a simplified modal to pick a widget (no row selection).

        Uses MessageDisplayCoordinator for proper buffer lifecycle management.

        Args:
            widgets: List of widget info dicts with id, label, description

        Returns:
            Widget dict with 'id' or None if cancelled
        """
        import os
        import re
        import select as select_module
        import sys

        from kollabor_tui.design_system import S, T, solid, solid_fg
        from kollabor_tui.terminal_state import get_terminal_size

        def strip_ansi(text):
            return re.sub(r"\033\[[0-9;]*m", "", text)

        # Use coordinator for proper render state management
        self.coordinator.enter_alternate_buffer()

        selected = None
        try:
            term_width, term_height = get_terminal_size()

            # Calculate modal dimensions
            modal_width = min(60, term_width - 4)
            modal_height = (
                len(widgets) + 5
            )  # title bar + title + widgets + footer bar + footer

            start_col = max(1, (term_width - modal_width) // 2)
            start_row = max(1, (term_height - modal_height) // 2)

            # Enter ANSI alternate buffer for clean modal display
            sys.stdout.write("\033[?1049h")  # Enter alternate screen buffer
            sys.stdout.write("\033[?25l")  # Hide cursor
            sys.stdout.flush()

            selected_idx = 0

            def render_picker(sel_idx: int) -> None:
                sys.stdout.write("\033[2J")  # Clear screen

                current_row = start_row

                # Title bar (top border)
                sys.stdout.write(f"\033[{current_row};{start_col}H")
                sys.stdout.write(solid_fg("▄" * modal_width, T().primary[0]))
                current_row += 1

                # Title
                sys.stdout.write(f"\033[{current_row};{start_col}H")
                title_line = solid(
                    f"  {S.BOLD}Add Widget{S.RESET_BOLD}",
                    T().primary[0],
                    T().text,
                    modal_width,
                )
                sys.stdout.write(title_line)
                current_row += 1

                # Widget options
                for i, w in enumerate(widgets):
                    label = w.get("label", w.get("id", ""))
                    desc = w.get("description", "")
                    desc_part = f" - {desc}" if desc else ""

                    if i == sel_idx:
                        indicator = f"> {S.BOLD}{label}{S.RESET_BOLD}"
                        fg_color = T().text
                        bg_color = T().primary[0]
                    else:
                        indicator = f"  {label}"
                        fg_color = T().text_dim
                        bg_color = T().dark[0]

                    opt_line = f"  {indicator}{desc_part}"
                    # Truncate if needed
                    max_line_width = modal_width - 4
                    visible_len = len(strip_ansi(opt_line))
                    if visible_len > max_line_width:
                        opt_line = opt_line[: max_line_width - 3] + "..."

                    sys.stdout.write(f"\033[{current_row};{start_col}H")
                    sys.stdout.write(solid(opt_line, bg_color, fg_color, modal_width))
                    current_row += 1

                # Footer bar (bottom border)
                sys.stdout.write(f"\033[{current_row};{start_col}H")
                sys.stdout.write(solid_fg("▀" * modal_width, T().primary[0]))
                current_row += 1

                # Footer text
                sys.stdout.write(f"\033[{current_row};{start_col}H")
                footer = "  ↑↓ select · enter add · esc cancel"
                sys.stdout.write(solid(footer, T().dark[0], T().text_dim, modal_width))

                sys.stdout.flush()

            # Initial render
            render_picker(selected_idx)

            # Pause async stdin reader for exclusive modal input
            self._pause_input_handler()

            # Input loop - read bytes directly for escape sequences
            fd = sys.stdin.fileno()

            def read_key():
                """Read a key, handling escape sequences."""
                char = os.read(fd, 1).decode("utf-8", errors="ignore")
                if not char:
                    return ""

                if char == "\x1b":
                    # Use 0.1s timeout to handle subprocess/SSH latency
                    readable, _, _ = select_module.select([fd], [], [], 0.1)
                    if readable:
                        char2 = os.read(fd, 1).decode("utf-8", errors="ignore")
                        if char2 == "[":
                            readable2, _, _ = select_module.select([fd], [], [], 0.1)
                            if readable2:
                                char3 = os.read(fd, 1).decode("utf-8", errors="ignore")
                                return f"ESC[{char3}"
                            return "ESC["
                        return f"ESC{char2}"
                    return "ESC"
                return char

            while True:
                key = read_key()

                if key == "ESC[A":  # Up arrow
                    selected_idx = (selected_idx - 1) % len(widgets)
                    render_picker(selected_idx)
                elif key == "ESC[B":  # Down arrow
                    selected_idx = (selected_idx + 1) % len(widgets)
                    render_picker(selected_idx)
                elif key == "ESC":  # Standalone ESC - cancel
                    break
                elif key == "\r" or key == "\n":
                    selected = widgets[selected_idx]
                    break
                elif key == "j":  # vim down
                    selected_idx = (selected_idx + 1) % len(widgets)
                    render_picker(selected_idx)
                elif key == "k":  # vim up
                    selected_idx = (selected_idx - 1) % len(widgets)
                    render_picker(selected_idx)
                elif key == "q":  # q to quit
                    break
                elif len(key) == 1 and key.isdigit():
                    idx = int(key) - 1
                    if 0 <= idx < len(widgets):
                        selected = widgets[idx]
                        break

        except Exception as e:
            logger.debug(f"Widget picker error: {e}")

        finally:
            # Resume async stdin reader (CRITICAL - must happen before return)
            self._resume_input_handler()

            # Exit ANSI alternate buffer and show cursor
            sys.stdout.write("\033[?25h")  # Show cursor
            sys.stdout.write("\033[?1049l")  # Exit alternate screen buffer
            sys.stdout.flush()

            # Reset render state via coordinator (CRITICAL for clean recovery)
            self.coordinator.exit_alternate_buffer(restore_state=True)

        return selected

    async def _show_widget_picker(self, picker: Any) -> Optional[str]:
        """Show WidgetPickerModal and return selected widget ID.

        Uses MessageDisplayCoordinator for proper buffer lifecycle management.

        Args:
            picker: WidgetPickerModal instance

        Returns:
            Widget ID if selected, None if cancelled
        """
        import os
        import select as select_module
        import sys

        # Use coordinator for proper render state management
        self.coordinator.enter_alternate_buffer()

        # Pause async stdin reader so modal sync loop has exclusive access
        self._pause_input_handler()

        try:
            # Enter ANSI alternate buffer for clean modal display
            sys.stdout.write("\033[?1049h")  # Enter alternate screen buffer
            sys.stdout.write("\033[?25l")  # Hide cursor
            sys.stdout.flush()

            # Show picker
            picker.show()

            # Create a simple keypress class that WidgetPickerModal expects
            class KeyPress:
                def __init__(self, name=None, char=None):
                    self.name = name
                    self.char = char

            fd = sys.stdin.fileno()

            def read_key():
                """Read a key, handling escape sequences."""
                char = os.read(fd, 1).decode("utf-8", errors="ignore")
                if not char:
                    return None

                if char == "\x1b":
                    # Use 0.1s timeout to handle subprocess/SSH latency
                    readable, _, _ = select_module.select([fd], [], [], 0.1)
                    if readable:
                        char2 = os.read(fd, 1).decode("utf-8", errors="ignore")
                        if char2 == "[":
                            readable2, _, _ = select_module.select([fd], [], [], 0.1)
                            if readable2:
                                char3 = os.read(fd, 1).decode("utf-8", errors="ignore")
                                # Map escape sequences
                                if char3 == "A":
                                    return KeyPress(name="Up")
                                elif char3 == "B":
                                    return KeyPress(name="Down")
                                elif char3 == "D":
                                    return KeyPress(name="Left")
                                elif char3 == "C":
                                    return KeyPress(name="Right")
                                elif char3 == "H":
                                    return KeyPress(name="Home")
                                elif char3 == "F":
                                    return KeyPress(name="End")
                                elif char3 in ("5", "6"):
                                    # Page Up/Down (may need another byte)
                                    readable3, _, _ = select_module.select(
                                        [fd], [], [], 0.05
                                    )
                                    if readable3:
                                        char4 = os.read(fd, 1).decode(
                                            "utf-8", errors="ignore"
                                        )
                                        if char3 == "5" and char4 == "~":
                                            return KeyPress(name="PageUp")
                                        elif char3 == "6" and char4 == "~":
                                            return KeyPress(name="PageDown")
                            return KeyPress(name="Unknown")
                        return KeyPress(name="Escape")
                    return KeyPress(name="Escape")
                elif char == "\r" or char == "\n":
                    return KeyPress(name="Enter")
                elif char == "\x7f":
                    return KeyPress(name="Backspace")
                elif char == "\x01":
                    return KeyPress(name="Ctrl+U")
                elif char.isprintable():
                    return KeyPress(char=char)
                return None

            # Initial render
            lines = picker.render()
            for line in lines:
                sys.stdout.write("\033[K")  # Clear line
                sys.stdout.write(line + "\n")
            sys.stdout.flush()

            # Pause async stdin reader for exclusive modal input
            self._pause_input_handler()

            # Input loop
            while picker.is_visible():
                key = read_key()
                if key is None:
                    continue

                handled = picker.handle_keypress(key)

                if handled:
                    # Re-render
                    sys.stdout.write("\033[H")  # Move cursor to top
                    lines = picker.render()
                    for line in lines:
                        sys.stdout.write("\033[K")  # Clear line
                        sys.stdout.write(line + "\n")
                    sys.stdout.flush()

                # Check if picker was closed
                if not picker.is_visible():
                    break

        except Exception as e:
            logger.error(f"Widget picker error: {e}", exc_info=True)

        finally:
            # Resume async stdin reader (CRITICAL - must happen before return)
            self._resume_input_handler()

            # Exit ANSI alternate buffer and show cursor
            sys.stdout.write("\033[?25h")  # Show cursor
            sys.stdout.write("\033[?1049l")  # Exit alternate screen buffer
            sys.stdout.flush()

            # Reset render state via coordinator (CRITICAL for clean recovery)
            self.coordinator.exit_alternate_buffer(restore_state=True)

        # Return selected widget ID
        selected: Optional[str] = picker.get_selected_widget()  # type: ignore[assignment]
        return selected

    async def _show_widget_picker_modal_with_row(
        self,
        widgets: List[Dict[str, Any]],
        title: str,
        initial_row_idx: int = 0,
        visible_rows: Optional[List[Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Show a modal to pick a widget with row selection.

        Uses MessageDisplayCoordinator for proper buffer lifecycle management.

        Args:
            widgets: List of widget info dicts with id, label, description
            title: Modal title (base title, row info appended)
            initial_row_idx: Initial target row index
            visible_rows: List of visible row objects for Tab cycling

        Returns:
            Dict with 'widget' and 'target_row' or None if cancelled
        """
        import os
        import re
        import select as select_module
        import sys

        from kollabor_tui.design_system import S, T, solid, solid_fg
        from kollabor_tui.terminal_state import get_terminal_size

        def strip_ansi(text):
            return re.sub(r"\033\[[0-9;]*m", "", text)

        # Use coordinator for proper render state management
        self.coordinator.enter_alternate_buffer()

        selected = None
        try:
            # Pause async stdin reader so modal sync loop has exclusive access
            self._pause_input_handler()

            term_width, term_height = get_terminal_size()

            # Calculate modal dimensions
            modal_width = min(60, term_width - 4)
            modal_height = (
                len(widgets) + 5
            )  # title bar + title + widgets + footer bar + footer

            start_col = max(1, (term_width - modal_width) // 2)
            start_row = max(1, (term_height - modal_height) // 2)

            # Enter ANSI alternate buffer for clean modal display
            sys.stdout.write("\033[?1049h")  # Enter alternate screen buffer
            sys.stdout.write("\033[?25l")  # Hide cursor
            sys.stdout.flush()

            selected_idx = 0
            target_row_idx = initial_row_idx

            def get_row_id(idx):
                if visible_rows and 0 <= idx < len(visible_rows):
                    return getattr(visible_rows[idx], "id", idx + 1)
                return idx + 1

            def render_picker(sel_idx: int, row_idx: int) -> None:
                sys.stdout.write("\033[2J")  # Clear screen

                current_row = start_row

                # Title bar (top border)
                sys.stdout.write(f"\033[{current_row};{start_col}H")
                sys.stdout.write(solid_fg("▄" * modal_width, T().primary[0]))
                current_row += 1

                # Title with row info
                row_id = get_row_id(row_idx)
                num_rows = len(visible_rows) if visible_rows else 1
                row_info = (
                    f" (Tab: change row {row_idx + 1}/{num_rows})"
                    if num_rows > 1
                    else ""
                )
                full_title = f"Add Widget to Row {row_id}{row_info}"

                sys.stdout.write(f"\033[{current_row};{start_col}H")
                title_line = solid(
                    f"  {S.BOLD}{full_title}{S.RESET_BOLD}",
                    T().primary[0],
                    T().text,
                    modal_width,
                )
                sys.stdout.write(title_line)
                current_row += 1

                # Widget options
                for i, w in enumerate(widgets):
                    label = w.get("label", w.get("id", ""))
                    desc = w.get("description", "")
                    desc_part = f" - {desc}" if desc else ""

                    if i == sel_idx:
                        indicator = f"> {S.BOLD}{label}{S.RESET_BOLD}"
                        fg_color = T().text
                        bg_color = T().primary[0]
                    else:
                        indicator = f"  {label}"
                        fg_color = T().text_dim
                        bg_color = T().dark[0]

                    opt_line = f"  {indicator}{desc_part}"
                    # Truncate if needed
                    max_line_width = modal_width - 4
                    visible_len = len(strip_ansi(opt_line))
                    if visible_len > max_line_width:
                        opt_line = opt_line[: max_line_width - 3] + "..."

                    sys.stdout.write(f"\033[{current_row};{start_col}H")
                    sys.stdout.write(solid(opt_line, bg_color, fg_color, modal_width))
                    current_row += 1

                # Footer bar (bottom border)
                sys.stdout.write(f"\033[{current_row};{start_col}H")
                sys.stdout.write(solid_fg("▀" * modal_width, T().primary[0]))
                current_row += 1

                # Footer text
                sys.stdout.write(f"\033[{current_row};{start_col}H")
                footer = "  ↑↓ widget · Tab row · enter select · esc cancel"
                sys.stdout.write(solid(footer, T().dark[0], T().text_dim, modal_width))

                sys.stdout.flush()

            # Initial render
            render_picker(selected_idx, target_row_idx)

            # Pause async stdin reader for exclusive modal input
            self._pause_input_handler()

            # Input loop - read bytes directly for escape sequences
            fd = sys.stdin.fileno()

            def read_key():
                """Read a key, handling escape sequences."""
                # Read one byte
                char = os.read(fd, 1).decode("utf-8", errors="ignore")
                if not char:
                    return ""

                if char == "\x1b":
                    # Check if more bytes are available (arrow keys send 3 bytes quickly)
                    # Use 0.1s timeout to handle subprocess/SSH latency
                    readable, _, _ = select_module.select([fd], [], [], 0.1)
                    if readable:
                        char2 = os.read(fd, 1).decode("utf-8", errors="ignore")
                        if char2 == "[":
                            readable2, _, _ = select_module.select([fd], [], [], 0.1)
                            if readable2:
                                char3 = os.read(fd, 1).decode("utf-8", errors="ignore")
                                return f"ESC[{char3}"
                            return "ESC["
                        return f"ESC{char2}"
                    return "ESC"
                return char

            while True:
                key = read_key()

                if key == "ESC[A":  # Up arrow
                    selected_idx = (selected_idx - 1) % len(widgets)
                    render_picker(selected_idx, target_row_idx)
                elif key == "ESC[B":  # Down arrow
                    selected_idx = (selected_idx + 1) % len(widgets)
                    render_picker(selected_idx, target_row_idx)
                elif key == "\t":  # Tab - cycle through rows
                    if visible_rows and len(visible_rows) > 1:
                        target_row_idx = (target_row_idx + 1) % len(visible_rows)
                        render_picker(selected_idx, target_row_idx)
                elif key == "ESC":  # Standalone ESC - cancel
                    break
                elif key == "\r" or key == "\n":
                    selected = {
                        "widget": widgets[selected_idx],
                        "target_row_idx": target_row_idx,
                        "target_row_id": get_row_id(target_row_idx),
                    }
                    break
                elif key == "j":  # vim down
                    selected_idx = (selected_idx + 1) % len(widgets)
                    render_picker(selected_idx, target_row_idx)
                elif key == "k":  # vim up
                    selected_idx = (selected_idx - 1) % len(widgets)
                    render_picker(selected_idx, target_row_idx)
                elif key == "q":  # q to quit
                    break
                elif len(key) == 1 and key.isdigit():
                    idx = int(key) - 1
                    if 0 <= idx < len(widgets):
                        selected = {
                            "widget": widgets[idx],
                            "target_row_idx": target_row_idx,
                            "target_row_id": get_row_id(target_row_idx),
                        }
                        break

        except Exception as e:
            logger.debug(f"Widget picker error: {e}")

        finally:
            # Resume async stdin reader (CRITICAL - must happen before return)
            self._resume_input_handler()

            # Exit ANSI alternate buffer and show cursor
            sys.stdout.write("\033[?25h")  # Show cursor
            sys.stdout.write("\033[?1049l")  # Exit alternate screen buffer
            sys.stdout.flush()

            # Reset render state via coordinator (CRITICAL for clean recovery)
            self.coordinator.exit_alternate_buffer(restore_state=True)

        return selected

    async def _show_modal_result(self, result: Any) -> None:
        """Show modal result to user using ANSI alternate buffer.

        Called from activate_widget() which manages coordinator lifecycle.
        Uses ANSI alternate screen buffer to preserve main buffer content.

        Args:
            result: Modal config dict with title, options, footer
        """
        if not result or not isinstance(result, dict):
            logger.debug(f"Invalid modal result: {result}")
            return

        title = result.get("title", "Modal")
        options = result.get("options", [])
        footer = result.get("footer", "↑↓ navigate · enter select · esc cancel")

        if not options:
            logger.warning("Modal config has no options")
            return

        import os
        import re
        import select as select_module
        import sys

        from kollabor_tui.design_system import S, T, solid, solid_fg
        from kollabor_tui.terminal_state import get_terminal_size

        # Use coordinator for proper render state management
        self.coordinator.enter_alternate_buffer()

        def strip_ansi(text):
            return re.sub(r"\033\[[0-9;]*m", "", text)

        try:
            # Pause async stdin reader so modal sync loop has exclusive access
            self._pause_input_handler()

            term_width, term_height = get_terminal_size()

            # Calculate needed width based on content
            max_modal_width = 80
            padding = 2

            content_width_needed = max(len(strip_ansi(title)), len(strip_ansi(footer)))
            for opt in options:
                label = opt.get("label", "")
                description = opt.get("description", "")
                desc_part = f" - {description}" if description else ""
                opt_text = f"  > {label}{desc_part}"
                content_width_needed = max(
                    content_width_needed, len(strip_ansi(opt_text))
                )

            # Calculate modal width with constraints
            modal_width = min(
                max_modal_width, term_width - padding * 2, content_width_needed + 4
            )
            modal_width = max(modal_width, 40)  # Minimum width

            # Calculate modal dimensions
            modal_height = 1 + len(options) + 2  # Title bar + content + footer

            # Calculate centering position
            start_col = max(0, (term_width - modal_width) // 2)
            start_row = max(0, (term_height - modal_height) // 2)

            # Enter ANSI alternate screen buffer for clean modal display
            sys.stdout.write("\033[?1049h")  # Enter alternate screen buffer
            sys.stdout.write("\033[?25l")  # Hide cursor
            sys.stdout.flush()

            selected_idx = 0
            selected = None

            def render_modal(selected_index: int) -> None:
                """Render the modal with selection indicator."""
                # Clear screen and position cursor
                sys.stdout.write("\033[2J")  # Clear entire screen
                sys.stdout.write(
                    f"\033[{start_row + 1};{start_col + 1}H"
                )  # Position cursor

                # Render title bar
                sys.stdout.write(solid_fg("▄" * modal_width, T().primary[0]) + "\n")
                title_line = solid(
                    f"  {S.BOLD}{title}{S.RESET_BOLD}",
                    T().primary[0],
                    T().text,
                    modal_width,
                )
                sys.stdout.write(title_line + "\n")

                # Render options with selection indicator
                for i, opt in enumerate(options):
                    label = opt.get("label", "")
                    description = opt.get("description", "")
                    desc_part = f" - {description}" if description else ""

                    # Selection indicator
                    if i == selected_index:
                        indicator = f"> {S.BOLD}{label}{S.RESET_BOLD}"
                        fg_color = T().text  # Bright text for selected
                        bg_color = T().primary[0]  # Primary bg for selected
                    else:
                        indicator = f"  {label}"
                        fg_color = T().text_dim
                        bg_color = T().dark[0]

                    opt_line = f"  {indicator}{desc_part}"

                    # Truncate if needed (check visible length without ANSI)
                    max_line_width = modal_width - 2
                    visible_len = len(strip_ansi(opt_line))
                    if visible_len > max_line_width:
                        excess = visible_len - max_line_width
                        opt_line = opt_line[: -excess - 3] + "..."

                    sys.stdout.write(
                        solid(opt_line, bg_color, fg_color, modal_width) + "\n"
                    )

                # Render footer
                sys.stdout.write(solid_fg("▀" * modal_width, T().primary[0]) + "\n")
                footer_text = f"  {footer}"
                # Truncate footer if needed (check visible length)
                if len(strip_ansi(footer_text)) > modal_width - 2:
                    footer_text = footer_text[: modal_width - 5] + "..."
                sys.stdout.write(
                    solid(footer_text, T().dark[0], T().text_dim, modal_width) + "\n"
                )

                sys.stdout.flush()

            # Initial render
            render_modal(selected_idx)

            # Pause async stdin reader for exclusive modal input
            self._pause_input_handler()

            # Input loop using os.read for raw input
            fd = sys.stdin.fileno()

            while True:
                char = os.read(fd, 1).decode("utf-8", errors="ignore")
                if not char:
                    continue

                # Handle escape sequences (arrow keys, esc)
                if char == "\x1b":
                    # Check if more data available (timeout 0.1s)
                    readable, _, _ = select_module.select([fd], [], [], 0.1)
                    if readable:
                        next_char = os.read(fd, 1).decode("utf-8", errors="ignore")
                        if next_char == "[":
                            # Arrow key sequence
                            readable2, _, _ = select_module.select([fd], [], [], 0.05)
                            if readable2:
                                direction = os.read(fd, 1).decode(
                                    "utf-8", errors="ignore"
                                )
                                if direction == "A":  # Up arrow
                                    selected_idx = (selected_idx - 1) % len(options)
                                    render_modal(selected_idx)
                                elif direction == "B":  # Down arrow
                                    selected_idx = (selected_idx + 1) % len(options)
                                    render_modal(selected_idx)
                        # Not an arrow sequence, ignore
                    else:
                        # No more chars, this was standalone ESC - cancel
                        logger.info("Modal cancelled (ESC)")
                        break

                # Handle Enter to confirm selection
                elif char == "\r" or char == "\n":
                    selected = options[selected_idx]
                    logger.info(
                        f"Selected: {selected.get('label')} -> action: {selected.get('action')}"
                    )
                    break

                # Handle number keys (1-9) for quick select
                elif char.isdigit():
                    idx = int(char) - 1
                    if 0 <= idx < len(options):
                        selected = options[idx]
                        logger.info(
                            f"Selected (quick): {selected.get('label')} -> action: {selected.get('action')}"
                        )
                        break

        except Exception as e:
            logger.debug(f"Modal input error: {e}")

        finally:
            # Resume async stdin reader (CRITICAL - must happen before return)
            self._resume_input_handler()

            # Exit ANSI alternate screen buffer and show cursor
            sys.stdout.write("\033[?25h")  # Show cursor
            sys.stdout.write("\033[?1049l")  # Exit alternate screen buffer
            sys.stdout.flush()

            # Reset render state via coordinator (CRITICAL for clean recovery)
            self.coordinator.exit_alternate_buffer(restore_state=True)

        # Execute selected action if any
        if selected:
            action = selected.get("action")
            if action and callable(action):
                try:
                    await action()
                except Exception as e:
                    logger.error(f"Error executing modal action: {e}", exc_info=True)

    async def _show_inline_editor(self, result: Any) -> None:
        """Show inline editor result.

        Uses InlineEditorService to provide consistent inline editing behavior.
        This method is called from navigation mode when activating inline_edit widgets.

        Args:
            result: Editor config dict from widget activation handler:
                - type: "text", "slider", or "dropdown"
                - current: Current value
                - max_length: Max text length (for text type)
                - placeholder: Placeholder text (for text type)
                - min/max/step/presets: For slider type
                - on_save: Callback function to save the result
        """
        if not result or not isinstance(result, dict):
            logger.warning(f"Invalid inline editor result: {result}")
            return

        # Import service to avoid circular dependency
        from .inline_editor_service import InlineEditorService

        # Create service and show editor
        service = InlineEditorService(self.renderer, self.state)
        await service.show_editor(result)

        # Re-render navigation state to show updated widget
        await self.render_navigation_state()

    async def _show_help_from_navigation(self) -> None:
        """Show help overlay from navigation mode (F1 or ? key).

        Emits a SHOW_HELP_OVERLAY event that can be handled by the application
        to display keyboard shortcuts help.
        """
        try:
            logger.info("Showing help overlay from navigation mode")
            from kollabor_events import EventType

            result = await self.event_bus.emit_with_hooks(
                EventType.SHOW_HELP_OVERLAY,
                {"source": "navigation_manager"},
                "navigation",
            )
            logger.info(f"Help overlay event emitted: {result}")
        except Exception as e:
            logger.error(f"Error showing help overlay: {e}")
