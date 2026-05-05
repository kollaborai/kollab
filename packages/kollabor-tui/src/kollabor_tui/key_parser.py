"""Keyboard input parsing for terminal applications.

This module provides comprehensive keyboard input parsing, including
single character input, control key detection, and escape sequence
handling for terminal applications.
"""

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Optional

logger = logging.getLogger(__name__)


class KeyType(Enum):
    """Types of keyboard input."""

    PRINTABLE = "printable"
    CONTROL = "control"
    SPECIAL = "special"
    EXTENDED = "extended"


@dataclass
class KeyPress:
    """Represents a parsed key press event.

    Attributes:
        name: Human-readable key name.
        code: Character code or sequence.
        char: Printable character (if applicable).
        type: Type of key press.
        modifiers: Any modifier keys pressed.
    """

    name: str
    code: int | str
    char: Optional[str] = None
    type: KeyType = KeyType.PRINTABLE
    modifiers: Optional[Dict[str, bool]] = None

    def __post_init__(self):
        if self.modifiers is None:
            self.modifiers = {
                "ctrl": False,
                "alt": False,
                "shift": False,
                "cmd": False,
            }

    @property
    def key(self) -> str:
        """Normalized lowercase key name for widget compatibility."""
        name = self.name.lower()
        # Normalize arrow key names: "ArrowUp" -> "up"
        name = name.replace("arrow", "")
        # Normalize other common names
        name = name.replace("ctrl+", "ctrl+")
        return name

    @property
    def is_cursor_key(self) -> bool:
        """Whether this key is a cursor movement key (arrows, page up/down)."""
        return self.key in ("up", "down", "left", "right", "pageup", "pagedown")

    @property
    def ctrl(self) -> bool:
        """Whether the ctrl modifier is active."""
        return self.modifiers.get("ctrl", False) if self.modifiers else False


class KeyParser:
    """Parses terminal keyboard input into structured KeyPress events.

    Handles single character input, control characters, and extended sequences
    like arrow keys and function keys.
    """

    # Control character mappings
    CONTROL_KEYS = {
        1: "Ctrl+A",
        2: "Ctrl+B",
        3: "Ctrl+C",
        4: "Ctrl+D",
        5: "Ctrl+E",
        6: "Ctrl+F",
        7: "Ctrl+G",
        8: "Ctrl+H",
        9: "Tab",
        10: "Ctrl+J",
        11: "Ctrl+K",
        12: "Ctrl+L",
        13: "Enter",
        14: "Ctrl+N",
        15: "Ctrl+O",
        16: "Ctrl+P",
        17: "Ctrl+Q",
        18: "Ctrl+R",
        19: "Ctrl+S",
        20: "Ctrl+T",
        21: "Ctrl+U",
        22: "Ctrl+V",
        23: "Ctrl+W",
        24: "Ctrl+X",
        25: "Ctrl+Y",
        26: "Ctrl+Z",
        27: "Escape",
        127: "Backspace",
    }

    # Extended sequence mappings (ESC sequences)
    ESCAPE_SEQUENCES = {
        # Basic arrow keys
        "[A": "ArrowUp",
        "[B": "ArrowDown",
        "[C": "ArrowRight",
        "[D": "ArrowLeft",
        # Alt/Option+arrow keys (modifier 3)
        "[1;3A": "Alt+ArrowUp",
        "[1;3B": "Alt+ArrowDown",
        "[1;3C": "Alt+ArrowRight",
        "[1;3D": "Alt+ArrowLeft",
        # Ctrl+arrow keys (common terminal sequences)
        "[1;5A": "Ctrl+ArrowUp",
        "[1;5B": "Ctrl+ArrowDown",
        "[1;5C": "Ctrl+ArrowRight",
        "[1;5D": "Ctrl+ArrowLeft",
        # Alternative Ctrl+arrow sequences (some terminals)
        "O5A": "Ctrl+ArrowUp",
        "O5B": "Ctrl+ArrowDown",
        "O5C": "Ctrl+ArrowRight",
        "O5D": "Ctrl+ArrowLeft",
        # Cmd+arrow keys (macOS sequences)
        "[1;9A": "Cmd+ArrowUp",
        "[1;9B": "Cmd+ArrowDown",
        "[1;9C": "Cmd+ArrowRight",
        "[1;9D": "Cmd+ArrowLeft",
        # Shift+Tab (BackTab)
        "[Z": "Shift+Tab",
        # Other navigation keys
        "[H": "Home",
        "[F": "End",
        "[3~": "Delete",
        "[5~": "PageUp",
        "[6~": "PageDown",
        # Function keys
        "OP": "F1",
        "OQ": "F2",
        "OR": "F3",
        "OS": "F4",
        "[15~": "F5",
        "[17~": "F6",
        "[18~": "F7",
        "[19~": "F8",
        "[20~": "F9",
        "[21~": "F10",
        "[23~": "F11",
        "[24~": "F12",
        # Bracketed paste
        "[200~": "BracketedPasteStart",
        "[201~": "BracketedPasteEnd",
        # Shift+Enter sequences (terminal-specific)
        "[13;2u": "Shift+Enter",  # kitty keyboard protocol
        "[27;2;13~": "Shift+Enter",  # xterm modifyOtherKeys
        "\r": "Shift+Enter",  # ESC + carriage return (some terminals)
    }

    def __init__(self):
        """Initialize the key parser."""
        self._escape_buffer = ""
        self._in_escape_sequence = False

    def parse_char(self, char: str) -> Optional[KeyPress]:
        """Parse a single character into a KeyPress event.

        Args:
            char: Single character from terminal input.

        Returns:
            KeyPress event or None if part of incomplete sequence.
        """
        if not char:
            return None

        char_code = ord(char)

        # Handle escape sequences
        if self._in_escape_sequence:
            return self._parse_escape_sequence(char)

        # Start of escape sequence
        if char_code == 27:  # ESC
            self._in_escape_sequence = True
            self._escape_buffer = ""
            # Return None to wait for potential sequence, but we'll handle standalone ESC elsewhere
            return None

        # Control characters
        if char_code in self.CONTROL_KEYS:
            name = self.CONTROL_KEYS[char_code]
            return KeyPress(
                name=name,
                code=char_code,
                type=KeyType.CONTROL,
                modifiers=self._parse_modifiers(name),
            )

        # Printable characters
        if 32 <= char_code <= 126:
            return KeyPress(
                name=char, code=char_code, char=char, type=KeyType.PRINTABLE
            )

        # Unknown/special characters
        return KeyPress(
            name=f"Key{char_code}",
            code=char_code,
            char=char,
            type=KeyType.SPECIAL,
        )

    def _parse_escape_sequence(self, char: str) -> Optional[KeyPress]:
        """Parse characters within an escape sequence.

        Args:
            char: Character in the escape sequence.

        Returns:
            KeyPress event when sequence is complete, None otherwise.
        """
        self._escape_buffer += char
        logger.debug(
            f"Escape sequence buffer: '{self._escape_buffer}' (char: '{char}', ord: {ord(char)})"
        )

        # Check for complete sequence
        if self._escape_buffer in self.ESCAPE_SEQUENCES:
            name = self.ESCAPE_SEQUENCES[self._escape_buffer]

            # Determine key type and modifiers
            if "Ctrl+" in name or "Cmd+" in name or "Alt+" in name:
                key_type = KeyType.CONTROL
                modifiers = self._parse_modifiers(name)
            else:
                key_type = KeyType.EXTENDED
                modifiers = {
                    "ctrl": False,
                    "alt": False,
                    "shift": False,
                    "cmd": False,
                }

            key_press = KeyPress(
                name=name,
                code=f"ESC{self._escape_buffer}",
                type=key_type,
                modifiers=modifiers,
            )
            self._reset_escape_state()
            logger.debug(
                f"Escape sequence '{self._escape_buffer}' → {name} (type: {key_type})"
            )
            return key_press

        # Check for incomplete sequence that could still match
        possible_matches = [
            seq
            for seq in self.ESCAPE_SEQUENCES.keys()
            if seq.startswith(self._escape_buffer)
        ]

        if possible_matches:
            # Still building sequence
            return None

        # Check if this is Alt+key (ESC followed by a single printable char)
        # This is how many terminals send Alt+key combinations
        if len(self._escape_buffer) == 1:
            alt_char = self._escape_buffer
            char_code = ord(alt_char)
            if 32 <= char_code <= 126:  # Printable character
                logger.debug(f"Alt+key detected: Alt+{alt_char}")
                result = KeyPress(
                    name=f"Alt+{alt_char}",
                    code=f"ESC{alt_char}",
                    char=alt_char,
                    type=KeyType.CONTROL,
                    modifiers={
                        "ctrl": False,
                        "alt": True,
                        "shift": False,
                        "cmd": False,
                    },
                )
                self._reset_escape_state()
                return result

        # Invalid sequence - treat as separate keys
        logger.debug(
            f" Unknown escape sequence: '{self._escape_buffer}' - treating as ESC+{self._escape_buffer}"
        )
        result = KeyPress(
            name=f"ESC+{self._escape_buffer}",
            code=f"ESC{self._escape_buffer}",
            type=KeyType.SPECIAL,
        )
        self._reset_escape_state()
        return result

    def _parse_modifiers(self, key_name: str) -> Dict[str, bool]:
        """Parse modifier keys from key name.

        Args:
            key_name: Name of the key.

        Returns:
            Dictionary of modifier states.
        """
        modifiers = {"ctrl": False, "alt": False, "shift": False, "cmd": False}

        if "Ctrl+" in key_name:
            modifiers["ctrl"] = True
        if "Alt+" in key_name:
            modifiers["alt"] = True
        if "Shift+" in key_name:
            modifiers["shift"] = True
        if "Cmd+" in key_name:
            modifiers["cmd"] = True

        return modifiers

    def _reset_escape_state(self):
        """Reset escape sequence parsing state."""
        self._in_escape_sequence = False
        self._escape_buffer = ""

    def check_for_standalone_escape(self) -> Optional[KeyPress]:
        """Check if we have a standalone ESC key (no following sequence).

        This should be called when we detect that input has paused
        and we're in the middle of an escape sequence with empty buffer.

        Returns:
            KeyPress for standalone ESC if detected, None otherwise.
        """
        if self._in_escape_sequence and self._escape_buffer == "":
            # We have a standalone ESC key
            self._reset_escape_state()
            return KeyPress(name="Escape", code=27, type=KeyType.CONTROL)
        return None

    def is_printable_char(self, key_press: KeyPress) -> bool:
        """Check if key press represents a printable character.

        Args:
            key_press: The key press to check.

        Returns:
            True if the key press is a printable character.
        """
        return (
            key_press.type == KeyType.PRINTABLE
            and key_press.char is not None
            and 32 <= ord(key_press.char) <= 126
        )

    def is_control_key(self, key_press: KeyPress, control_name: str) -> bool:
        """Check if key press matches a specific control key.

        Args:
            key_press: The key press to check.
            control_name: Name of control key (e.g., "Enter", "Ctrl+C").

        Returns:
            True if key press matches the control key.
        """
        return key_press.type == KeyType.CONTROL and key_press.name == control_name
