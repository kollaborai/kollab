"""Layout management system for terminal rendering.

This module provides comprehensive layout management for terminal rendering,
including area management, adaptive sizing, and thinking animation support.
"""

import logging
import re
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class LayoutMode(Enum):
    """Layout rendering modes."""

    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"
    STACKED = "stacked"
    ADAPTIVE = "adaptive"


class AreaAlignment(Enum):
    """Alignment options for layout areas."""

    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"
    JUSTIFY = "justify"


@dataclass
class ScreenRegion:
    """Represents a region of the screen."""

    x: int
    y: int
    width: int
    height: int

    def contains_point(self, x: int, y: int) -> bool:
        """Check if point is within this region."""
        return self.x <= x < self.x + self.width and self.y <= y < self.y + self.height

    def intersects(self, other: "ScreenRegion") -> bool:
        """Check if this region intersects with another."""
        return not (
            self.x + self.width <= other.x
            or other.x + other.width <= self.x
            or self.y + self.height <= other.y
            or other.y + other.height <= self.y
        )


@dataclass
class LayoutArea:
    """Represents a layout area with content and configuration."""

    name: str
    content: List[str] = field(default_factory=list)
    region: Optional[ScreenRegion] = None
    alignment: AreaAlignment = AreaAlignment.LEFT
    visible: bool = True
    priority: int = 0
    min_width: int = 10
    min_height: int = 1
    max_width: Optional[int] = None
    max_height: Optional[int] = None
    padding: int = 0

    def get_content_width(self) -> int:
        """Get the maximum width of content in this area."""
        if not self.content:
            return 0
        # Account for ANSI codes when measuring width
        return max(len(self._strip_ansi(line)) for line in self.content)

    def get_content_height(self) -> int:
        """Get the height of content in this area."""
        return len(self.content)

    def _strip_ansi(self, text: str) -> str:
        """Remove ANSI escape codes from text for width calculation."""
        return re.sub(r"\033\[[0-9;]*m", "", text)


class ThinkingAnimationManager:
    """Manages thinking animation state and display."""

    def __init__(self, spinner_frames: Optional[List[str]] = None):
        """Initialize thinking animation manager.

        Args:
            spinner_frames: Custom spinner frames (uses default if None).
        """
        self.spinner_frames = spinner_frames or [
            "⠋",
            "⠙",
            "⠹",
            "⠸",
            "⠼",
            "⠴",
            "⠦",
            "⠧",
        ]
        self.current_frame = 0
        self.is_active = False
        self.start_time: Optional[float] = None
        self.messages: deque[str] = deque(maxlen=2)

    def start_thinking(self, message: str = "") -> None:
        """Start thinking animation with optional message.

        Args:
            message: Thinking message to display.
        """
        self.is_active = True
        if message:
            # Clear previous messages and set the new one
            self.messages.clear()
            self.messages.append(message)
        if not self.start_time:
            self.start_time = time.time()

    def stop_thinking(self) -> Optional[str]:
        """Stop thinking animation and return completion message.

        Returns:
            Completion message if thinking was active.
        """
        if not self.is_active:
            return None

        self.is_active = False
        completion_msg = None

        if self.start_time:
            duration = time.time() - self.start_time
            completion_msg = f"Thought for {duration:.1f} seconds"
            self.start_time = None

        self.messages.clear()
        return completion_msg

    def get_next_frame(self) -> str:
        """Get next spinner frame for animation.

        Returns:
            Current spinner frame character.
        """
        if not self.is_active:
            return ""

        frame = self.spinner_frames[self.current_frame]
        self.current_frame = (self.current_frame + 1) % len(self.spinner_frames)
        return frame

    def get_current_spinner_frame(self) -> str:
        """Get current spinner frame without advancing.

        Returns:
            Current spinner frame character without advancing index.
        """
        if not self.is_active and self.current_frame == 0:
            return ""
        return self.spinner_frames[self.current_frame]

    def get_display_lines(self, apply_effect_func) -> List[str]:
        """Get formatted display lines for thinking animation.

        Args:
            apply_effect_func: Function to apply visual effects to text.

        Returns:
            List of formatted display lines.
        """
        if not self.is_active or not self.messages:
            return []

        lines = []
        spinner = self.get_next_frame()

        # Calculate elapsed time
        elapsed = ""
        if self.start_time:
            duration = time.time() - self.start_time
            elapsed = f" ({duration:.0f}s - esc to cancel)"

        for i, msg in enumerate(self.messages):
            if i == len(self.messages) - 1:
                # Main thinking line with spinner and elapsed time
                formatted_text = apply_effect_func(
                    f"{spinner} Thinking{elapsed}: {msg}"
                )
                lines.append(formatted_text)
            else:
                # Secondary thinking line
                formatted_text = apply_effect_func(f"  {msg}")
                lines.append(formatted_text)

        return lines

    def get_display_lines_modern(
        self,
        width: Optional[int] = None,
        tokens: Optional[int] = None,
        apply_effect_func=None,
        tool_executing: bool = False,
        tool_name: str = "",
        tool_spinner: str = "",
        simple_mode: bool = False,
    ) -> List[str]:
        """Get formatted display lines using modern TagBox design.

        Matches thinking_v2 from the design system exactly.

        Args:
            width: Total width of the thinking box (defaults to global width).
            tokens: Optional token count to display.
            apply_effect_func: Optional function to apply visual effects (shimmer, etc.)
            tool_executing: Whether a tool is currently executing.
            tool_name: Name of the tool being executed.
            tool_spinner: Current spinner frame for tool animation.
            simple_mode: If True, render simple text without fancy UI.

        Returns:
            List of formatted display lines with TagBox styling.
        """
        from kollabor_tui.design_system import T, TagBox
        from kollabor_tui.terminal_state import get_global_width

        # Use global width as default if not specified
        if width is None:
            width = get_global_width()

        if not self.is_active:
            return []

        # Simple mode: render plain text thinking indicator
        if simple_mode:
            seconds = 0.0
            if self.start_time:
                seconds = time.time() - self.start_time

            # Build simple text
            if self.messages:
                current_msg = self.messages[-1]
                if tokens:
                    text = f"~ {current_msg} {seconds:.1f}s ({tokens} tokens)"
                else:
                    text = f"~ {current_msg} {seconds:.1f}s"
            else:
                if tokens:
                    text = f"~ thinking {seconds:.1f}s ({tokens} tokens)"
                else:
                    text = f"~ thinking {seconds:.1f}s..."

            lines = [text]

            # Add tool execution line if tool is running
            if tool_executing and tool_name:
                lines.append(f"  Executing {tool_name}...")

            return lines

        # Calculate elapsed time in seconds
        seconds = 0.0
        if self.start_time:
            seconds = time.time() - self.start_time  # type: ignore[union-attr]

        # Build content text - matches thinking_v2 format exactly
        # Include the current thinking message if available
        content_lines = []

        if self.messages:
            current_msg = self.messages[-1]
            # Message may contain newlines (streaming preview) — split into
            # separate content lines so TagBox renders each on its own row.
            msg_lines = current_msg.split("\n")
            # Append timer/tokens to the last line only
            for i, line in enumerate(msg_lines):
                if i == len(msg_lines) - 1:
                    if tokens:
                        content_lines.append(
                            f" {line} {seconds:.1f}s  ({tokens} tokens)"
                        )
                    else:
                        content_lines.append(f" {line} {seconds:.1f}s")
                else:
                    content_lines.append(f" {line}")
        else:
            if tokens:
                content_lines.append(f" thinking {seconds:.1f}s  ({tokens} tokens)")
            else:
                content_lines.append(f" thinking {seconds:.1f}s...")

        # Add tool execution line inside the box if tool is running
        # TagBox's wrap_text handles wrapping to content_width automatically
        if tool_executing and tool_name:
            content_lines.append(f" {tool_spinner} Executing {tool_name}...")

        # Get next spinner frame for tag (advance animation)
        spinner = self.get_next_frame()
        tag_char = f" {spinner} " if spinner else " ~ "

        # Build tag_chars list - spinner for first line, blanks for rest
        tag_chars = [tag_char] + ["   "] * (len(content_lines) - 1)

        # Render with TagBox - thinking_v2 style from preview-modern-ui.py
        rendered = TagBox.render(
            lines=content_lines,
            tag_bg=T().thinking_tag,
            tag_width=3,
            content_colors=T().dark,
            content_fg=T().text,
            content_width=width - 3,
            tag_chars=tag_chars,
        )

        # Apply shimmer/pulse effect AFTER TagBox renders (preserves box structure)
        # Only apply to the middle line (content), not top/bottom borders
        lines = rendered.split("\n")
        if apply_effect_func and len(lines) >= 3:
            # Apply effect to content line(s) - skip first (top border) and last (bottom border)
            for i in range(1, len(lines) - 1):
                lines[i] = apply_effect_func(lines[i])

        return lines


class LayoutManager:
    """Manages terminal layout with multiple areas and adaptive sizing."""

    def __init__(self, terminal_width: int = 80, terminal_height: int = 24):
        """Initialize layout manager.

        Args:
            terminal_width: Terminal width in characters.
            terminal_height: Terminal height in characters.
        """
        self.terminal_width = terminal_width
        self.terminal_height = terminal_height

        # Layout areas
        self._areas: Dict[str, LayoutArea] = {}

        # Layout state
        self._dirty = True
        self._last_render_lines = 0

        # Permission prompt state
        self._permission_view: Optional[Any] = None
        self._permission_response_event: Optional[Any] = None
        self._permission_response: Optional[Any] = None

        # Initialize standard areas
        self._initialize_standard_areas()

    def _initialize_standard_areas(self) -> None:
        """Initialize standard layout areas (status, input, thinking)."""
        self._areas["status_a"] = LayoutArea("status_a", priority=10)
        self._areas["status_b"] = LayoutArea("status_b", priority=10)
        self._areas["status_c"] = LayoutArea("status_c", priority=10)
        self._areas["input"] = LayoutArea("input", priority=20)
        self._areas["thinking"] = LayoutArea("thinking", priority=30)

    def set_terminal_size(self, width: int, height: int) -> None:
        """Update terminal dimensions.

        Args:
            width: New terminal width.
            height: New terminal height.
        """
        if self.terminal_width != width or self.terminal_height != height:
            self.terminal_width = width
            self.terminal_height = height
            self._dirty = True

    def add_area(self, name: str, area: LayoutArea) -> None:
        """Add a layout area.

        Args:
            name: Area name.
            area: LayoutArea instance.
        """
        self._areas[name] = area
        self._dirty = True

    def get_area(self, name: str) -> Optional[LayoutArea]:
        """Get a layout area by name.

        Args:
            name: Area name.

        Returns:
            LayoutArea instance or None if not found.
        """
        return self._areas.get(name)

    def update_area_content(self, name: str, content: List[str]) -> None:
        """Update content for a specific area.

        Args:
            name: Area name.
            content: New content lines.
        """
        area = self._areas.get(name)
        if area:
            area.content = content.copy()
            self._dirty = True

    def set_area_visibility(self, name: str, visible: bool) -> None:
        """Set visibility for a specific area.

        Args:
            name: Area name.
            visible: Whether area should be visible.
        """
        area = self._areas.get(name)
        if area and area.visible != visible:
            area.visible = visible
            self._dirty = True

    def calculate_layout(
        self, mode: LayoutMode = LayoutMode.ADAPTIVE
    ) -> Dict[str, ScreenRegion]:
        """Calculate layout regions for all visible areas.

        Args:
            mode: Layout mode to use.

        Returns:
            Dictionary mapping area names to screen regions.
        """
        visible_areas = {
            name: area
            for name, area in self._areas.items()
            if area.visible and area.content
        }

        if not visible_areas:
            return {}

        if mode == LayoutMode.ADAPTIVE:
            return self._calculate_adaptive_layout(visible_areas)
        elif mode == LayoutMode.HORIZONTAL:
            return self._calculate_horizontal_layout(visible_areas)
        elif mode == LayoutMode.VERTICAL:
            return self._calculate_vertical_layout(visible_areas)
        else:
            return self._calculate_stacked_layout(visible_areas)

    def _calculate_adaptive_layout(
        self, areas: Dict[str, LayoutArea]
    ) -> Dict[str, ScreenRegion]:
        """Calculate adaptive layout based on terminal size and content.

        Args:
            areas: Dictionary of visible areas.

        Returns:
            Dictionary mapping area names to screen regions.
        """
        regions: Dict[str, ScreenRegion] = {}
        current_y = 0

        # Sort areas by priority (higher priority first)
        _ = sorted(areas.items(), key=lambda x: x[1].priority, reverse=True)

        # Handle thinking area first (if present)
        if "thinking" in areas and areas["thinking"].content:
            thinking_height = areas["thinking"].get_content_height()
            regions["thinking"] = ScreenRegion(
                0, current_y, self.terminal_width, thinking_height
            )
            current_y += thinking_height + 1  # Add spacing

        # Handle input area
        if "input" in areas and areas["input"].content:
            input_height = areas["input"].get_content_height()
            regions["input"] = ScreenRegion(
                0, current_y, self.terminal_width, input_height
            )
            current_y += input_height + 1

        # Handle status areas
        status_areas = {
            name: area
            for name, area in areas.items()
            if name.startswith("status_") and area.content
        }

        if status_areas:
            status_regions = self._layout_status_areas(status_areas, current_y)
            regions.update(status_regions)

        return regions

    def _calculate_horizontal_layout(
        self, areas: Dict[str, LayoutArea]
    ) -> Dict[str, ScreenRegion]:
        """Calculate horizontal layout (side-by-side areas).

        Args:
            areas: Dictionary of visible areas.

        Returns:
            Dictionary mapping area names to screen regions.
        """
        regions: Dict[str, ScreenRegion] = {}
        area_count = len(areas)

        if area_count == 0:
            return regions

        area_width = max(1, (self.terminal_width - (area_count - 1)) // area_count)
        current_x = 0

        for i, (name, area) in enumerate(areas.items()):
            # Last area gets remaining width
            if i == area_count - 1:
                width = self.terminal_width - current_x
            else:
                width = area_width

            regions[name] = ScreenRegion(current_x, 0, width, area.get_content_height())
            current_x += width + 1  # Add spacing

        return regions

    def _calculate_vertical_layout(
        self, areas: Dict[str, LayoutArea]
    ) -> Dict[str, ScreenRegion]:
        """Calculate vertical layout (stacked areas).

        Args:
            areas: Dictionary of visible areas.

        Returns:
            Dictionary mapping area names to screen regions.
        """
        regions = {}
        current_y = 0

        for name, area in areas.items():
            height = area.get_content_height()
            regions[name] = ScreenRegion(0, current_y, self.terminal_width, height)
            current_y += height + 1  # Add spacing

        return regions

    def _calculate_stacked_layout(
        self, areas: Dict[str, LayoutArea]
    ) -> Dict[str, ScreenRegion]:
        """Calculate stacked layout (overlapping areas).

        Args:
            areas: Dictionary of visible areas.

        Returns:
            Dictionary mapping area names to screen regions.
        """
        regions = {}

        # Simple stacked layout - each area takes full width
        for name, area in areas.items():
            height = area.get_content_height()
            regions[name] = ScreenRegion(0, 0, self.terminal_width, height)

        return regions

    def _layout_status_areas(
        self, status_areas: Dict[str, LayoutArea], start_y: int
    ) -> Dict[str, ScreenRegion]:
        """Layout status areas with adaptive column management.

        Args:
            status_areas: Dictionary of status areas.
            start_y: Starting Y position.

        Returns:
            Dictionary mapping status area names to screen regions.
        """
        regions: Dict[str, ScreenRegion] = {}

        if not status_areas:
            return regions

        # Determine layout based on terminal width
        if self.terminal_width >= 80:
            # Three-column layout for wide terminals
            column_width = (self.terminal_width - 6) // 3  # 6 for spacing

            # Get areas A, B, C in order
            area_names = ["status_a", "status_b", "status_c"]
            areas_with_content = [
                (name, status_areas.get(name))
                for name in area_names
                if name in status_areas
            ]

            max_height = (
                max(
                    area.get_content_height()
                    for _, area in areas_with_content
                    if area is not None
                )
                if areas_with_content
                else 1
            )

            for i, (name, area) in enumerate(areas_with_content):
                x_pos = i * (column_width + 2)  # 2 for spacing
                regions[name] = ScreenRegion(x_pos, start_y, column_width, max_height)
        else:
            # Vertical layout for narrow terminals
            current_y = start_y
            for name, area in status_areas.items():
                height = area.get_content_height()
                regions[name] = ScreenRegion(0, current_y, self.terminal_width, height)
                current_y += height + 1

        return regions

    def render_areas(self, regions: Dict[str, ScreenRegion]) -> List[str]:
        """Render all areas into display lines.

        Args:
            regions: Dictionary mapping area names to screen regions.

        Returns:
            List of formatted display lines.
        """
        lines = []
        max_y = (
            max(region.y + region.height for region in regions.values())
            if regions
            else 0
        )

        for y in range(max_y):
            line_parts = {}

            # Collect content for this line from all areas
            for name, region in regions.items():
                if region.y <= y < region.y + region.height:
                    area = self._areas[name]
                    content_index = y - region.y

                    if content_index < len(area.content):
                        content = area.content[content_index]
                        line_parts[region.x] = (
                            region,
                            content,
                            area.alignment,
                        )

            # Build the line
            if line_parts:
                line = self._build_line_from_parts(line_parts)
                lines.append(line)
            else:
                lines.append("")

        self._last_render_lines = len(lines)
        self._dirty = False
        return lines

    def _build_line_from_parts(
        self, line_parts: Dict[int, Tuple[ScreenRegion, str, AreaAlignment]]
    ) -> str:
        """Build a single display line from area parts.

        Args:
            line_parts: Dictionary mapping x-position to (region, content, alignment).

        Returns:
            Formatted line string.
        """
        if not line_parts:
            return ""

        # Sort by x position
        sorted_parts = sorted(line_parts.items())
        line_chars = [" "] * self.terminal_width

        for x_pos, (region, content, alignment) in sorted_parts:
            # Remove ANSI codes for width calculation
            visible_content = re.sub(r"\033\[[0-9;]*m", "", content)

            # Apply alignment within the region
            if alignment == AreaAlignment.CENTER:
                padding = max(0, (region.width - len(visible_content)) // 2)
                start_x = region.x + padding
            elif alignment == AreaAlignment.RIGHT:
                padding = max(0, region.width - len(visible_content))
                start_x = region.x + padding
            else:  # LEFT or JUSTIFY
                start_x = region.x

            # Place content in line, handling ANSI codes
            content_chars = list(content)
            for i, char in enumerate(content_chars):
                pos = start_x + i
                if 0 <= pos < self.terminal_width and pos < region.x + region.width:
                    line_chars[pos] = char

        return "".join(line_chars).rstrip()

    def get_render_info(self) -> Dict[str, Any]:
        """Get layout rendering information for debugging.

        Returns:
            Dictionary with layout information.
        """
        return {
            "terminal_size": (self.terminal_width, self.terminal_height),
            "areas_count": len(self._areas),
            "visible_areas": [
                name for name, area in self._areas.items() if area.visible
            ],
            "dirty": self._dirty,
            "last_render_lines": self._last_render_lines,
            "area_stats": {
                name: {
                    "content_lines": len(area.content),
                    "content_width": area.get_content_width(),
                    "visible": area.visible,
                    "priority": area.priority,
                }
                for name, area in self._areas.items()
            },
        }

    # ==================== PERMISSION PROMPT METHODS ====================

    def has_active_permission_prompt(self) -> bool:
        """Check if permission prompt is currently displayed."""
        return self._permission_view is not None

    async def show_permission_prompt(
        self,
        confirmation_details: Dict[str, Any],
    ) -> Any:  # ConfirmationResponse
        """
        Show permission prompt in thinking area and wait for response.

        Replaces the normal "Thinking..." or "Executing..." status with
        the permission prompt. Uses Box() style rendering.

        Args:
            confirmation_details: Details about the tool requiring permission

        Returns:
            User's confirmation response (ConfirmationResponse)
        """
        import asyncio

        from kollabor_events.permissions_models import ConfirmationResponse
        from kollabor_tui.status.permission_status_view import PermissionStatusView

        self._permission_response_event = asyncio.Event()
        self._permission_response = None

        def on_response(response: ConfirmationResponse) -> None:
            self._permission_response = response
            self._permission_response_event.set()  # type: ignore[union-attr]

        self._permission_view = PermissionStatusView(
            confirmation_details=confirmation_details,
            on_response=on_response,
            confirmation_response_enum=ConfirmationResponse,
        )

        # Replace thinking area content with permission prompt
        lines = self._permission_view.render()
        self.update_area_content("thinking", lines)

        # Trigger layout dirty flag
        self._dirty = True

        # CRITICAL: Yield to event loop to allow render loop to display the prompt
        # Without this, we block before the UI can render, causing a deadlock
        await asyncio.sleep(0.1)  # Give render loop time to update screen

        # Wait for user keypress
        await self._permission_response_event.wait()

        # Clear permission view, restore normal status
        self._permission_view = None
        self.update_area_content("thinking", [])
        self._dirty = True

        return self._permission_response

    def handle_permission_keypress(self, key_char: str) -> bool:
        """Route keypress to permission view if active.

        Args:
            key_char: Character pressed

        Returns:
            True if handled by permission view, False otherwise
        """
        if self._permission_view:
            return bool(self._permission_view.handle_keypress(key_char))
        return False

    def cancel_permission_prompt(self) -> None:
        """Cancel any active permission prompt.

        This is called when the user sends a new message while a permission
        prompt is waiting, preventing lockup.
        """
        if self._permission_view:
            logger.info("Cancelling active permission prompt")
            from kollabor_events.permissions_models import ConfirmationResponse

            # Set the response to CANCEL so the blocked coroutine gets a proper response
            self._permission_response = ConfirmationResponse.CANCEL
            # Trigger the event to unblock the waiting coroutine
            if self._permission_response_event:
                self._permission_response_event.set()
            # Clear the permission view and restore normal status
            self._permission_view = None
            self.update_area_content("thinking", [])
            self._dirty = True
