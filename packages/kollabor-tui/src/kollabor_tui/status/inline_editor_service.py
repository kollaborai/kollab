"""Inline editor service for interactive status widget editing.

This service provides a centralized implementation for showing inline editors
(slider, text, dropdown) directly in the status area. It handles editor
instantiation, keyboard input, and callback execution.

Architecture:
    - InlineEditorService: Main service class
    - Used by: WidgetInteractionHandler, StatusNavigationManager
    - Editors: InlineSliderEditor, InlineTextEditor, InlineDropdownEditor

Example:
    service = InlineEditorService(renderer, navigation_state)
    result = await service.show_editor({
        "type": "slider",
        "current": 0.7,
        "min": 0.0,
        "max": 2.0,
        "step": 0.1,
        "presets": [0.1, 0.5, 0.7, 1.0],
        "on_save": callback,
    })
"""

import logging
import sys
from typing import Any, Dict, Optional

from kollabor_tui.key_parser import KeyParser, KeyPress, KeyType

from .inline_editors import (
    BaseInlineEditor,
    InlineDropdownEditor,
    InlineSliderEditor,
    InlineTextEditor,
)

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    import msvcrt
else:
    import termios
    import tty

logger = logging.getLogger(__name__)


class InlineEditorService:
    """Service for showing inline editors in status widgets.

    This service provides a centralized implementation for inline editing,
    used by both WidgetInteractionHandler and StatusNavigationManager.

    The service handles:
    - Editor instantiation based on type
    - Keyboard input loop with raw mode
    - State updates and re-rendering
    - Callback execution (on_save, on_change, on_select)

    Attributes:
        renderer: TerminalRenderer instance for re-rendering
        navigation_state: StatusNavigationState for state management
        key_parser: KeyParser for input handling
    """

    def __init__(self, renderer, navigation_state):
        """Initialize the inline editor service.

        Args:
            renderer: TerminalRenderer instance
            navigation_state: StatusNavigationState instance for state updates
        """
        self.renderer = renderer
        self.navigation_state = navigation_state
        self.key_parser: KeyParser = KeyParser()
        logger.debug("InlineEditorService initialized")

    async def show_editor(self, edit_config: Dict[str, Any]) -> Optional[Any]:
        """Show inline editor and return edited value.

        This method:
        1. Creates the appropriate editor based on type
        2. Sets navigation state to inline edit mode
        3. Runs keyboard input loop
        4. Updates editor output on each keypress
        5. Executes callbacks on confirmation
        6. Returns final value or None if cancelled

        Args:
            edit_config: Editor configuration dict with keys:
                - type: "slider", "text", or "dropdown"
                - current: Current value
                - For slider: min, max, step, presets
                - For text: placeholder, max_length
                - For dropdown: options, selected (index)
                - on_save: Callback when confirmed (optional)
                - on_change: Callback on each change (optional)
                - on_select: Callback for dropdown selection (optional)
                - config_key: Config key for auto-save (optional)

        Returns:
            Final edited value if confirmed, None if cancelled
        """
        if not edit_config or not isinstance(edit_config, dict):
            logger.warning(f"Invalid inline editor config: {edit_config}")
            return None

        editor_type = edit_config.get("type", "text")

        # Create appropriate editor
        editor = self._create_editor(edit_config)
        if not editor:
            logger.warning(f"Failed to create editor of type: {editor_type}")
            return None

        # Get widget ID for state tracking
        widget_id = self.navigation_state.get_active_widget_id()

        # Set inline edit state and trigger initial render
        editor_output = editor.render()
        await self.navigation_state.set_inline_edit_state(
            widget_id=widget_id, editor=editor, editor_output=editor_output
        )
        await self.renderer.render_active_area()

        # Save terminal settings for raw mode
        old_settings = None
        if not IS_WINDOWS:
            try:
                old_settings = termios.tcgetattr(sys.stdin.fileno())
                tty.setraw(sys.stdin.fileno())
            except Exception:
                pass  # Already in raw mode from navigation

        # Input loop
        confirmed = False
        try:
            while True:
                # Read and process input
                if IS_WINDOWS:
                    char = msvcrt.getwch()  # type: ignore[attr-defined]
                else:
                    char = sys.stdin.read(1)

                # Handle escape sequences
                key_press = await self._parse_keypress(char)
                if not key_press:
                    continue

                # Handle the key press
                handled = editor.handle_keypress(key_press)

                # Check for completion
                if editor.is_confirmed():
                    confirmed = True
                    logger.info(f"Editor confirmed: {editor.get_value()}")
                    break
                elif editor.is_cancelled():
                    logger.info("Editor cancelled")
                    break

                # Update state and re-render if handled
                if handled:
                    # Call on_change callback if provided
                    on_change = edit_config.get("on_change")
                    if on_change and callable(on_change):
                        try:
                            await on_change(editor.get_value())
                        except Exception as e:
                            logger.error(f"Error in on_change callback: {e}")

                    # Call on_select callback for dropdown
                    on_select = edit_config.get("on_select")
                    if on_select and callable(on_select):
                        try:
                            await on_select(editor.get_value())
                        except Exception as e:
                            logger.error(f"Error in on_select callback: {e}")

                    # Update editor output
                    editor_output = editor.render()
                    await self.navigation_state.update_inline_editor_output(
                        editor_output
                    )
                    await self.renderer.render_active_area()

        except Exception as e:
            logger.error(f"Inline editor input error: {e}", exc_info=True)

        finally:
            # Clear inline edit state
            await self.navigation_state.set_inline_edit_state()

            # Deactivate widget interaction to allow re-activation
            await self.navigation_state.deactivate_widget()

            # Restore terminal settings if we saved them (Unix only)
            if not IS_WINDOWS and old_settings is not None:
                try:
                    termios.tcsetattr(
                        sys.stdin.fileno(), termios.TCSADRAIN, old_settings
                    )
                except Exception as e:
                    logger.warning(f"Failed to restore terminal settings: {e}")

        # Execute save callback if confirmed
        if confirmed:
            final_value = editor.get_value()
            await self._execute_save_callback(final_value, edit_config)
            return final_value

        return None

    def _create_editor(self, config: Dict[str, Any]) -> Optional[BaseInlineEditor]:
        """Create editor instance based on config type.

        Args:
            config: Editor configuration dict

        Returns:
            Editor instance or None if type is unknown
        """
        editor_type = config.get("type", "text")

        if editor_type == "text":
            return self._create_text_editor(config)
        elif editor_type == "slider":
            return self._create_slider_editor(config)
        elif editor_type == "dropdown":
            return self._create_dropdown_editor(config)
        else:
            logger.warning(f"Unknown editor type: {editor_type}")
            return None

    def _create_text_editor(self, config: Dict[str, Any]) -> InlineTextEditor:
        """Create text editor from config.

        Args:
            config: Configuration with current, placeholder, max_length

        Returns:
            InlineTextEditor instance
        """
        current_value = config.get("current", "")
        placeholder = config.get("placeholder", "")

        editor = InlineTextEditor(
            value=current_value,
            placeholder=placeholder,
            width=40,
        )
        logger.info(
            f"Created text editor: value='{current_value}', placeholder='{placeholder}'"
        )
        return editor

    def _create_slider_editor(self, config: Dict[str, Any]) -> InlineSliderEditor:
        """Create slider editor from config.

        Args:
            config: Configuration with current, min, max, step, presets

        Returns:
            InlineSliderEditor instance
        """
        current = config.get("current", 0.0)
        min_val = config.get("min", 0.0)
        max_val = config.get("max", 100.0)
        step = config.get("step", 1.0)
        presets = config.get("presets", [])

        editor = InlineSliderEditor(
            value=current,
            min_val=min_val,
            max_val=max_val,
            step=step,
            presets=presets,
            width=35,
        )
        logger.info(
            f"Created slider editor: value={current}, range=[{min_val}, {max_val}]"
        )
        return editor

    def _create_dropdown_editor(self, config: Dict[str, Any]) -> InlineDropdownEditor:
        """Create dropdown editor from config.

        Args:
            config: Configuration with options, selected

        Returns:
            InlineDropdownEditor instance
        """
        options = config.get("options", [])
        selected = config.get("selected", 0)

        editor = InlineDropdownEditor(
            options=options,
            selected_index=selected,
            width=30,
        )
        logger.info(f"Created dropdown editor: {len(options)} options")
        return editor

    async def _parse_keypress(self, char: str) -> Optional[KeyPress]:
        """Parse keypress character into KeyPress object.

        Handles escape sequences and regular characters.

        Args:
            char: First character read from stdin

        Returns:
            KeyPress object or None if parsing failed
        """
        # Handle escape sequences
        if char == "\x1b" or char == "\x00" or char == "\xe0":
            try:
                if IS_WINDOWS:
                    # On Windows, msvcrt returns \x00 or \xe0 prefix for
                    # special keys, followed by a scancode
                    if char in ("\x00", "\xe0"):
                        scan = msvcrt.getwch()  # type: ignore[attr-defined]
                        # Map Windows scancodes to arrow key names
                        scan_map = {
                            "H": KeyPress(name="Up", code=0, type=KeyType.SPECIAL),
                            "P": KeyPress(name="Down", code=0, type=KeyType.SPECIAL),
                            "K": KeyPress(name="Left", code=0, type=KeyType.SPECIAL),
                            "M": KeyPress(name="Right", code=0, type=KeyType.SPECIAL),
                        }
                        return scan_map.get(scan)
                    else:
                        # \x1b on Windows - standalone Escape
                        return KeyPress(name="Escape", code=27, type=KeyType.CONTROL)
                else:
                    import select

                    ready, _, _ = select.select([sys.stdin], [], [], 0.1)
                    if ready:
                        # Read the sequence character by character
                        buffer = ""
                        while True:
                            try:
                                c = sys.stdin.read(1)
                                buffer += c
                                # Check if we have a complete sequence
                                key_press = self.key_parser.parse_char("\x1b")
                                if key_press:
                                    # Feed remaining chars to parser
                                    for bc in buffer:
                                        kp = self.key_parser.parse_char(bc)
                                        if kp:
                                            key_press = kp
                                            break
                                    break
                            except Exception:
                                break
                        else:
                            # Timeout - standalone ESC
                            key_press = KeyPress(
                                name="Escape", code=27, type=KeyType.CONTROL
                            )
                        return key_press
                    else:
                        # Standalone ESC - cancel
                        return KeyPress(name="Escape", code=27, type=KeyType.CONTROL)
            except Exception as e:
                logger.debug(f"Escape sequence error: {e}")
                return KeyPress(name="Escape", code=27, type=KeyType.CONTROL)
        else:
            # Regular character - parse it
            return self.key_parser.parse_char(char)

    async def _execute_save_callback(self, value: Any, config: Dict[str, Any]) -> None:
        """Execute save callback and config update.

        Args:
            value: Final edited value
            config: Editor configuration with callbacks
        """
        # Call on_save callback if provided
        on_save = config.get("on_save")
        if on_save and callable(on_save):
            try:
                logger.info(f"Executing on_save callback with value: {value}")
                await on_save(value)
            except Exception as e:
                logger.error(f"Error in on_save callback: {e}", exc_info=True)

        # Save to config if config_key provided
        config_key = config.get("config_key")
        if config_key:
            # Get config from navigation state
            # (State should have access to config through widget context)
            try:
                # Note: Config access needs to be provided through context
                # This is a placeholder - actual implementation depends on
                # how config is passed through the widget context
                logger.info(f"Would save to config: {config_key} = {value}")
            except Exception as e:
                logger.error(f"Error saving to config: {e}", exc_info=True)


__all__ = ["InlineEditorService"]
