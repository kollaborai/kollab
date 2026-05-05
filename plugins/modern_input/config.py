from dataclasses import dataclass
from typing import Optional


@dataclass
class ModernInputConfig:
    enabled: bool = True

    # Sizing
    width_mode: str = "auto"  # auto, percentage (e.g. "80%"), fixed (e.g. "60")
    min_width: int = 40
    max_width: Optional[int] = None  # None = use global width from terminal_state
    max_visible_lines: int = 3

    # Placeholder
    show_placeholder: bool = True
    placeholder: str = "Type your message..."

    # Cursor
    cursor_blink: bool = True
    cursor_blink_rate: float = 0.5  # seconds

    # Colors (use theme by default, allow overrides)
    use_theme_colors: bool = True
    custom_text_color: Optional[str] = None
    custom_placeholder_color: Optional[str] = None

    # Status
    show_status: bool = False

    @classmethod
    def from_config_manager(cls, config_manager) -> "ModernInputConfig":
        """Load config from config manager."""
        prefix = "plugins.modern_input"
        return cls(
            enabled=config_manager.get(f"{prefix}.enabled", True),
            width_mode=config_manager.get(f"{prefix}.width_mode", "auto"),
            min_width=config_manager.get(f"{prefix}.min_width", 40),
            max_width=config_manager.get(f"{prefix}.max_width", None),
            max_visible_lines=config_manager.get(f"{prefix}.max_visible_lines", 3),
            show_placeholder=config_manager.get(f"{prefix}.show_placeholder", True),
            placeholder=config_manager.get(
                f"{prefix}.placeholder", "Type your message..."
            ),
            cursor_blink=config_manager.get(f"{prefix}.cursor_blink", True),
            cursor_blink_rate=config_manager.get(f"{prefix}.cursor_blink_rate", 0.5),
            use_theme_colors=config_manager.get(f"{prefix}.use_theme_colors", True),
            show_status=config_manager.get(f"{prefix}.show_status", False),
        )
