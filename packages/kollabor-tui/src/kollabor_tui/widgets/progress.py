"""Progress widget for displaying operation progress."""

import time
from typing import Any, List

from kollabor_tui.design_system import T, TagBox
from kollabor_tui.key_parser import KeyPress
from kollabor_tui.visual_effects import ColorPalette

from .base_widget import BaseWidget


class ProgressWidget(BaseWidget):
    """Widget for displaying progress bar with percentage and status text.

    Supports animated progress, color gradients, and detailed status information.
    Ideal for file uploads, long-running operations, and batch processing.

    Example config:
    {
        "label": "Processing Files",
        "current": 45,
        "total": 100,
        "show_percentage": True,
        "status_text": "Processing...",
        "use_gradient": True,
        "animate": True
    }
    """

    def __init__(self, config: dict, config_path: str, config_service=None):
        """Initialize progress widget.

        Args:
            config: Widget configuration with progress data and display options.
            config_path: Dot-notation path to config value.
            config_service: ConfigService instance for reading/writing values.
        """
        super().__init__(config, config_path, config_service)

        # Progress data
        self.current = self.config.get("current", 0)
        self.total = self.config.get("total", 100)

        # Display options
        self.show_percentage = self.config.get("show_percentage", True)
        self.status_text = self.config.get("status_text", "")
        self.show_fraction = self.config.get("show_fraction", True)
        self.use_gradient = self.config.get("use_gradient", True)
        self.animate = self.config.get("animate", False)

        # Bar styling
        self.bar_width = self.config.get("bar_width", 40)
        self.bar_char_filled = self.config.get("bar_char_filled", "█")
        self.bar_char_empty = self.config.get("bar_char_empty", "░")
        self.bar_brackets = self.config.get("bar_brackets", True)

        # Animation
        self.animation_start = time.time() if self.animate else None
        self.animation_offset = 0

        # Colors
        self.colors = ColorPalette()

    def render(self) -> List[str]:
        """Render progress widget.

        Returns:
            List of strings representing widget display lines.
        """
        lines = []

        # Label line
        label = self.get_label()
        if label:
            label_color = (
                self.colors.accent_color if self.focused else self.colors.primary_color
            )
            lines.append(f"{label_color}{label}{self.colors.reset}")

        # Calculate progress
        if self.total > 0:
            percentage = (self.current / self.total) * 100
        else:
            percentage = 0

        filled_width = (
            int((self.current / self.total) * self.bar_width) if self.total > 0 else 0
        )

        # Update animation
        if self.animate and self.animation_start:
            elapsed = time.time() - self.animation_start
            self.animation_offset = int(elapsed * 10) % self.bar_width

        # Build progress bar
        bar = self._build_progress_bar(filled_width)

        # Add brackets
        if self.bar_brackets:
            bar = f"[{bar}]"
        else:
            bar = f"|{bar}|"

        # Progress bar line
        lines.append(bar)

        # Percentage and fraction
        if self.show_percentage or self.show_fraction:
            info_parts = []

            if self.show_percentage:
                percentage_str = f"{percentage:.1f}%"
                color = self._get_percentage_color(percentage)
                info_parts.append(f"{color}{percentage_str}{self.colors.reset}")

            if self.show_fraction:
                fraction_str = f"{self.current}/{self.total}"
                info_parts.append(
                    f"{self.colors.muted_color}{fraction_str}{self.colors.reset}"
                )

            if info_parts:
                lines.append(" ".join(info_parts))

        # Status text
        if self.status_text:
            lines.append(
                f"{self.colors.muted_color}{self.status_text}{self.colors.reset}"
            )

        # ETA calculation (if we have progress data)
        if self.animate and self.animation_start and self.current > 0:
            eta = self._calculate_eta()
            if eta:
                lines.append(f"{self.colors.muted_color}ETA: {eta}{self.colors.reset}")

        return lines

    def render_modern(self, width: int = 50) -> List[str]:  # type: ignore[override]
        """Render progress widget with modern design system styling.

        Uses TagBox for label and progress bar with gradient fill.

        Args:
            width: Total width of progress widget (default: 50).

        Returns:
            List containing progress widget display lines with modern styling.
        """
        lines = []
        label = self.get_label()
        tag_width = 3
        content_width = width - tag_width

        # Label line with progress icon
        if label:
            icon = " 📊 " if self.focused else "   "
            tag_bg = T().primary[0] if self.focused else T().dark[0]
            tag_fg = T().text_dark if self.focused else T().text_dim
            content_colors = T().input_bg if self.focused else T().dark[0]
            content_fg = T().text if self.focused else T().text_dim

            content = f" {label}"
            label_line = TagBox.render(
                lines=[content],
                tag_bg=tag_bg,
                tag_fg=tag_fg,
                tag_width=tag_width,
                content_colors=content_colors,
                content_fg=content_fg,
                content_width=content_width,
                tag_chars=[icon],
                use_gradient=self.focused,
            )
            lines.append(label_line)

        # Calculate progress
        if self.total > 0:
            percentage = (self.current / self.total) * 100
        else:
            percentage = 0

        filled_width = (
            int((self.current / self.total) * self.bar_width) if self.total > 0 else 0
        )

        # Update animation
        if self.animate and self.animation_start:
            elapsed = time.time() - self.animation_start
            self.animation_offset = int(elapsed * 10) % self.bar_width

        # Build progress bar with TagBox
        bar = self._build_progress_bar(filled_width)

        # Progress bar line with TagBox
        icon = " ◆ " if self.focused else " ◇ "
        tag_bg = (
            T().success[0]
            if percentage >= 75
            else (T().warning[0] if percentage >= 25 else T().error[0])
        )
        tag_fg = T().text_dark
        content_colors = (
            T().success
            if percentage >= 75
            else (T().warning if percentage >= 25 else T().error)
        )
        content_fg = T().text

        content = f" {bar}"
        bar_line = TagBox.render(
            lines=[content],
            tag_bg=tag_bg,
            tag_fg=tag_fg,
            tag_width=tag_width,
            content_colors=content_colors,
            content_fg=content_fg,
            content_width=content_width,
            tag_chars=[icon],
            use_gradient=self.use_gradient,
        )
        lines.append(bar_line)

        # Percentage and fraction
        if self.show_percentage or self.show_fraction:
            info_parts = []

            if self.show_percentage:
                percentage_str = f"{percentage:.1f}%"
                color = (
                    T().success
                    if percentage >= 75
                    else (T().warning if percentage >= 25 else T().error)
                )
                info_parts.append(f"{T().fg(color[0])}{percentage_str}{T().reset}")

            if self.show_fraction:
                fraction_str = f"{self.current}/{self.total}"
                info_parts.append(f"{T().fg(T().dim[0])}{fraction_str}{T().reset}")

            if info_parts:
                content = f"     {' '.join(info_parts)}"
                lines.append(content)

        # Status text
        if self.status_text:
            content = f"     {T().fg(T().dim[0])}{self.status_text}{T().reset}"
            lines.append(content)

        # ETA calculation (if we have progress data)
        if self.animate and self.animation_start and self.current > 0:
            eta = self._calculate_eta()
            if eta:
                content = f"     {T().fg(T().dim[0])}ETA: {eta}{T().reset}"
                lines.append(content)

        return lines

    def handle_input(self, key_press: KeyPress) -> bool:
        """Handle keyboard input (read-only widget).

        Args:
            key_press: Key press event to handle.

        Returns:
            False (widget is read-only).
        """
        # Progress widget is read-only
        return False

    def get_value(self) -> Any:
        """Get current progress value.

        Returns:
            Dictionary with current, total, and percentage.
        """
        percentage = (self.current / self.total * 100) if self.total > 0 else 0
        return {"current": self.current, "total": self.total, "percentage": percentage}

    def set_value(self, value: Any):
        """Update progress value.

        Args:
            value: New value (int, float, or dict with current/total).
        """
        if isinstance(value, dict):
            self.current = value.get("current", self.current)
            self.total = value.get("total", self.total)
        elif isinstance(value, (int, float)):
            self.current = value

        # Start animation on first update
        if self.animate and not self.animation_start:
            self.animation_start = time.time()

    def update_progress(self, current: int, total: int | None = None):
        """Update progress values.

        Args:
            current: Current progress value.
            total: Total value (optional, keeps existing if None).
        """
        self.current = current
        if total is not None:
            self.total = total

    def set_status(self, status: str):
        """Update status text.

        Args:
            status: New status message.
        """
        self.status_text = status

    def _build_progress_bar(self, filled_width: int) -> str:
        """Build progress bar string with colors.

        Args:
            filled_width: Width of filled portion.

        Returns:
            Colored progress bar string.
        """
        bar = ""

        for i in range(self.bar_width):
            if i < filled_width:
                # Filled portion
                if self.use_gradient:
                    color = self._get_gradient_color(i, filled_width)
                else:
                    color = self.colors.success_color

                char = self.bar_char_filled
                bar += f"{color}{char}{self.colors.reset}"
            else:
                # Empty portion
                bar += (
                    f"{self.colors.muted_color}{self.bar_char_empty}{self.colors.reset}"
                )

        return bar

    def _get_gradient_color(self, position: int, filled_width: int) -> str:
        """Get color for gradient effect.

        Args:
            position: Position in bar.
            filled_width: Total filled width.

        Returns:
            Color code for position.
        """
        # Create gradient from start to end of filled portion
        if filled_width == 0:
            return self.colors.muted_color

        progress = position / filled_width

        # Gradient from blue to cyan to green
        if progress < 0.5:
            return self.colors.primary_color
        elif progress < 0.8:
            return self.colors.accent_color
        else:
            return self.colors.success_color

    def _get_percentage_color(self, percentage: float) -> str:
        """Get color based on percentage.

        Args:
            percentage: Progress percentage.

        Returns:
            Color code.
        """
        if percentage < 25:
            return self.colors.error_color
        elif percentage < 50:
            return self.colors.primary_color
        elif percentage < 75:
            return self.colors.accent_color
        else:
            return self.colors.success_color

    def _calculate_eta(self) -> str | None:
        """Calculate estimated time to completion.

        Returns:
            ETA string or None if unable to calculate.
        """
        if not self.animation_start or self.current <= 0:
            return None

        elapsed = time.time() - self.animation_start
        rate = self.current / elapsed if elapsed > 0 else 0

        if rate <= 0:
            return None

        remaining = self.total - self.current
        eta_seconds = remaining / rate

        return self._format_time(eta_seconds)

    def _format_time(self, seconds: float) -> str:
        """Format time duration.

        Args:
            seconds: Time in seconds.

        Returns:
            Formatted time string.
        """
        if seconds < 60:
            return f"{int(seconds)}s"
        elif seconds < 3600:
            minutes = int(seconds / 60)
            return f"{minutes}m"
        else:
            hours = int(seconds / 3600)
            minutes = int((seconds % 3600) / 60)
            return f"{hours}h {minutes}m"

    def is_complete(self) -> bool:
        """Check if progress is complete.

        Returns:
            True if current >= total.
        """
        return float(self.current) >= float(self.total)

    def reset(self):
        """Reset progress to zero."""
        self.current = 0
        self.animation_start = time.time() if self.animate else None
