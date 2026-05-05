"""Modal renderer using existing visual effects infrastructure."""

import logging
import re
from typing import Any, Dict, List, Optional

# Cross-package imports: these stay in kollabor for now
from kollabor_events.models import UIConfig
from kollabor_tui.design_system import Box, C, S, T, TagBox, solid, solid_fg

# kollabor_tui imports (same package)
from kollabor_tui.key_parser import KeyPress
from kollabor_tui.terminal_state import get_terminal_size

# Import from kollabor_tui (same package)
# Same-package import: widgets in kollabor_tui.widgets
from kollabor_tui.widgets import (
    BaseWidget,
    CheckboxWidget,
    DropdownWidget,
    LabelWidget,
    SliderWidget,
    TextInputWidget,
)

# Same-package imports (modals module)
from .modal_actions import ModalActionHandler
from .modal_overlay_renderer import ModalOverlayRenderer
from .modal_state_manager import ModalDisplayMode, ModalLayout, ModalStateManager

logger = logging.getLogger(__name__)


class ModalRenderer:
    """Modal overlay renderer using existing visual effects system."""

    def __init__(
        self,
        terminal_renderer: Optional[Any],
        visual_effects: Optional[Any],
        config_service: Optional[Any] = None,
    ) -> None:
        """Initialize modal renderer with existing infrastructure.

        Args:
            terminal_renderer: Terminal renderer for output.
            visual_effects: Visual effects system for styling.
            config_service: ConfigService for config persistence.
        """
        self.terminal_renderer = terminal_renderer
        self.visual_effects = visual_effects
        self.config_service = config_service

        # NEW: Initialize overlay rendering system for proper modal display
        self.overlay_renderer: Optional[ModalOverlayRenderer] = None
        self.state_manager: Optional[ModalStateManager] = None

        if terminal_renderer is not None and hasattr(
            terminal_renderer, "terminal_state"
        ):
            self.overlay_renderer = ModalOverlayRenderer(
                terminal_renderer.terminal_state
            )
            self.state_manager = ModalStateManager(terminal_renderer.terminal_state)

        # Widget management
        self.widgets: List[BaseWidget] = []
        self.focused_widget_index = 0
        self.scroll_offset = 0
        self._triggered_action: Optional[Dict[str, Any]] = None
        self._save_confirm_active = False  # For save confirmation prompt
        self._save_target_active = False  # For local/global save target selection
        self._search_active = False
        self._search_query = ""
        self._visible_widget_indices: List[int] = (
            []
        )  # Widget indices visible after search filter
        self.use_modern_widgets = True  # Use design system rendering

        # Command list selection (for modals with "commands" sections)
        self.command_items: List[Dict] = []  # Flat list of all command items
        self.selected_command_index = 0
        self.has_command_sections = False
        self._command_line_positions: List[int] = (
            []
        )  # Starting line of each command item

        # Action handling
        self.action_handler = (
            ModalActionHandler(config_service) if config_service else None
        )

    @property
    def modal_width(self) -> int:
        """Get dynamic modal width (terminal_width - 2).

        Returns:
            Modal width based on current terminal dimensions.
        """
        terminal_width, _ = get_terminal_size()
        return max(40, terminal_width - 2)  # Minimum 40 cols

    @property
    def visible_height(self) -> int:
        """Get dynamic visible content height (terminal_height - 4 - chrome_offset).

        The chrome_offset accounts for title bar (3 lines) and footer (3 lines).
        Modal total height = terminal_height - 4
        Content visible height = modal_height - 6 (chrome) = terminal_height - 10

        Returns:
            Number of visible content lines.
        """
        _, terminal_height = get_terminal_size()
        # Modal height = terminal_height - 4
        # Chrome = 6 lines (title ~3, footer ~3)
        # Content = modal_height - chrome = terminal_height - 4 - 6 = terminal_height - 10
        return max(10, terminal_height - 10)  # Minimum 10 lines

    async def show_modal(self, ui_config: UIConfig) -> Dict[str, Any]:
        """Show modal overlay using TRUE overlay system.

        Args:
            ui_config: Modal configuration.

        Returns:
            Modal interaction result.
        """
        try:
            # Reset command selection state for fresh modal
            self._command_selected = False
            self._triggered_action = None  # Reset action key trigger
            self._search_active = False
            self._search_query = ""

            # FIXED: Use overlay system instead of chat pipeline clearing
            # No more clear_active_area() - that only clears display, not buffers

            # Render modal using existing visual effects (content generation)
            modal_lines = self._render_modal_box(ui_config)

            # Use overlay rendering instead of animation that routes through chat
            await self._render_modal_lines(modal_lines)

            return await self._handle_modal_input(ui_config)
        except Exception as e:
            logger.error(f"Error showing modal: {e}")
            # Ensure proper cleanup on error
            if self.state_manager:
                self.state_manager.restore_terminal_state()
            return {"success": False, "error": str(e)}

    def refresh_modal_display(self) -> bool:
        """Refresh modal display without accumulation using overlay system.

        This method refreshes the current modal content without any
        interaction with conversation buffers or message systems.

        Returns:
            True if refresh was successful.
        """
        try:
            # Use state manager to refresh display without chat pipeline
            if self.state_manager:
                return bool(self.state_manager.refresh_modal_display())
            else:
                logger.warning("State manager not available - fallback refresh")
                return True
        except Exception as e:
            logger.error(f"Error refreshing modal display: {e}")
            return False

    def close_modal(self) -> bool:
        """Close modal and restore terminal state.

        Returns:
            True if modal was closed successfully.
        """
        try:
            # Use state manager to properly restore terminal state
            if self.state_manager:
                return bool(self.state_manager.restore_terminal_state())
            else:
                logger.warning("State manager not available - fallback close")
                return True
        except Exception as e:
            logger.error(f"Error closing modal: {e}")
            return False

    def _render_modal_box(
        self, ui_config: UIConfig, preserve_widgets: bool = False
    ) -> List[str]:
        """Render modal box using design system.

        Args:
            ui_config: Modal configuration.
            preserve_widgets: If True, preserve existing widget states instead of recreating.

        Returns:
            List of rendered modal lines.
        """
        # Use explicit width if specified, otherwise use dynamic width (terminal_width - 2)
        if ui_config.width:
            width = min(int(ui_config.width), self.modal_width)
        else:
            width = self.modal_width
        title = ui_config.title or "Modal"

        lines = []

        # Title bar using design system Box
        title_box = Box.render(
            [f"  {S.BOLD}{title}{S.RESET_BOLD}"], T().primary, T().text, width
        )
        for line in title_box.split("\n"):
            lines.append(line)

        # Content area
        actual_content_width = width - 2
        content_lines = self._render_modal_content(
            ui_config.modal_config or {}, actual_content_width + 2, preserve_widgets
        )
        lines.extend(content_lines)

        # Bottom border with footer - segmented style
        if self._save_target_active:
            footer = "Save to: (L)ocal  (G)lobal  (Enter) local  (Esc) cancel"
        elif self._save_confirm_active:
            footer = "Save changes? (Y)es / (N)o / (Esc) cancel"
        else:
            if self._search_active:
                footer = "Type to filter | Esc exit search | Enter select"
            else:
                footer = (ui_config.modal_config or {}).get(
                    "footer", "enter to select • esc to close"
                )

        seg_width = width // 3
        remaining = width - (seg_width * 2)

        if self._save_confirm_active or self._save_target_active:
            # Save confirmation / target selection - single warning bar
            warning_color = T().warning[0] if hasattr(T(), "warning") else (200, 150, 0)
            top = solid_fg("▄" * width, warning_color)
            mid = solid(f"  {footer}".ljust(width), warning_color, T().text_dark, width)
            bot = solid_fg("▀" * width, warning_color)
        else:
            # Check if custom footer text is provided in modal definition
            custom_footer = (ui_config.modal_config or {}).get("footer")
            if custom_footer and custom_footer != "enter to select • esc to close":
                # Custom footer - single bar with primary color
                primary_color = (
                    T().primary[0] if hasattr(T(), "primary") else T().dark[0]
                )
                top = solid_fg("▄" * width, primary_color)
                mid = solid(
                    f"  {custom_footer}".ljust(width), primary_color, T().text, width
                )
                bot = solid_fg("▀" * width, primary_color)
            else:
                # Standard footer - three colored segments
                segments = [
                    (
                        T().success[0],
                        T().text_dark,
                        f" {C['success']} Enter: Save",
                        seg_width,
                    ),
                    (T().error[0], T().text, f" {C['error']} Esc: Cancel", seg_width),
                    (
                        T().secondary[0],
                        T().text_dark,
                        f" {C['triangle_right']} Tab: Next",
                        remaining,
                    ),
                ]
                top = "".join(solid_fg("▄" * w, bg) for bg, _, _, w in segments)
                mid = "".join(
                    solid(txt.ljust(w), bg, fg, w) for bg, fg, txt, w in segments
                )
                bot = "".join(solid_fg("▀" * w, bg) for bg, _, _, w in segments)
        lines.append(top)
        lines.append(mid)
        lines.append(bot)

        return lines

    def _render_modal_content(
        self, modal_config: dict, width: int, preserve_widgets: bool = False
    ) -> List[str]:
        """Render modal content with interactive widgets and scrolling.

        Args:
            modal_config: Modal configuration dict.
            width: Modal width.
            preserve_widgets: If True, preserve existing widget states instead of recreating.

        Returns:
            List of content lines with rendered widgets.
        """
        # Store config for scroll calculations (needed for non-selectable items)
        self._last_modal_config = modal_config

        all_lines = []  # All content lines before pagination
        widget_line_map = []  # Maps line index to widget index

        # Search filter bar (when active)
        if self._search_active:
            search_box = TagBox.render(
                lines=[f" {S.BOLD}Filter:{S.RESET_BOLD} {self._search_query}\u2588"],
                tag_bg=T().primary[0],

                tag_width=3,
                content_colors=T().dark[0],
                content_fg=T().text,
                content_width=width - 7,
                tag_chars=[" / "],
                use_gradient=False,
            )
            for line in search_box.split("\n"):
                all_lines.append(f"  {line}")
                widget_line_map.append(-1)
            all_lines.append(f"  {' ' * (width - 4)}")
            widget_line_map.append(-1)

        # Create or preserve widgets based on mode
        if not preserve_widgets:
            self.widgets = []
            self.focused_widget_index = 0
            self.scroll_offset = 0
            self.widgets = self._create_widgets(modal_config)
            if self.widgets:
                self.widgets[0].set_focus(True)
            # Reset command selection for command-style modals
            self.selected_command_index = 0

        # Rebuild command_items list. Selection clamped after rebuild
        # since filtered list may be shorter than current index.
        self.command_items = []
        self._command_line_positions = []
        self.has_command_sections = False

        # Build all content lines with widget indices
        widget_index = 0
        self._visible_widget_indices = []
        sections = modal_config.get("sections", [])

        for section_idx, section in enumerate(sections):
            section_title = section.get("title", "Section")
            section_widgets = section.get("widgets", [])

            # Search filtering: skip sections with no matching content
            _section_title_matches = False
            if self._search_query:
                query_lower = self._search_query.lower()
                _section_title_matches = query_lower in section_title.lower()
                has_any_match = _section_title_matches or any(
                    query_lower in w.get("label", "").lower()
                    or query_lower in w.get("help", "").lower()
                    or query_lower in w.get("config_path", "").lower()
                    for w in section_widgets
                )
                if not has_any_match:
                    section_commands = section.get("commands", [])
                    section_sessions = section.get("sessions", [])
                    has_cmd_match = any(
                        query_lower in c.get("name", "").lower()
                        or query_lower in c.get("description", "").lower()
                        for c in section_commands + section_sessions
                    )
                    if not has_cmd_match:
                        widget_index += len(section_widgets)
                        continue

            # Section header using TagBox
            header_output = TagBox.render(
                lines=[f" {S.BOLD}{section_title}{S.RESET_BOLD}"],
                tag_bg=T().primary[0],

                tag_width=3,
                content_colors=T().dark[0],
                content_fg=T().text,
                content_width=width - 7,
                tag_chars=[" ■ "],
                use_gradient=False,
            )
            for line in header_output.split("\n"):
                all_lines.append(f"  {line}")
                widget_line_map.append(-1)

            if section_widgets:
                num_widgets = len(section_widgets)
                for widget_idx_in_section, widget_config in enumerate(section_widgets):
                    # Skip non-matching widgets during search
                    if self._search_query and not _section_title_matches:
                        q = self._search_query.lower()
                        if not (
                            q in widget_config.get("label", "").lower()
                            or q in widget_config.get("help", "").lower()
                            or q in widget_config.get("config_path", "").lower()
                        ):
                            widget_index += 1
                            continue

                    if widget_index < len(self.widgets):
                        self._visible_widget_indices.append(widget_index)
                        widget = self.widgets[widget_index]

                        # Determine position for grouped rendering
                        if num_widgets == 1:
                            position = "only"
                        elif widget_idx_in_section == 0:
                            position = "first"
                        elif widget_idx_in_section == num_widgets - 1:
                            position = "last"
                        else:
                            position = "middle"

                        # Use render_modern for design system styling
                        if hasattr(widget, "render_modern"):
                            widget_output = widget.render_modern(
                                width=width - 4, position=position
                            )  # type: ignore[call-arg]
                            # render_modern returns list with multi-line string(s)
                            for output_block in widget_output:
                                for line in output_block.split("\n"):
                                    padded = f"  {line}"
                                    all_lines.append(padded)
                                    widget_line_map.append(widget_index)
                        else:
                            # Fallback for widgets without render_modern
                            widget_lines = widget.render()
                            for widget_line in widget_lines:
                                padded = f"  {widget_line.strip()}"
                                all_lines.append(padded)
                                widget_line_map.append(widget_index)

                        widget_index += 1

            # Handle "commands" format (used by help modal, skills modal, etc.)
            section_commands = section.get("commands", [])
            if section_commands and not section_widgets:
                self.has_command_sections = True

                # Filter commands by search query
                if self._search_query and not _section_title_matches:
                    query_lower = self._search_query.lower()
                    visible_commands = [
                        (i, cmd)
                        for i, cmd in enumerate(section_commands)
                        if query_lower in cmd.get("name", "").lower()
                        or query_lower in cmd.get("description", "").lower()
                    ]
                else:
                    visible_commands = list(enumerate(section_commands))

                num_visible = len(visible_commands)
                for vis_idx, (cmd_idx, cmd) in enumerate(visible_commands):
                    name = cmd.get("name", "")
                    description = cmd.get("description", "")
                    is_selectable = cmd.get("selectable", True)  # Default to selectable

                    # Determine position for grouped rendering
                    if num_visible == 1:
                        position = "only"
                    elif vis_idx == 0:
                        position = "first"
                    elif vis_idx == num_visible - 1:
                        position = "last"
                    else:
                        position = "middle"

                    # Truncate name and description for TagBox content
                    content_width = width - 10  # Account for tag and padding
                    max_name_len = min(22, content_width // 2)
                    max_desc_len = content_width - max_name_len - 2

                    if len(name) > max_name_len:
                        name = name[: max_name_len - 3] + "..."
                    if len(description) > max_desc_len:
                        description = description[: max_desc_len - 3] + "..."

                    if is_selectable:
                        # Track command item with its global index (only for selectable items)
                        global_cmd_idx = len(self.command_items)
                        self.command_items.append(cmd)
                        # Record the line position for accurate scroll tracking
                        self._command_line_positions.append(len(all_lines))

                        # Check if this item is selected
                        is_selected = global_cmd_idx == self.selected_command_index

                        # Render command as TagBox with checkbox-style indicator
                        # Check for loaded state (skills use this)
                        is_loaded = cmd.get("loaded", False)

                        if is_selected:
                            # Selected item - bright highlight
                            tag_bg = T().success[0]
                            tag_fg = T().text_dark
                            tag_char = " > "
                            content_colors = T().input_bg
                            content_fg = T().text
                        elif is_loaded:
                            # Loaded but not selected - subtle indicator
                            tag_bg = T().ai_tag
                            tag_fg = T().text_dark
                            tag_char = f" {C['success']} "
                            content_colors = T().dark[0]
                            content_fg = T().text_dim
                        else:
                            # Unloaded/unselected - dimmed
                            tag_bg = T().dark[0]
                            tag_fg = T().text_dim
                            tag_char = f" {C['check_off']} "
                            content_colors = T().dark[0]
                            content_fg = T().text_dim

                        cmd_box = TagBox.render(
                            lines=[f" {name:<{max_name_len}} {description}"],
                            tag_bg=tag_bg,
                            tag_fg=tag_fg,
                            tag_width=3,
                            content_colors=content_colors,
                            content_fg=content_fg,
                            content_width=width - 7,
                            tag_chars=[tag_char],
                            use_gradient=is_selected,
                            position=position,
                        )
                        for line in cmd_box.split("\n"):
                            all_lines.append(f"  {line}")
                        widget_line_map.append(-2)  # Command entry
                    else:
                        # Non-selectable info item - render dimmed with TagBox
                        cmd_box = TagBox.render(
                            lines=[f" {name:<{max_name_len}} {description}"],
                            tag_bg=T().dark[0],
                            tag_fg=T().text_dim,
                            tag_width=3,
                            content_colors=T().dark[0],
                            content_fg=T().text_dim,
                            content_width=width - 7,
                            tag_chars=["   "],
                            use_gradient=False,
                            position=position,
                        )
                        for line in cmd_box.split("\n"):
                            all_lines.append(f"  {line}")
                        widget_line_map.append(-4)  # Non-selectable info line

            # Also handle "sessions" format (used by resume modal, etc.)
            section_sessions = section.get("sessions", [])
            if section_sessions and not section_widgets and not section_commands:
                self.has_command_sections = True

                # Filter sessions by search query
                if self._search_query and not _section_title_matches:
                    query_lower = self._search_query.lower()
                    visible_sessions = [
                        (i, s)
                        for i, s in enumerate(section_sessions)
                        if query_lower in s.get("title", "").lower()
                        or query_lower in s.get("subtitle", "").lower()
                    ]
                else:
                    visible_sessions = list(enumerate(section_sessions))

                num_visible = len(visible_sessions)
                for vis_idx, (sess_idx, sess) in enumerate(visible_sessions):
                    # Determine position for grouped rendering
                    if num_visible == 1:
                        position = "only"
                    elif vis_idx == 0:
                        position = "first"
                    elif vis_idx == num_visible - 1:
                        position = "last"
                    else:
                        position = "middle"

                    # Track session item with its global index
                    global_sess_idx = len(self.command_items)
                    # Convert session format to command format for selection handling
                    cmd_item = {
                        "name": sess.get("title", sess.get("id", "Unknown")),
                        "description": sess.get("subtitle", ""),
                        "session_id": sess.get("id")
                        or sess.get("metadata", {}).get("session_id", ""),
                        "action": sess.get("action", "resume_session"),
                        "exit_mode": sess.get("exit_mode", "normal"),
                        "metadata": sess.get("metadata", {}),
                    }
                    self.command_items.append(cmd_item)

                    title = sess.get("title", sess.get("id", "Unknown"))
                    subtitle = sess.get("subtitle", "")

                    # Check if this item is selected
                    is_selected = global_sess_idx == self.selected_command_index

                    # Truncate title for TagBox content
                    content_width = width - 10
                    max_title_len = content_width - 4
                    if len(title) > max_title_len:
                        title = title[: max_title_len - 3] + "..."

                    # Build content with optional subtitle
                    content_lines = [f" {title}"]
                    tag_chars = [" > " if is_selected else f" {C['bullet']} "]
                    if subtitle:
                        max_sub_len = content_width - 4
                        if len(subtitle) > max_sub_len:
                            subtitle = subtitle[: max_sub_len - 3] + "..."
                        content_lines.append(f" {S.DIM}{subtitle}{S.RESET_DIM}")
                        tag_chars.append("   ")

                    if is_selected:
                        tag_bg = T().success[0]
                        tag_fg = T().text_dark
                        content_colors = T().input_bg
                        content_fg = T().text
                    else:
                        tag_bg = T().dark[0]
                        tag_fg = T().text_dim
                        content_colors = T().dark[0]
                        content_fg = T().text_dim

                    sess_box = TagBox.render(
                        lines=content_lines,
                        tag_bg=tag_bg,
                        tag_fg=tag_fg,
                        tag_width=3,
                        content_colors=content_colors,
                        content_fg=content_fg,
                        content_width=width - 7,
                        tag_chars=tag_chars,
                        use_gradient=is_selected,
                        position=position,
                    )
                    for line in sess_box.split("\n"):
                        all_lines.append(f"  {line}")
                    widget_line_map.append(-2)  # Session entry

            # Add blank line after each section (except the last one)
            if section_idx < len(sections) - 1:
                all_lines.append(f"  {' ' * (width-4)}")
                widget_line_map.append(-1)  # Blank line, no widget

        # Auto-scroll to keep focused widget visible
        if self.widgets:
            focused_lines = [
                i
                for i, w in enumerate(widget_line_map)
                if w == self.focused_widget_index
            ]
            if focused_lines:
                first_line = focused_lines[0]
                last_line = focused_lines[
                    -1
                ]  # Last line of the widget (handles multi-line widgets like expanded dropdowns)

                # DEBUG: Log scroll calculation
                logger.debug(
                    f"Auto-scroll: widget={self.focused_widget_index}, first_line={first_line}, last_line={last_line}, "
                    f"scroll_offset={self.scroll_offset}, visible_height={self.visible_height}, "
                    f"total_lines={len(all_lines)}, widget_lines={len(focused_lines)}"
                )

                # When scrolling up, include section header
                if first_line < self.scroll_offset:
                    # If focusing first widget (wrap-around), scroll to top to show header
                    if self.focused_widget_index == 0:
                        self.scroll_offset = 0
                    else:
                        # Look for section header above the focused widget
                        section_header_line = first_line
                        for i in range(first_line - 1, -1, -1):
                            if widget_line_map[i] == -1:  # Header or blank line
                                section_header_line = i
                            else:
                                break  # Found another widget, stop
                        self.scroll_offset = section_header_line
                elif last_line >= self.scroll_offset + self.visible_height:
                    # Widget extends beyond visible area - scroll to show all of it
                    # Use last_line instead of first_line to handle multi-line widgets
                    new_scroll_offset = last_line - self.visible_height + 1
                    logger.debug(
                        f"Adjusting scroll: {self.scroll_offset} -> {new_scroll_offset} (last_line={last_line})"
                    )
                    self.scroll_offset = new_scroll_offset

        # Clamp selected command index to valid range after filtering
        if self.command_items:
            if self.selected_command_index >= len(self.command_items):
                self.selected_command_index = 0
                self.scroll_offset = 0
        elif self.has_command_sections:
            self.selected_command_index = 0
            self.scroll_offset = 0

        # Apply scroll offset and return visible lines
        total_lines = len(all_lines)

        # Clamp scroll offset to valid range (fixes wrap-around from first to last item)
        max_scroll = max(0, total_lines - self.visible_height)
        self.scroll_offset = max(0, min(self.scroll_offset, max_scroll))

        end_offset = min(self.scroll_offset + self.visible_height, total_lines)
        visible_lines = all_lines[self.scroll_offset : end_offset]

        # Pad to fixed height (visible_height) to prevent height changes when scrolling
        while len(visible_lines) < self.visible_height:
            visible_lines.append(f"  {' ' * (width-4)}")

        # Add scroll indicator if needed (always at the same position)
        if total_lines > self.visible_height:
            scroll_info = f" [{self.scroll_offset + 1}-{end_offset}/{total_lines}] "
            if self.scroll_offset > 0:
                scroll_info = f"{C['arrow_up']}{scroll_info}"
            if end_offset < total_lines:
                scroll_info = f"{scroll_info}{C['arrow_down']}"
            visible_lines.append(f"  {S.DIM}{scroll_info.center(width-4)}{S.RESET_DIM}")
        else:
            # Add empty indicator line to maintain fixed height
            visible_lines.append(f"  {' ' * (width-4)}")

        return visible_lines

    async def _animate_entrance(self, lines: List[str]):
        """Render modal cleanly without stacking animation.

        Args:
            lines: Modal lines to render.
        """
        try:
            # Single clean render without animation to prevent stacking
            await self._render_modal_lines(lines)
        except Exception as e:
            logger.error(f"Error rendering modal: {e}")
            # Single fallback render only
            await self._render_modal_lines(lines)

    async def _render_modal_lines(self, lines: List[str]):
        """Render modal lines using TRUE overlay system (no chat pipeline).

        Args:
            lines: Lines to render.
        """
        try:
            # FIXED: Use overlay rendering system instead of chat pipeline
            # This completely bypasses write_message() and conversation buffers

            # Create modal layout configuration
            # Use dynamic modal width (terminal_width - 2)
            width = self.modal_width
            height = len(lines)

            # For full-width modals, don't center - position at top-left with minimal margin
            # Check if this is a "full screen" modal (width close to terminal width)
            terminal_width, _ = get_terminal_size()
            is_full_width = width >= terminal_width - 4

            if is_full_width:
                # Full-width modal: position at top with 1-char left margin
                layout = ModalLayout(
                    width=width,
                    height=height + 2,
                    start_row=1,  # Start near top
                    start_col=1,  # 1-char left margin
                    center_horizontal=False,
                    center_vertical=False,
                    padding=0,
                    border_style="box",
                )
            else:
                # Smaller modal: center it
                layout = ModalLayout(
                    width=width,
                    height=height + 2,
                    center_horizontal=True,
                    center_vertical=True,
                    padding=2,
                    border_style="box",
                )

            # Prepare modal display with state isolation
            if self.state_manager:
                prepare_result = self.state_manager.prepare_modal_display(
                    layout, ModalDisplayMode.OVERLAY
                )
                if not prepare_result:
                    logger.error("Failed to prepare modal display")
                    return

                # Render modal content using direct terminal output (bypassing chat)
                render_result = self.state_manager.render_modal_content(lines)
                if not render_result:
                    logger.error("Failed to render modal content")
                    return

                logger.info(f"Modal rendered via overlay system: {len(lines)} lines")
            else:
                # Fallback to basic display for testing
                logger.warning(
                    "Modal overlay system not available - using fallback display"
                )
                for line in lines:
                    print(line)

        except Exception as e:
            logger.error(f"Error rendering modal via overlay system: {e}")
            # Ensure state is cleaned up on error
            if self.state_manager:
                self.state_manager.restore_terminal_state()

    def _create_widgets(self, modal_config: dict) -> List[BaseWidget]:
        """Create widgets from modal configuration.

        Args:
            modal_config: Modal configuration dictionary.

        Returns:
            List of instantiated widgets.
        """

        widgets = []
        sections = modal_config.get("sections", [])

        for section_idx, section in enumerate(sections):
            section_widgets = section.get("widgets", [])

            for widget_idx, widget_config in enumerate(section_widgets):
                try:
                    widget = self._create_widget(widget_config)
                    widgets.append(widget)
                except Exception as e:
                    logger.error(
                        f"FAILED to create widget {widget_idx} in section {section_idx}: {e}"
                    )
                    logger.error(f"Widget config that failed: {widget_config}")
                    import traceback

                    logger.error(f"Full traceback: {traceback.format_exc()}")

        return widgets

    def _create_widget(self, config: dict) -> BaseWidget:
        """Create a single widget from configuration.

        Args:
            config: Widget configuration dictionary.

        Returns:
            Instantiated widget.

        Raises:
            ValueError: If widget type is unknown.
        """

        try:
            widget_type = config["type"]
        except KeyError as e:
            logger.error(f"Widget config missing 'type' field: {e}")
            raise ValueError(f"Widget config missing required 'type' field: {config}")

        # Support both "config_path" (for config-bound widgets) and "field" (for form modals)
        config_path = config.get("config_path") or config.get(
            "field", "kollabor.ui.unknown"
        )

        # Get current value from config service if available
        current_value = None
        if self.config_service:
            current_value = self.config_service.get(config_path)
        else:
            pass

        # Create widget config with current value
        widget_config = config.copy()
        if current_value is not None:
            widget_config["current_value"] = current_value

        try:
            if widget_type == "checkbox":
                widget: BaseWidget = CheckboxWidget(
                    widget_config, config_path, self.config_service
                )
                return widget
            elif widget_type == "dropdown":
                widget = DropdownWidget(widget_config, config_path, self.config_service)
                return widget
            elif widget_type == "text_input":
                widget = TextInputWidget(
                    widget_config, config_path, self.config_service
                )
                return widget
            elif widget_type == "slider":
                widget = SliderWidget(widget_config, config_path, self.config_service)
                return widget
            elif widget_type == "label":
                # Label widgets use "value" directly, not config_path
                label_text = config.get("label", "")
                value_text = config.get("value", "")
                help_text = config.get("help", "")
                widget = LabelWidget(
                    label=label_text,
                    value=value_text,
                    help_text=help_text,
                    config_path=config_path,
                    current_value=value_text,
                )
                return widget
            else:
                error_msg = f"Unknown widget type: {widget_type}"
                logger.error(f"{error_msg}")
                raise ValueError(error_msg)
        except Exception as e:
            logger.error(
                f"FATAL: Widget constructor failed for type '{widget_type}': {e}"
            )
            logger.error(f"Widget config that caused failure: {widget_config}")
            import traceback

            logger.error(f"Full constructor traceback: {traceback.format_exc()}")
            raise

    def _handle_widget_navigation(self, key_press: KeyPress) -> bool:
        """Handle widget focus navigation.

        Args:
            key_press: Key press event.

        Returns:
            True if navigation was handled.
        """
        # Handle command-style modal navigation (no widgets, just command items)
        if self.has_command_sections and self.command_items and not self.widgets:
            total_content_lines = self._estimate_total_content_lines()
            max_scroll = max(0, total_content_lines - self.visible_height)
            last_idx = len(self.command_items) - 1
            page_items = max(1, self.visible_height // 3)  # ~items per visible page

            if key_press.name == "ArrowDown":
                if self.selected_command_index < last_idx:
                    self.selected_command_index += 1
                else:
                    self.selected_command_index = 0
                    self.scroll_offset = 0
                    return True
            elif key_press.name == "ArrowUp":
                if self.selected_command_index > 0:
                    self.selected_command_index -= 1
                else:
                    self.selected_command_index = last_idx
                    self.scroll_offset = max_scroll
                    return True
            elif key_press.name == "Tab" or key_press.name == "PageDown":
                # Page down by visible page worth of items
                self.selected_command_index = min(
                    self.selected_command_index + page_items, last_idx
                )
            elif key_press.name == "Shift+Tab" or key_press.name == "PageUp":
                # Page up by visible page worth of items
                self.selected_command_index = max(
                    self.selected_command_index - page_items, 0
                )
            else:
                return False

            # Use real line positions for accurate scroll tracking
            self._scroll_to_selected_command(max_scroll)
            return True

        if not self.widgets:
            return False

        # CRITICAL FIX: Check if focused widget is expanded before handling navigation
        # If a dropdown is expanded, let it handle its own ArrowDown/ArrowUp
        focused_widget = self.widgets[self.focused_widget_index]
        if hasattr(focused_widget, "_expanded") and focused_widget._expanded:
            # Widget is expanded - don't intercept arrow keys
            if key_press.name in ["ArrowDown", "ArrowUp"]:
                return False  # Let widget handle its own navigation

        # When search filter is active, only navigate visible widgets
        nav_indices = (
            self._visible_widget_indices
            if self._search_query and self._visible_widget_indices
            else list(range(len(self.widgets)))
        )

        if not nav_indices:
            return False

        # Find current position in nav_indices
        try:
            current_pos = nav_indices.index(self.focused_widget_index)
        except ValueError:
            current_pos = 0
            self.widgets[self.focused_widget_index].set_focus(False)
            self.focused_widget_index = nav_indices[0]
            self.widgets[self.focused_widget_index].set_focus(True)
            return True

        if key_press.name == "Tab" or key_press.name == "ArrowDown":
            self.widgets[self.focused_widget_index].set_focus(False)
            next_pos = (current_pos + 1) % len(nav_indices)
            self.focused_widget_index = nav_indices[next_pos]
            self.widgets[self.focused_widget_index].set_focus(True)
            return True

        elif key_press.name == "ArrowUp" or key_press.name == "Shift+Tab":
            self.widgets[self.focused_widget_index].set_focus(False)
            prev_pos = (current_pos - 1) % len(nav_indices)
            self.focused_widget_index = nav_indices[prev_pos]
            self.widgets[self.focused_widget_index].set_focus(True)
            return True

        elif key_press.name == "PageDown":
            self.widgets[self.focused_widget_index].set_focus(False)
            next_pos = min(current_pos + self.visible_height, len(nav_indices) - 1)
            self.focused_widget_index = nav_indices[next_pos]
            self.widgets[self.focused_widget_index].set_focus(True)
            return True

        elif key_press.name == "PageUp":
            self.widgets[self.focused_widget_index].set_focus(False)
            prev_pos = max(current_pos - self.visible_height, 0)
            self.focused_widget_index = nav_indices[prev_pos]
            self.widgets[self.focused_widget_index].set_focus(True)
            return True

        return False

    def _handle_widget_input(self, key_press: KeyPress) -> bool:
        """Route input to focused widget.

        Args:
            key_press: Key press event.

        Returns:
            True if input was handled by a widget.
        """
        # Handle Enter key for command-style modals
        logger.info(
            f"🔧 _handle_widget_input: has_command_sections={self.has_command_sections}, "
            f"command_items={len(self.command_items) if self.command_items else 0}, "
            f"widgets={len(self.widgets) if self.widgets else 0}"
        )
        if self.has_command_sections and self.command_items and not self.widgets:
            # Check for action key shortcuts (e, d, etc.) defined in modal actions
            if hasattr(self, "_last_modal_config") and self._last_modal_config:
                actions = self._last_modal_config.get("actions", [])
                for action_def in actions:
                    action_key = action_def.get("key", "")
                    action_name = action_def.get("action", "")
                    # Match single-char keys or special keys like Enter/Escape
                    if (len(action_key) == 1 and key_press.char == action_key) or (
                        key_press.name == action_key
                    ):
                        # Get currently selected command item
                        selected_cmd = self.get_selected_command()
                        if selected_cmd:
                            # CRITICAL FIX: If action is "select" or "toggle", use the command item's actual action
                            # This allows Enter to trigger different actions based on the selected item
                            # (e.g., "select_agent" for agents, "create_agent_prompt" for create button)
                            # Skills modal uses "toggle", agents/profiles use "select"
                            if action_name in ("select", "toggle"):
                                # Use the command item's action directly (don't override with "select")
                                actual_action = selected_cmd.get("action")
                                if actual_action:
                                    # Preserve ALL fields from the selected command (including target_path, etc.)
                                    self._triggered_action = dict(selected_cmd)
                                    # Ensure action is set (override the action key with the command's actual action)
                                    self._triggered_action["action"] = actual_action
                                    self._command_selected = True
                                    logger.info(
                                        f"🎯 Select action using command item action: {action_key} -> {actual_action}"
                                    )
                                    return True
                            else:
                                # For specific actions (e, d, etc.), use the action from the key
                                # Preserve ALL fields from selected command (including target_path, etc.)
                                self._triggered_action = dict(selected_cmd)
                                self._triggered_action["action"] = action_name
                                self._command_selected = True
                                logger.info(
                                    f"🎯 Action key triggered: {action_key} -> {action_name}"
                                )
                                return True

            if key_press.name == "Enter" or key_press.char == "\r":
                # Mark that a command was selected
                self._command_selected = True
                logger.info(
                    f"🎯 Command selected! _command_selected={self._command_selected}"
                )
                return True
            return False

        if not self.widgets or self.focused_widget_index >= len(self.widgets):
            return False

        focused_widget = self.widgets[self.focused_widget_index]

        result: bool = focused_widget.handle_input(key_press)
        return result

    def get_selected_command(self) -> Optional[Dict]:
        """Get the currently selected command item.

        Returns:
            Selected command dict or None.
        """
        # If an action key was triggered (e, d, etc.), return that action
        if self._triggered_action:
            return dict(self._triggered_action)  # type: ignore[no-any-return]

        # Otherwise return the normally selected command
        if self.has_command_sections and self.command_items:
            if 0 <= self.selected_command_index < len(self.command_items):
                return self.command_items[self.selected_command_index]
        return None

    def was_command_selected(self) -> bool:
        """Check if a command was selected via Enter.

        Returns:
            True if a command was selected.
        """
        return getattr(self, "_command_selected", False)

    def _get_widget_values(self) -> Dict[str, Any]:
        """Get all widget values for saving.

        Returns:
            Dictionary mapping config paths to values.
        """
        values = {}
        for widget in self.widgets:
            if widget.has_pending_changes():
                values[widget.config_path] = widget.get_pending_value()
        return values

    def _reset_widget_focus(self):
        """Reset widget focus to first widget."""
        if self.widgets:
            for widget in self.widgets:
                widget.set_focus(False)
            self.focused_widget_index = 0
            self.widgets[0].set_focus(True)

    def _create_gradient_header(self, title: str) -> str:
        """Create a header text with bold styling.

        Args:
            title: Section title text.

        Returns:
            Formatted title with bold effect.
        """
        if not title:
            return ""

        return f"{S.BOLD}{title}{S.RESET_BOLD}"

    def _strip_ansi(self, text: str) -> str:
        """Remove ANSI escape codes from text.

        Args:
            text: Text with potential ANSI codes.

        Returns:
            Text with ANSI codes removed.
        """
        return re.sub(r"\033\[[0-9;]*m", "", text)

    def _scroll_to_selected_command(self, max_scroll: int) -> None:
        """Scroll to keep the selected command item visible.

        Uses real line positions recorded during rendering when available,
        falls back to estimation otherwise.
        """
        idx = self.selected_command_index
        if self._command_line_positions and idx < len(self._command_line_positions):
            selected_line = self._command_line_positions[idx]
        else:
            selected_line = idx * 3  # fallback: 3 lines per TagBox item

        # Keep selection vertically centered when possible
        center_offset = max(0, selected_line - self.visible_height // 3)
        self.scroll_offset = min(center_offset, max_scroll)

        # Ensure selection is within the visible window
        if selected_line < self.scroll_offset:
            self.scroll_offset = max(0, selected_line - 1)
        elif selected_line >= self.scroll_offset + self.visible_height - 3:
            self.scroll_offset = min(
                max_scroll, selected_line - self.visible_height + 4
            )

    def _estimate_total_content_lines(self) -> int:
        """Estimate total content lines including non-selectable items.

        Used for scroll calculations when there are non-selectable items
        at the end of the modal content.

        Returns:
            Estimated total number of content lines.
        """
        if not hasattr(self, "_last_modal_config") or not self._last_modal_config:
            # Fallback: use command items count * 2 (approximate)
            return len(self.command_items) * 2 if self.command_items else 0

        total_lines = 0
        sections = self._last_modal_config.get("sections", [])

        for section in sections:
            # Section header with borders (▄▄▄ + title + ▀▀▀ = ~3-4 lines)
            if section.get("title"):
                total_lines += 4

            # Content box borders (▄▄▄ top + ▀▀▀ bottom = 2 lines)
            total_lines += 2

            # Count all commands (selectable and non-selectable)
            commands = section.get("commands", [])
            total_lines += len(commands)

            # Count sessions
            sessions = section.get("sessions", [])
            total_lines += len(sessions)

            # Blank line between sections
            total_lines += 1

        # Add extra buffer for any additional spacing
        total_lines += 5

        return total_lines

    def _pad_line_with_ansi(self, line: str, target_width: int) -> str:
        """Pad line to target width, accounting for ANSI escape codes.

        Args:
            line: Line that may contain ANSI codes.
            target_width: Target visible width.

        Returns:
            Line padded to target visible width.
        """
        visible_length = len(self._strip_ansi(line))
        padding_needed = max(0, target_width - visible_length)
        return line + " " * padding_needed

    async def _handle_modal_input(self, ui_config: UIConfig) -> Dict[str, Any]:
        """Handle modal input with persistent event loop for widget interaction.

        Args:
            ui_config: Modal configuration.

        Returns:
            Modal completion result when user exits.
        """
        # Store ui_config for refresh operations
        self.current_ui_config = ui_config

        # Modal is now active and waiting for input
        # Input handling happens through input_handler._handle_modal_keypress()
        # which calls our widget methods and refreshes display

        # The modal stays open until input_handler calls one of:
        # - _exit_modal_mode() (Escape key)
        # - _save_and_exit_modal() (Enter key or save action)

        # This method completes when the modal is closed externally
        # Return success with widget information
        return {
            "success": True,
            "action": "modal_interactive",
            "widgets_enabled": True,
            "widget_count": len(self.widgets),
            "widgets_created": [w.__class__.__name__ for w in self.widgets],
        }
