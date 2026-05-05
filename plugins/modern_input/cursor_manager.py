import time


class CursorManager:
    def __init__(self, blink_rate: float = 0.5):
        self.blink_rate = blink_rate
        self._last_blink = time.time()
        self._cursor_visible = True

    def update(self) -> None:
        """Update cursor blink state."""
        now = time.time()
        if now - self._last_blink >= self.blink_rate:
            self._cursor_visible = not self._cursor_visible
            self._last_blink = now

    def get_cursor_char(self, is_active: bool) -> str:
        """Get cursor character based on state."""
        if not is_active:
            return ""
        return "█" if self._cursor_visible else " "

    def reset(self) -> None:
        """Reset cursor to visible state."""
        self._cursor_visible = True
        self._last_blink = time.time()
