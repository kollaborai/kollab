"""Tool spinner manager for animated tool call icons."""

import time
from typing import List, Optional


class ToolSpinnerManager:
    """Manages animated spinner state for tool calls.

    Similar to ThinkingAnimationManager but for tool execution.
    """

    # Default spinner frames
    BRAILLE_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    CLASSIC_FRAMES = ["◐", "◓", "◑", "◒"]

    def __init__(
        self,
        style: str = "braille",
        custom_frames: Optional[List[str]] = None,
        speed_ms: int = 100,
    ):
        """Initialize spinner manager.

        Args:
            style: Spinner style - 'braille', 'classic', or 'custom'
            custom_frames: Custom frame list if style='custom'
            speed_ms: Milliseconds between frame advances
        """
        self.style = style
        self.speed_ms = speed_ms
        self._frame_index = 0
        self._last_advance_time = 0.0

        # Set frames based on style
        if style == "custom" and custom_frames:
            self._frames = custom_frames
        elif style == "classic":
            self._frames = self.CLASSIC_FRAMES
        else:
            self._frames = self.BRAILLE_FRAMES

    def get_current_frame(self) -> str:
        """Get current spinner frame character.

        Automatically advances frame if enough time has passed.

        Returns:
            Current spinner frame character
        """
        now = time.time() * 1000  # Convert to ms
        if now - self._last_advance_time >= self.speed_ms:
            self._frame_index = (self._frame_index + 1) % len(self._frames)
            self._last_advance_time = now
        return self._frames[self._frame_index]

    def reset(self) -> None:
        """Reset spinner to first frame."""
        self._frame_index = 0
        self._last_advance_time = 0.0

    def configure(
        self,
        style: Optional[str] = None,
        custom_frames: Optional[List[str]] = None,
        speed_ms: Optional[int] = None,
    ) -> None:
        """Reconfigure spinner settings.

        Args:
            style: New style (optional)
            custom_frames: New custom frames (optional)
            speed_ms: New speed (optional)
        """
        if speed_ms is not None:
            self.speed_ms = speed_ms
        if style is not None:
            self.style = style
            if style == "custom" and custom_frames:
                self._frames = custom_frames
            elif style == "classic":
                self._frames = self.CLASSIC_FRAMES
            else:
                self._frames = self.BRAILLE_FRAMES
        self.reset()


# Global singleton for easy access
_spinner_instance: Optional[ToolSpinnerManager] = None


def get_tool_spinner(config=None) -> ToolSpinnerManager:
    """Get or create the tool spinner manager.

    Args:
        config: Optional ConfigManager to read settings from

    Returns:
        ToolSpinnerManager instance
    """
    global _spinner_instance

    if _spinner_instance is None:
        style = "braille"
        custom_frames = None
        speed_ms = 100

        if config:
            style = config.get("terminal.tool_spinner_style", "braille")
            raw_frames = config.get("terminal.tool_spinner_frames", None)
            custom_frames = raw_frames if isinstance(raw_frames, list) else None
            speed_ms = config.get("terminal.tool_spinner_speed_ms", 100)

        _spinner_instance = ToolSpinnerManager(style, custom_frames, speed_ms)

    return _spinner_instance


def is_tool_spinner_enabled(config=None) -> bool:
    """Check if tool spinner is enabled in config.

    Args:
        config: Optional ConfigManager

    Returns:
        True if spinner enabled, False otherwise
    """
    if config:
        return bool(config.get("terminal.tool_spinner_enabled", True))
    return True
