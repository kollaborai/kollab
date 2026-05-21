"""Terminal rendering system for Kollab.

This module provides comprehensive terminal rendering for the Kollab
application, including visual effects, layout management, message
display, and terminal state management.
"""

import logging
import math
import time
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, List, Optional

from kollabor_tui.design_system import (
    C,
    T,
    solid,
    solid_fg,
    wrap_text,
)
from kollabor_tui.message_renderer import MessageRenderer
from kollabor_tui.render_layout import LayoutManager, ThinkingAnimationManager
from kollabor_tui.terminal_state import (
    TerminalState,
    get_global_width,
    set_global_terminal_state,
)
from kollabor_tui.visual_effects import VisualEffects, set_terminal_state

if TYPE_CHECKING:
    from kollabor_config.manager import ConfigManager
    from kollabor_tui.input_handler import InputHandler

logger = logging.getLogger(__name__)


@dataclass
class InputData:
    buffer: str
    cursor_pos: int
    is_shell_command: bool
    lines: list[str]
    prompt: str
    continuation_prompt: str


@dataclass
class CursorInfo:
    cursor_line: int
    cursor_pos_in_line: int


@dataclass
class VisibleInfo:
    lines: list[str]
    start: int
    has_above: bool
    has_below: bool


class TerminalRenderer:
    """Advanced terminal renderer with modular architecture.

    Features:
    - Modular visual effects system
    - Advanced layout management
    - Comprehensive status rendering
    - Message formatting and display
    - Terminal state management
    """

    def __init__(
        self, event_bus=None, config: Optional["ConfigManager"] = None
    ) -> None:
        """Initialize the terminal renderer with modern architecture."""
        self.event_bus = event_bus
        self._app_config: Optional["ConfigManager"] = (
            config  # Store config for render cache settings
        )
        # Dynamic attributes set by application at runtime
        self.simple_mode: bool = False
        self.pipe_mode: bool = False
        self.render_loop: Optional[Any] = None
        self.input_handler: Optional["InputHandler"] = (
            None  # Will be set externally if needed
        )

        # Initialize core components
        # Pass config to TerminalState for global_width configuration
        self.terminal_state = TerminalState(config=self._app_config)
        self.visual_effects = VisualEffects()

        # Inject terminal state for color detection (single source of truth)
        set_terminal_state(self.terminal_state)

        # Set as global terminal state for universal access
        set_global_terminal_state(self.terminal_state)

        self.layout_manager = LayoutManager()
        self.layout_renderer: Optional[Any] = (
            None  # Widget-based status renderer (set by application)
        )
        self.message_renderer = MessageRenderer(
            self.terminal_state, self.visual_effects
        )

        # Initialize thinking animation manager
        self.thinking_animation = ThinkingAnimationManager()

        # Initialize message display coordinator for unified message handling
        from kollabor_tui.message_coordinator import MessageDisplayCoordinator

        self.message_coordinator = MessageDisplayCoordinator(self)

        # Interface properties
        self.input_buffer = ""
        self.cursor_position = 0
        self.thinking_active = False

        # Cursor blink animation
        self._cursor_blink_rate = 0.2  # seconds
        self._cursor_last_blink = time.time()
        self._cursor_visible = True

        # Background shimmer animation
        self._shimmer_speed = 0.5  # cycles per second (slower)
        self._shimmer_intensity = 0.25  # how much to vary brightness (0-1)
        self._last_activity = time.time()
        self._idle_timeout = 10.0  # seconds before sleeping shimmer

        # Input foreground color is chosen adaptively from the base input
        # background and held stable during shimmer animation so the text
        # does not flicker between light/dark as the background pulses.
        self._cached_input_fg: tuple[int, int, int] | None = None

        # State management
        self.writing_messages = False
        self.input_line_written = False
        self.last_line_count = 0

        # Tool execution state for spinner animation
        self._tool_executing = False
        self._tool_name = ""

        # Spinner frames for different states
        self._thinking_frames = [
            "⠋",
            "⠙",
            "⠹",
            "⠸",
            "⠼",
            "⠴",
            "⠦",
            "⠧",
            "⠇",
            "⠏",
        ]  # Extended braille
        self._tool_frames = ["⣾", "⣽", "⣻", "⢿", "⡿", "⢽", "⡼", "⣪"]  # Blocks
        self._waiting_frames = ["▉", "▊", "▋", "▌", "▍", "▎", "▏"]  # Shades
        self._current_spinner_frame = 0

        # Render optimization: cache to prevent unnecessary writes
        self._last_render_content: List[str] = []  # Cache of last rendered content
        self._render_cache_enabled = True  # Enable/disable render caching
        self._resize_redraw_pending = False
        self._render_generation = 0

        # Configuration (will be updated by config methods)
        self.thinking_effect = "shimmer"
        self.use_modern_ui = True  # Toggle for new vs legacy rendering

        logger.info("Advanced terminal renderer initialized")

    def wake_shimmer(self) -> None:
        """Wake up the shimmer animation on user activity."""
        self._last_activity = time.time()

    def enter_raw_mode(self) -> None:
        """Enter raw terminal mode for character-by-character input."""
        success = self.terminal_state.enter_raw_mode()
        if not success:
            logger.debug("Failed to enter raw mode")

    def exit_raw_mode(self) -> None:
        """Exit raw terminal mode and restore settings."""
        success = self.terminal_state.exit_raw_mode()
        if not success:
            logger.warning("Failed to exit raw mode")

    def create_kollabor_banner(
        self, version: str = "v1.0.0", context: dict | None = None
    ) -> str:
        """Create Kollab banner with optional context info.

        Args:
            version: Version string to display.
            context: Optional dict with agent, model, profile, skills, directory.

        Returns:
            Formatted banner string.
        """
        return self.visual_effects.create_banner(version, context=context)

    def write_hook_message(self, content: str, **metadata) -> None:
        """Write a hook message using coordinated display.

        Args:
            content: Hook message content.
            **metadata: Additional metadata.
        """
        # Route hook messages through the coordinator to prevent conflicts
        self.message_coordinator.display_message_sequence(
            [("system", content, metadata)]
        )
        logger.debug(f"Wrote hook message: {content[:50]}...")

    def update_thinking(self, active: bool, message: str = "") -> None:
        """Update the thinking animation state.

        Args:
            active: Whether thinking animation should be active.
            message: Optional thinking message to display.
        """
        self.thinking_active = active

        if active and message:
            self.thinking_animation.start_thinking(message)
            logger.debug(f"Started thinking: {message}")
        elif not active:
            completion_msg = self.thinking_animation.stop_thinking()
            if completion_msg:
                logger.info(completion_msg)

        # Publish to DisplayTap for live attach streaming
        tap = getattr(self, "message_coordinator", None)
        if tap and hasattr(tap, "_display_tap") and tap._display_tap is not None:
            tap._display_tap.publish(
                {
                    "type": "thinking",
                    "active": active,
                    "message": message,
                }
            )

    def set_thinking_effect(self, effect: str) -> None:
        """Set the thinking text effect.

        Args:
            effect: Effect type - "dim", "shimmer", "pulse", "scramble", or "none"
        """
        if effect in ["dim", "shimmer", "pulse", "scramble", "none", "normal"]:
            self.thinking_effect = effect
            self.visual_effects.configure_effect("thinking", enabled=True)
            logger.debug(f"Set thinking effect to: {effect}")
        else:
            logger.warning(f"Invalid thinking effect: {effect}")

    def configure_shimmer(self, speed: int, wave_width: int) -> None:
        """Configure shimmer effect parameters.

        Args:
            speed: Number of frames between shimmer updates
            wave_width: Number of characters in the shimmer wave
        """
        self.visual_effects.configure_effect("thinking", speed=speed, width=wave_width)
        logger.debug(f"Configured shimmer: speed={speed}, wave_width={wave_width}")

    def configure_thinking_limit(self, limit: int) -> None:
        """Configure the thinking message limit.

        Args:
            limit: Maximum number of thinking messages to keep
        """
        self.thinking_animation.messages = deque(maxlen=limit)
        logger.debug(f"Configured thinking message limit: {limit}")

    def set_tool_executing(self, active: bool, tool_name: str = "") -> None:
        """Set tool execution state for spinner animation.

        When a tool is executing, the spinner will animate in the thinking pane.

        Args:
            active: Whether a tool is currently executing
            tool_name: Name of the tool being executed
        """
        self._tool_executing = active
        self._tool_name = tool_name
        if active:
            logger.debug(f"Tool executing: {tool_name}")
        else:
            logger.debug("Tool execution complete")

    def _get_next_spinner_frame(self) -> str:
        """Get next spinner frame based on current state.

        Returns:
            Spinner frame character for current state.
        """
        # Select frames based on state
        if self._tool_executing:
            frames = self._tool_frames
        elif self.thinking_active:
            frames = self._thinking_frames
        else:
            frames = self._waiting_frames

        # Clamp index — frame sets have different lengths and state
        # transitions can leave _current_spinner_frame out of bounds
        idx = self._current_spinner_frame % len(frames) if frames else 0
        frame = frames[idx]
        self._current_spinner_frame = (idx + 1) % len(frames)
        return frame

    async def render_active_area(self) -> None:
        """Render the active input/status area using modern components.

        This method renders dynamic interface parts:
        thinking animation, input prompt, and status lines.
        """
        # Skip rendering if in alternate buffer (modal/fullscreen views)
        if self.message_coordinator and self.message_coordinator._in_alternate_buffer:
            return

        if self._should_skip_render_modal():
            return

        if self.writing_messages and not await self._has_custom_input():
            return

        if self.terminal_state.is_resize_in_progress():
            self._resize_redraw_pending = True
            self.invalidate_render_cache()
            return

        terminal_width, terminal_height, size_changed = self._check_resize()
        self._update_layout_sizes(terminal_width, terminal_height)

        lines = await self._build_active_lines()
        await self._render_lines(lines, size_changed=size_changed)

    def _should_skip_render_modal(self) -> bool:
        """Check if rendering should be skipped due to active modal."""
        if not (hasattr(self, "input_handler") and self.input_handler):
            return False

        try:
            from kollabor_events.models import CommandMode

            if self.input_handler.command_mode == CommandMode.MODAL:
                return True
        except Exception as e:
            logger.error(f"Error checking modal state: {e}")
        return False

    async def _has_custom_input(self) -> bool:
        """Check if any plugin provides custom input (e.g., command menu)."""
        if not self.event_bus:
            return False

        try:
            from kollabor_events import EventType

            result = await self.event_bus.emit_with_hooks(
                EventType.INPUT_RENDER,
                {"input_buffer": self.input_buffer},
                "renderer",
            )
            if "main" in result:
                for hook_result in result["main"].values():
                    if (
                        isinstance(hook_result, dict)
                        and "fancy_input_lines" in hook_result
                    ):
                        return True
        except Exception as e:
            logger.warning(f"Hook execution failed in render_input_line: {e}")
        return False

    def _check_resize(self) -> tuple[int, int, bool]:
        """Check for terminal resize and return (width, height, size_changed).

        Returns:
            Tuple of (terminal_width, terminal_height, size_changed_flag)
        """
        old_size = self.terminal_state.get_size()
        resize_settled = self.terminal_state.check_and_clear_resize_flag()
        size_changed = False

        if resize_settled:
            self.terminal_state.update_size()
            terminal_width, terminal_height = self.terminal_state.get_size()

            if old_size[0] > 0 and terminal_width < old_size[0]:
                width_reduction = (old_size[0] - terminal_width) / old_size[0]
            else:
                width_reduction = 0.0

            if old_size != (terminal_width, terminal_height):
                self.invalidate_render_cache()
                self._resize_redraw_pending = True
                logger.debug("Terminal size changed - cache invalidated")

            if terminal_width < old_size[0] and width_reduction >= 0.1:
                size_changed = True
                logger.debug(
                    f"Terminal width reduced by {width_reduction*100:.1f}% - aggressive clearing"
                )
        else:
            terminal_width, terminal_height = old_size

        return terminal_width, terminal_height, size_changed

    def _update_layout_sizes(self, width: int, height: int) -> None:
        """Update layout managers with new terminal size."""
        self.layout_manager.set_terminal_size(width, height)
        if self.layout_renderer:
            self.layout_renderer.set_terminal_width(width)

    async def _build_active_lines(self) -> list[str]:
        """Build all lines for the active area (thinking, input, status)."""
        lines = []

        # Permission prompt or thinking animation
        if self.layout_manager.has_active_permission_prompt():
            thinking_area = self.layout_manager.get_area("thinking")
            if thinking_area and thinking_area.content:
                lines.extend(thinking_area.content)
        elif self.thinking_active or self._tool_executing:
            lines.extend(self._build_thinking_lines())

        # Input area
        await self._render_input_area(lines)

        # Status area (command menu, status modal, or status views)
        lines.extend(await self._build_status_lines())

        return lines

    def _build_thinking_lines(self) -> list[str]:
        """Build thinking animation lines."""

        def apply_effect(text):
            return self.visual_effects.apply_thinking_effect(text, self.thinking_effect)

        spinner_frame = self._get_next_spinner_frame()
        global_width = get_global_width()

        return self.thinking_animation.get_display_lines_modern(
            width=global_width,
            apply_effect_func=apply_effect,
            tool_executing=self._tool_executing,
            tool_name=self._tool_name,
            tool_spinner=spinner_frame,
            simple_mode=getattr(self, "simple_mode", False),
        )

    async def _build_status_lines(self) -> list[str]:
        """Build status area lines (command menu, status modal, or status views)."""
        command_menu_lines = await self._get_command_menu_lines()
        if command_menu_lines:
            return command_menu_lines

        status_modal_lines = await self._get_status_modal_lines()
        if status_modal_lines:
            return status_modal_lines

        if self.layout_renderer:
            return self.layout_renderer.render()  # type: ignore[no-any-return]

        return []

    async def _render_input_area(self, lines: List[str]) -> None:
        """Render the input area, checking for plugin overrides.

        Args:
            lines: List of lines to append input rendering to.
        """
        # CRITICAL: Skip input box rendering when navigation mode is active
        # Per spec v1.2: Input box should be HIDDEN (not just blurred) when in navigation mode
        if self.message_coordinator and self.message_coordinator.navigation_active:
            logger.debug("Navigation mode active - skipping input box rendering")
            return

        # Try to get enhanced input from plugins
        if self.event_bus:
            try:
                from kollabor_events import EventType

                result = await self.event_bus.emit_with_hooks(
                    EventType.INPUT_RENDER,
                    {"input_buffer": self.input_buffer},
                    "renderer",
                )

                # Check if any plugin provided enhanced input
                if "main" in result:
                    for hook_result in result["main"].values():
                        if (
                            isinstance(hook_result, dict)
                            and "fancy_input_lines" in hook_result
                        ):
                            lines.extend(hook_result["fancy_input_lines"])
                            return
            except Exception as e:
                logger.warning(f"Error rendering enhanced input: {e}")

        # Default input rendering - always use modern design system
        self._render_input_modern(lines, position="only")

    def _render_input_modern(self, lines: List[str], position: str = "first") -> None:
        """Render input area using modern design without tag section."""
        from kollabor_tui.terminal_state import get_global_width

        width = get_global_width()
        simple_mode = getattr(self, "simple_mode", False)

        # Prepare input data
        input_data = self._prepare_input_data()
        cursor_info = self._calculate_cursor_position(input_data)
        visible_info = self._calculate_visible_window(
            input_data.lines, cursor_info.cursor_line
        )

        # Get colors
        input_border = self._get_input_shimmer_color(input_data.is_shell_command)
        input_bg = self._get_input_base_color(input_data.is_shell_command)
        input_fg = self._get_stable_input_foreground(input_data.is_shell_command)

        # Render borders
        show_top = position in ("only", "first")
        show_bottom = position in ("only", "last")
        if show_top and not simple_mode:
            lines.append(solid_fg("▄" * width, input_border))

        # Render content lines
        cursor_char = self._get_cursor_char(simple_mode)
        self._render_input_content(
            lines,
            input_data,
            cursor_info,
            visible_info,
            cursor_char,
            input_bg,
            input_fg,
            width,
            simple_mode,
        )

        # Bottom border
        if show_bottom and not simple_mode:
            lines.append(solid_fg("▀" * width, input_border))

    def _prepare_input_data(self) -> InputData:
        """Prepare and normalize input buffer for rendering."""
        is_shell = self.input_buffer.lstrip().startswith("!")
        prompt_style = (
            self._app_config.get("kollabor.ui.prompt_char", "chevron")
            if self._app_config
            else "chevron"
        )
        prompt_chars = {
            "chevron": "❯",
            "arrow": "▶",
            "angle": ">",
            "dot": "●",
            "diamond": "◆",
            "braille": "⠠⠵",
            "dash": "—",
        }
        prompt = "!" if is_shell else prompt_chars.get(prompt_style, "❯")
        display_buffer = self.input_buffer
        cursor_pos = getattr(self, "cursor_position", 0)

        # Strip ! from shell commands for display
        if is_shell and self.input_buffer:
            stripped = self.input_buffer.lstrip()
            leading_ws = len(self.input_buffer) - len(stripped)
            if stripped.startswith("!"):
                display_buffer = self.input_buffer[:leading_ws] + stripped[1:]
                if cursor_pos > leading_ws + 1:
                    cursor_pos -= 1
                elif cursor_pos > leading_ws:
                    cursor_pos = leading_ws

        return InputData(
            buffer=display_buffer,
            cursor_pos=max(0, min(cursor_pos, len(display_buffer))),
            is_shell_command=is_shell,
            lines=display_buffer.split("\n"),
            prompt=prompt,
            continuation_prompt="  ",
        )

    def _calculate_cursor_position(self, input_data: InputData) -> CursorInfo:
        """Calculate which line and position the cursor is on."""
        chars_counted = 0
        for i, line in enumerate(input_data.lines):
            line_end = chars_counted + len(line)
            if input_data.cursor_pos <= line_end:
                return CursorInfo(
                    cursor_line=i,
                    cursor_pos_in_line=input_data.cursor_pos - chars_counted,
                )
            chars_counted = line_end + 1
        return CursorInfo(cursor_line=0, cursor_pos_in_line=0)

    def _calculate_visible_window(
        self, lines: list[str], cursor_line: int
    ) -> VisibleInfo:
        """Calculate which lines should be visible (max 3)."""
        MAX_VISIBLE = 3
        total = len(lines)

        if total <= MAX_VISIBLE:
            return VisibleInfo(lines=lines, start=0, has_above=False, has_below=False)

        half = MAX_VISIBLE // 2
        start = max(0, cursor_line - half)
        end = min(total, start + MAX_VISIBLE)

        if end == total:
            start = max(0, total - MAX_VISIBLE)

        return VisibleInfo(
            lines=lines[start:end],
            start=start,
            has_above=start > 0,
            has_below=end < total,
        )

    def _get_input_base_color(self, is_shell: bool) -> tuple[int, int, int]:
        """Get the non-animated base background color for the input box."""
        base: Any = T().input_bg[0]

        if isinstance(base, (list, tuple)) and len(base) == 3:
            if isinstance(base[0], (list, tuple)):
                base = base[0]

        return tuple(base) if isinstance(base, (list, tuple)) else T().input_bg[0]

    def _get_input_border_color(self, is_shell: bool) -> tuple[int, int, int]:
        """Get the non-animated border/accent color for the input box."""
        if is_shell:
            base: Any = T().error[0]
        elif self.input_handler:
            from kollabor_events.models import CommandMode

            if self.input_handler.command_mode == CommandMode.MENU_POPUP:
                base = T().primary[0]
            else:
                base = T().secondary[0]
        else:
            base = T().secondary[0]

        if isinstance(base, (list, tuple)) and len(base) == 3:
            if isinstance(base[0], (list, tuple)):
                base = base[0]

        return tuple(base) if isinstance(base, (list, tuple)) else T().primary[0]

    def _get_stable_input_foreground(self, is_shell: bool) -> tuple[int, int, int]:
        """Choose readable input text color once from the base background.

        This intentionally ignores shimmer frames so the foreground does not
        flicker between dark and light while the input background pulses.
        """
        base_color = self._get_input_base_color(is_shell)
        return T().text_on(base_color)

    def _get_input_shimmer_color(self, is_shell: bool) -> tuple[Any, ...]:
        """Get the input border color with shimmer effect."""
        base = self._get_input_border_color(is_shell)

        # Apply shimmer if not idle
        is_idle = (time.time() - self._last_activity) > self._idle_timeout
        if is_idle:
            return base

        shimmer = math.sin(time.time() * self._shimmer_speed * 2 * math.pi) * 0.5 + 0.5
        return tuple(
            min(255, int(c + (255 - c) * shimmer * self._shimmer_intensity))
            for c in base
        )

    def _get_cursor_char(self, simple_mode: bool) -> str:
        """Get the cursor character (handles blinking)."""
        now = time.time()
        is_idle = (now - self._last_activity) > self._idle_timeout

        if simple_mode:
            return "|"
        if is_idle:
            return str(C["cursor"])

        if now - self._cursor_last_blink >= self._cursor_blink_rate:
            self._cursor_visible = not self._cursor_visible
            self._cursor_last_blink = now
        return str(C["cursor"]) if self._cursor_visible else " "

    def _render_input_content(
        self,
        lines: list[str],
        input_data: InputData,
        cursor_info: CursorInfo,
        visible_info: VisibleInfo,
        cursor_char: str,
        input_bg: tuple[Any, ...],
        input_fg: tuple[int, int, int],
        width: int,
        simple_mode: bool,
    ) -> None:
        """Render the visible input lines with wrapping and cursor."""
        for i, line_text in enumerate(visible_info.lines):
            actual_idx = visible_info.start + i

            # Scroll indicator
            scroll = " "
            if i == 0 and visible_info.has_above:
                scroll = "▲"
            elif i == len(visible_info.lines) - 1 and visible_info.has_below:
                scroll = "▼"

            # Prompt
            line_prompt = (
                input_data.prompt if actual_idx == 0 else input_data.continuation_prompt
            )

            # Insert cursor
            if actual_idx == cursor_info.cursor_line:
                pos = max(0, min(cursor_info.cursor_pos_in_line, len(line_text)))
                line_text = line_text[:pos] + cursor_char + line_text[pos:]

            # Wrap and render
            prefix = f"{scroll} {line_prompt} "
            prefix_len = len(prefix)
            available = width - prefix_len

            for wrap_idx, wrapped in enumerate(
                wrap_text(line_text, available, word_wrap=True, continuation_indent=0)
            ):
                if wrap_idx == 0:
                    content = f"{prefix}{wrapped}"
                else:
                    content = " " * prefix_len + wrapped

                content = content.ljust(width)[:width]

                if simple_mode:
                    lines.append(content.replace(str(C["cursor"]), "|"))
                else:
                    lines.append(solid(content, input_bg, input_fg, width))

    def _write(self, text: str) -> None:
        """Write text directly to terminal.

        Args:
            text: Text to write.
        """
        # Collect in buffer if buffered mode is active
        if hasattr(self, "_write_buffer") and self._write_buffer is not None:
            self._write_buffer.append(text)
        else:
            self.terminal_state.write_raw(text)

    def _start_buffered_write(self) -> None:
        """Start buffered write mode - collects all writes until flush."""
        self._write_buffer: list[str] = []

    def _flush_buffered_write(self) -> None:
        """Flush all buffered writes at once to reduce flickering."""
        if hasattr(self, "_write_buffer") and self._write_buffer is not None:
            # Join all buffered content and write in one operation
            self.terminal_state.write_raw("".join(self._write_buffer))
        self._write_buffer = None  # type: ignore[assignment]

    def _get_terminal_width(self) -> int:
        """Get terminal width, with fallback."""
        width, _ = self.terminal_state.get_size()
        return width

    async def _get_command_menu_lines(self) -> list[str]:
        """Get command menu lines if menu is active.

        Returns:
            List of command menu lines, or empty list if not active.
        """
        if not self.event_bus:
            return []

        try:
            # Check for command menu via COMMAND_MENU_RENDER event
            from kollabor_events import EventType

            logger.debug("Emitting COMMAND_MENU_RENDER event")
            result = await self.event_bus.emit_with_hooks(
                EventType.COMMAND_MENU_RENDER,
                {"request": "get_menu_lines"},
                "renderer",
            )
            logger.debug(f"COMMAND_MENU_RENDER result: {result}")

            # Check if any component provided menu lines
            if "main" in result and "hook_results" in result["main"]:
                for hook_result in result["main"]["hook_results"]:
                    if (
                        isinstance(hook_result, dict)
                        and "result" in hook_result
                        and isinstance(hook_result["result"], dict)
                        and "menu_lines" in hook_result["result"]
                    ):
                        return list(hook_result["result"]["menu_lines"])

        except Exception as e:
            logger.debug(f"No command menu available: {e}")

        return []

    async def _get_status_modal_lines(self) -> list[str]:
        """Get status modal lines if status modal is active.

        Returns:
            List of status modal lines, or empty list if not active.
        """
        if not self.event_bus:
            return []

        try:
            # Check for status modal via input handler
            from kollabor_events import EventType

            result = await self.event_bus.emit_with_hooks(
                EventType.STATUS_MODAL_RENDER,
                {"request": "get_status_modal_lines"},
                "renderer",
            )

            # Check if any component provided status modal lines
            if "main" in result and "hook_results" in result["main"]:
                for hook_result in result["main"]["hook_results"]:
                    if (
                        isinstance(hook_result, dict)
                        and "result" in hook_result
                        and isinstance(hook_result["result"], dict)
                        and "status_modal_lines" in hook_result["result"]
                    ):
                        return list(hook_result["result"]["status_modal_lines"])

        except Exception as e:
            logger.debug(f"No status modal available: {e}")

        return []

    async def _render_lines(self, lines: List[str], size_changed: bool = False) -> None:
        """Render lines to terminal with proper clearing.

        Args:
            lines: Lines to render.
            size_changed: True if terminal size changed (triggers aggressive clearing).
        """
        render_generation = self._render_generation + 1
        self._render_generation = render_generation

        # --- Skip if cached and unchanged ---
        cache_enabled = self._should_enable_render_cache()
        if (
            cache_enabled
            and not size_changed
            and not self._resize_redraw_pending
            and self._last_render_content == lines
        ):
            return

        self._last_render_content = lines.copy()
        current_line_count = len(lines)

        self._start_buffered_write()

        # --- Clear previous render ---
        self._clear_previous_render(current_line_count, size_changed)

        # --- Render new content ---
        for i, line in enumerate(lines):
            if i > 0:
                self._write("\n")
            self._write("\r\033[2K")
            self._write(line)

        self._write("\033[?25l")  # Hide cursor

        self._flush_buffered_write()

        if render_generation != self._render_generation:
            logger.debug("Skipping stale render state commit")
            return

        self.last_line_count = current_line_count
        self.input_line_written = True
        self._resize_redraw_pending = False

    def _should_enable_render_cache(self) -> bool:
        """Check if render cache should be enabled."""
        if self._app_config is not None:
            cache_enabled: bool = self._app_config.get(
                "terminal.render_cache_enabled", True
            )
        else:
            cache_enabled = self._render_cache_enabled

        # Disable during animations (spinner, shimmer)
        if cache_enabled:
            now = time.time()
            is_animating = (
                self._tool_executing
                or self.thinking_active
                or (now - self._last_activity) <= self._idle_timeout
            )
            if is_animating:
                return False

        return cache_enabled

    def _clear_previous_render(
        self, current_line_count: int, size_changed: bool
    ) -> None:
        """Clear the previously rendered active area.

        Args:
            current_line_count: Number of lines in new render.
            size_changed: True if terminal size changed.
        """
        if not (
            self.input_line_written
            and hasattr(self, "last_line_count")
            and self.last_line_count > 0
        ):
            # First render - just clear current line
            self._write("\r\033[2K")
            return

        if size_changed:
            self._clear_for_resize()
        elif current_line_count < self.last_line_count:
            self._clear_for_shrinking_content()
        else:
            self._reposition_cursor_for_overwrite()

    def _clear_for_resize(self) -> None:
        """Clear active area aggressively on resize (catches artifacts above)."""
        logger.debug("Terminal resize - aggressive clear")
        # Move to column 0 first so upward movement anchors consistently.
        self._write("\r")
        # Move up through the previously rendered active area plus a small margin.
        clear_lines = max(self.last_line_count + 3, 1)
        if clear_lines > 1:
            self._write(f"\033[{clear_lines - 1}A")
        self._write("\r\033[J")  # Clear to end of screen from a stable anchor

    def _clear_for_shrinking_content(self) -> None:
        """Clear old lines when content height decreased."""
        # Move up to start of previous render
        if self.last_line_count > 1:
            self._write(f"\033[{self.last_line_count - 1}A")
        self._write("\r")

        for i in range(self.last_line_count):
            self._write("\033[2K")  # Clear line
            if i < self.last_line_count - 1:
                self._write("\033[B")  # Move down

        # Move back to start
        if self.last_line_count > 1:
            self._write(f"\033[{self.last_line_count - 1}A")

    def _reposition_cursor_for_overwrite(self) -> None:
        """Move cursor back to start for overwrite (line count same or increased)."""
        if self.last_line_count > 1:
            self._write(f"\033[{self.last_line_count - 1}A")  # Move up to start
        self._write("\r")

    def clear_active_area(self, force: bool = False) -> None:
        """Clear the active area before writing conversation messages.

        Args:
            force: If True, clear regardless of input_line_written state.
                   Use for exit cleanup.
        """
        if (force or self.input_line_written) and hasattr(self, "last_line_count"):
            self.terminal_state.clear_line()
            for _ in range(self.last_line_count - 1):
                self.terminal_state.move_cursor_up(1)
                self.terminal_state.clear_line()
            self.input_line_written = False
            self.invalidate_render_cache()  # Force re-render after clearing
            logger.debug("Cleared active area")

    def invalidate_render_cache(self) -> None:
        """Invalidate the render cache to force next render.

        Call this when external changes should force a re-render
        (e.g., terminal resize, configuration changes, manual refresh).
        """
        self._last_render_content.clear()
        logger.debug("Render cache invalidated")
