"""Modern Input Plugin for Kollab.

Provides customizable input rendering using the design system.
Supports multi-line input with scroll indicators.
"""

import logging
from typing import Any, Dict

from kollabor_events import Event, EventType, Hook, HookPriority
from kollabor_tui.terminal_state import get_global_width
from plugins.modern_input.config import ModernInputConfig
from plugins.modern_input.cursor_manager import CursorManager
from plugins.modern_input.renderer import ModernInputRenderer

logger = logging.getLogger(__name__)


class ModernInputPlugin:
    """Plugin that renders input box using design system with customization."""

    @staticmethod
    def get_default_config() -> Dict[str, Any]:
        """Get default configuration."""
        return {
            "plugins": {
                "modern_input": {
                    "enabled": True,
                    "width_mode": "auto",
                    "min_width": 40,
                    # Defaults to global width (matches thinking box and messages)
                    "max_width": None,
                    "max_visible_lines": 3,
                    "show_placeholder": True,
                    "placeholder": "Type your message...",
                    "cursor_blink": True,
                    "cursor_blink_rate": 0.5,
                    "use_theme_colors": True,
                    "custom_text_color": None,
                    "custom_placeholder_color": None,
                    "show_status": False,
                }
            }
        }

    @staticmethod
    def get_config_widgets() -> Dict[str, Any]:
        return {
            "title": "Modern Input",
            "widgets": [
                {
                    "type": "checkbox",
                    "label": "Enabled",
                    "config_path": "plugins.modern_input.enabled",
                    "help": "Enable the modern input plugin",
                },
                {
                    "type": "dropdown",
                    "label": "Width Mode",
                    "config_path": "plugins.modern_input.width_mode",
                    "options": ["auto", "80%", "90%", "100%"],
                    "help": "How input box width is calculated",
                },
                {
                    "type": "slider",
                    "label": "Min Width",
                    "config_path": "plugins.modern_input.min_width",
                    "min_value": 20,
                    "max_value": 120,
                    "step": 5,
                    "help": "Minimum input box width in characters",
                },
                {
                    "type": "slider",
                    "label": "Max Visible Lines",
                    "config_path": "plugins.modern_input.max_visible_lines",
                    "min_value": 1,
                    "max_value": 10,
                    "step": 1,
                    "help": "Maximum visible input lines before scrolling",
                },
                {
                    "type": "checkbox",
                    "label": "Show Placeholder",
                    "config_path": "plugins.modern_input.show_placeholder",
                    "help": "Show placeholder text when input is empty",
                },
                {
                    "type": "checkbox",
                    "label": "Cursor Blink",
                    "config_path": "plugins.modern_input.cursor_blink",
                    "help": "Enable cursor blinking animation",
                },
                {
                    "type": "slider",
                    "label": "Cursor Blink Rate",
                    "config_path": "plugins.modern_input.cursor_blink_rate",
                    "min_value": 0.1,
                    "max_value": 2.0,
                    "step": 0.1,
                    "help": "Cursor blink rate in seconds",
                },
            ],
        }

    def __init__(
        self,
        name: str,
        event_bus: "EventBus",
        renderer: "TerminalRenderer",
        config: "ConfigService",
    ) -> None:
        self.name = name
        self.event_bus = event_bus
        self.renderer = renderer
        self.config_service = config

        self.config = ModernInputConfig.from_config_manager(config.config_manager)
        self.cursor_manager = CursorManager(self.config.cursor_blink_rate)
        self.input_renderer = ModernInputRenderer(self.config)

        self.hooks = [
            Hook(
                name="render_modern_input",
                plugin_name=self.name,
                event_type=EventType.INPUT_RENDER,
                priority=HookPriority.DISPLAY.value,
                callback=self._render_input,
            )
        ]

    async def _render_input(self, data: Dict[str, Any], event: Event) -> Dict[str, Any]:
        """Render the input box."""
        if not self.config.enabled:
            return {"status": "disabled"}

        # Get state from renderer
        buffer = getattr(self.renderer, "input_buffer", "")
        cursor_pos = getattr(self.renderer, "cursor_position", len(buffer))
        is_thinking = getattr(self.renderer, "thinking_active", False)

        # Update cursor blink
        if self.config.cursor_blink:
            self.cursor_manager.update()

        cursor_char = self.cursor_manager.get_cursor_char(not is_thinking)

        # Calculate width
        terminal_width, _ = self.renderer.terminal_state.get_size()
        width = self._calculate_width(terminal_width)

        # Render
        lines = self.input_renderer.render(
            buffer_content=buffer,
            cursor_position=cursor_pos,
            cursor_char=cursor_char,
            width=width,
            is_thinking=is_thinking,
        )

        return {
            "status": "rendered",
            "fancy_input_lines": lines,
            "lines": len(lines),
        }

    def _calculate_width(self, terminal_width: int) -> int:
        """Calculate box width based on config."""
        mode = self.config.width_mode

        # Get effective max_width (use global width if not specified)
        max_width = (
            self.config.max_width
            if self.config.max_width is not None
            else get_global_width()
        )

        if mode == "auto":
            width = min(terminal_width, max_width)
        elif mode.endswith("%"):
            pct = int(mode[:-1]) / 100
            width = int(terminal_width * pct)
        else:
            try:
                width = int(mode)
            except ValueError:
                width = max_width

        return max(self.config.min_width, min(width, max_width))

    async def initialize(self) -> None:
        """Initialize plugin."""
        self.config_service.register_reload_callback(self._reload_config)
        logger.info("Modern input plugin initialized")

    def _reload_config(self) -> None:
        """Reload configuration."""
        self.config = ModernInputConfig.from_config_manager(
            self.config_service.config_manager
        )
        self.cursor_manager = CursorManager(self.config.cursor_blink_rate)
        self.input_renderer = ModernInputRenderer(self.config)
        logger.info("Modern input config reloaded")

    async def register_hooks(self) -> None:
        """Register hooks."""
        for hook in self.hooks:
            await self.event_bus.register_hook(hook)

    async def shutdown(self) -> None:
        """Shutdown plugin."""
        pass
