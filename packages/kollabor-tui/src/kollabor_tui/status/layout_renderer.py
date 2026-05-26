"""Status layout renderer for the Kollab status area.

Renders the status area based on the configured layout, using
widgets from the registry.
"""

import logging
from typing import Any, Dict, List, Optional

from kollabor_tui.design_system import T, solid, solid_fg
from kollabor_tui.terminal_state import get_global_width
from kollabor_tui.visual_effects import PulseEffect, ShimmerEffect, UltraShimmerEffect

from .core_widgets import WidgetContext
from .layout_manager import RowConfig, StatusLayoutManager, WidgetConfig
from .navigation_state import NavigationMode, SelectionType
from .utils import fg as _fg
from .utils import strip_ansi as _strip_ansi
from .widget_registry import StatusWidgetRegistry, WidthType

# Maps widget_id -> (effect_name, effect_instance)
# Keyed by widget_id so changing effects auto-replaces the old entry.
_widget_effects: Dict[str, tuple] = {}


def _get_widget_effect(widget_id: str, effect_name: str):
    """Get or create effect instance for a widget.

    Maintains effect state (animation position) across renders.
    Keyed by widget_id alone so changing a widget's effect replaces
    the old entry instead of orphaning it.

    Args:
        widget_id: Unique widget identifier
        effect_name: Effect type (shimmer, pulse, ultra)

    Returns:
        Effect instance or None for 'none'
    """
    if effect_name == "none":
        _widget_effects.pop(widget_id, None)
        return None

    cached = _widget_effects.get(widget_id)
    if cached and cached[0] == effect_name:
        return cached[1]

    # Create new effect (replaces any previous effect for this widget)
    effect: UltraShimmerEffect | ShimmerEffect | PulseEffect | None = None
    if effect_name == "ultra":
        effect = UltraShimmerEffect()
    elif effect_name == "shimmer":
        effect = ShimmerEffect(speed=3, wave_width=2)
    elif effect_name == "pulse":
        effect = PulseEffect(speed=4, pulse_width=2)

    if effect:
        _widget_effects[widget_id] = (effect_name, effect)

    return effect


logger = logging.getLogger(__name__)


def _bg_line(text: str, bg: tuple[int, int, int], width: int) -> str:
    """Apply background to entire line, preserving foreground colors.

    Args:
        text: Text that may contain ANSI foreground codes
        bg: RGB tuple for background
        width: Total width to pad to

    Returns:
        Text with background applied
    """
    r, g, b = bg
    visible_len = len(_strip_ansi(text))
    padding = max(0, width - visible_len)
    return f"\033[48;2;{r};{g};{b}m{text}{' ' * padding}\033[0m"


def _get_widget_bg_color(color_name: str) -> Optional[tuple]:
    """Get widget background color RGB tuple from color name.

    Args:
        color_name: Color name ("none", "dark0", "dark1", "primary0", "secondary0")

    Returns:
        RGB tuple or None for transparent (no background)
    """
    color_map = {
        "none": None,
        "dark0": T().dark[0],
        "dark1": T().dark[1],
        "primary0": T().primary[0],
        "secondary0": T().secondary[0],
    }
    result = color_map.get(color_name, None)
    logger.debug(f"[COLOR] _get_widget_bg_color('{color_name}') -> {result}")
    return result


def _apply_bg_color(text: str, bg: tuple, add_leading_space: bool = False) -> str:
    """Apply background color to text with padding.

    Always adds internal padding (leading + trailing space) for visual consistency.
    The add_leading_space flag adds EXTRA separator space before the widget
    for spacing between widgets.

    Resets to row's dark background at end (not terminal default) to prevent
    color bleeding while maintaining row consistency.

    On bright backgrounds, replaces foreground colors with a dark text color
    so text remains readable regardless of theme.

    Args:
        text: Text that may contain ANSI foreground codes
        bg: RGB tuple for background
        add_leading_space: If True, add extra separator space before widget

    Returns:
        Text with background and padding applied, reset to dark at end
    """
    r, g, b = bg
    dr, dg, db = T().dark[0]

    # Always include internal padding (space before and after text)
    # add_leading_space adds EXTRA separator with dark bg between widgets
    if add_leading_space:
        # Extra dark separator space + colored content with internal padding
        return f"\033[48;2;{dr};{dg};{db}m \033[48;2;{r};{g};{b}m {text} \033[48;2;{dr};{dg};{db}m"
    else:
        # Just colored content with internal padding (first widget)
        return f"\033[48;2;{r};{g};{b}m {text} \033[48;2;{dr};{dg};{db}m"


def render_selected_widget(text: str, width: int) -> str:
    """Render widget with solid block selection highlight.

    Uses solid block style (▄▀) NOT ASCII box-drawing.
    Highlights selected widget in navigation mode with primary color background.

    Args:
        text: Widget text content
        width: Total width for selection highlight

    Returns:
        Rendered widget with top border, highlighted content, and bottom border
    """
    # Top border (solid block line)
    top = solid_fg("▄" * width, T().primary[0])

    # Content with solid background
    content = solid(f" {text:<{width-2}} ", T().primary[0], T().text_dark, width)

    # Bottom border (solid block line)
    bottom = solid_fg("▀" * width, T().primary[0])

    return f"{top}\n{content}\n{bottom}"


def render_mode_indicator(nav_state) -> str:
    """Render mode indicator using design system.

    Shows current mode (INPUT/STATUS_FOCUS/EDIT) with appropriate colors.
    Always visible to avoid confusion about current mode.

    Args:
        nav_state: StatusNavigationState with active and interaction_active flags

    Returns:
        Rendered mode indicator string
    """
    # Check navigation mode
    mode = nav_state.mode if hasattr(nav_state, "mode") else None
    if mode == NavigationMode.EDIT:
        mode_text = " EDIT "
        bg, fg = T().primary[0], T().text_dark
    elif mode == NavigationMode.STATUS_FOCUS:
        mode_text = " NAVIGATE "
        bg, fg = T().dark[0], T().text_dim
    elif nav_state.active:
        if nav_state.interaction_active:
            mode_text = " INTERACT "
            bg, fg = T().warning[0], T().text_dark
        else:
            mode_text = " NAVIGATE "
            bg, fg = T().ai_tag, T().text_dark
    else:
        mode_text = " INPUT "
        bg, fg = T().dark[0], T().text

    return str(solid(mode_text, bg, fg, len(mode_text)))


class StatusLayoutRenderer:
    """Renders the status area based on layout configuration.

    Uses the widget registry to render individual widgets and
    arranges them according to the layout configuration.
    """

    def __init__(
        self,
        widget_registry: StatusWidgetRegistry,
        layout_manager: StatusLayoutManager,
        terminal_width: int = 76,
        navigation_manager: Optional[Any] = None,
    ):
        """Initialize the layout renderer.

        Args:
            widget_registry: Registry of available widgets
            layout_manager: Manager for layout configuration
            terminal_width: Terminal width for layout calculations
            navigation_manager: Optional navigation manager for interactive mode
        """
        self._widget_registry = widget_registry
        self._layout_manager = layout_manager
        self._terminal_width = terminal_width
        self._context: Optional[WidgetContext] = None
        self._navigation_manager = navigation_manager
        self.simple_mode: bool = False

        logger.info("StatusLayoutRenderer initialized")

    def set_terminal_width(self, width: int) -> None:
        """Update terminal width for layout calculations."""
        self._terminal_width = width

    def set_context(self, context: WidgetContext) -> None:
        """Set the widget context for rendering.

        Args:
            context: WidgetContext with services needed for rendering
        """
        self._context = context
        self._widget_registry.set_context(context)

    def set_navigation_manager(self, navigation_manager: Any) -> None:
        """Set the navigation manager for interactive mode.

        Args:
            navigation_manager: Navigation manager instance
        """
        self._navigation_manager = navigation_manager

    def render(self) -> List[str]:
        """Render the complete status area.

        Returns:
            List of rendered status rows.
        """
        layout = self._layout_manager.get_layout()

        # Check if in edit mode
        is_edit_mode = False
        nav_mode = NavigationMode.INPUT
        if self._navigation_manager and hasattr(self._navigation_manager, "state"):
            nav_state = self._navigation_manager.state
            nav_mode = (
                nav_state.mode if hasattr(nav_state, "mode") else NavigationMode.INPUT
            )
            is_edit_mode = nav_mode == NavigationMode.EDIT

        # In edit mode, show all 6 rows. Otherwise, show only visible rows.
        if is_edit_mode:
            # Row IDs are 1-6, not 0-5
            all_rows = [layout.get_row(i) for i in range(1, 7)]
            rows_to_render = [r for r in all_rows if r is not None]
        else:
            rows_to_render = layout.get_visible_rows()

        if not rows_to_render:
            return []

        # Constrain width
        width = get_global_width()

        lines = []

        # Check if in simple mode (no borders/colors)
        simple_mode = getattr(self, "simple_mode", False)

        # Add mode indicator if navigation is active (skip in simple mode)
        if (
            not simple_mode
            and self._navigation_manager
            and hasattr(self._navigation_manager, "state")
        ):
            nav_state = self._navigation_manager.state
            if nav_state.active:
                mode_line = render_mode_indicator(nav_state)
                # Add help hints after mode indicator
                if is_edit_mode:
                    help_text = _fg(
                        "  ←→↑↓:nav  Enter:add  d:del  c:color  Esc:exit", T().text_dim
                    )
                else:
                    help_text = _fg(
                        "  ←→↑↓:move  Enter:act  e:edit  Esc:exit", T().text_dim
                    )
                combined = f" {mode_line}{help_text}"
                visible_len = len(_strip_ansi(combined))
                padding = max(0, width - visible_len)
                mode_display = _bg_line(
                    f"{combined}{' ' * padding}", T().dark[0], width
                )
                lines.append(mode_display)

        # Render each row
        for row_idx, row in enumerate(rows_to_render):
            if is_edit_mode:
                row_content = self._render_edit_row(row, width, row_idx)
            else:
                row_content = self._render_row(
                    row, width, row_idx, simple_mode=simple_mode
                )

            if row_content:  # Only add non-empty rows
                lines.append(row_content)

        return lines

    def _render_row(
        self,
        row: RowConfig,
        total_width: int,
        row_idx: int = 0,
        simple_mode: bool = False,
    ) -> str:
        """Render a single row of widgets with smart width allocation.

        Uses two-pass rendering:
        1. First pass: render with generous width to get natural sizes
        2. If overflow: allocate widths proportionally and re-render

        Args:
            row: Row configuration
            total_width: Total available width
            row_idx: Row index for selection checking
            simple_mode: If True, render without backgrounds/colors

        Returns:
            Rendered row string with gutter and background (or plain text in simple mode)
        """
        # Gutter takes 1 character, leading space takes 1
        gutter_width = 1
        content_width = total_width - gutter_width - 1  # -1 for leading space

        # Check if navigation is active and get selected position
        selected_row, selected_widget_idx = -1, -1
        nav_active = False
        if self._navigation_manager and hasattr(self._navigation_manager, "state"):
            nav_state = self._navigation_manager.state
            if nav_state.active:
                nav_active = True
                selected_row = nav_state.selected_row
                selected_widget_idx = nav_state.selected_widget_index

        # Handle empty rows - show placeholder only in navigation mode
        if not row.widgets:
            if nav_active:
                return self._render_empty_row_placeholder(
                    row, total_width, row_idx, selected_row
                )
            return ""

        # Filter to non-empty widgets first
        active_widgets = []
        for widget_idx, widget_config in enumerate(row.widgets):
            widget = self._widget_registry.get(widget_config.id)
            if widget:
                # Quick render to check if widget has content
                try:
                    test_render = widget.render(100, self._context)
                    if test_render and _strip_ansi(test_render).strip():
                        active_widgets.append((widget_idx, widget_config, widget))
                except Exception as e:
                    logger.debug(f"Widget render test failed, skipping: {e}")

        if not active_widgets:
            if nav_active:
                return self._render_empty_row_placeholder(
                    row, total_width, row_idx, selected_row
                )
            return ""

        # Calculate spacing overhead: "  " between widgets
        num_widgets = len(active_widgets)
        spacing_overhead = (num_widgets - 1) * 2 if num_widgets > 1 else 0
        available_for_widgets = content_width - spacing_overhead

        # First pass: get natural sizes (render with large width)
        natural_sizes = []
        for _, widget_config, widget in active_widgets:
            rendered = widget.render(200, self._context)  # Large width for natural size
            natural_size = len(_strip_ansi(rendered))
            natural_sizes.append(natural_size)

        total_natural = sum(natural_sizes)

        # Determine allocated widths
        if total_natural <= available_for_widgets:
            # Everything fits naturally
            allocated_widths = natural_sizes
        else:
            # Need to compress - allocate proportionally with minimum widths
            allocated_widths = self._allocate_widths(
                active_widgets, natural_sizes, available_for_widgets
            )

        # Second pass: render with allocated widths
        rendered_widgets = []
        for i, (widget_idx, widget_config, widget) in enumerate(active_widgets):
            is_selected = row_idx == selected_row and widget_idx == selected_widget_idx
            alloc_width = allocated_widths[i]

            # Render widget with allocated width
            # Add leading space for all widgets except the first
            add_leading = i > 0
            widget_text = self._render_widget_with_width(
                widget_config, widget, alloc_width, is_selected, add_leading
            )
            if widget_text:
                rendered_widgets.append(
                    (widget_text, widget_config)
                )  # Keep config to check for background

        if not rendered_widgets:
            return ""

        # Join widgets with proper spacing
        # All widgets (except first) get leading space - either from background or standard spacing
        content_parts = []
        for i, (widget_text, widget_config) in enumerate(rendered_widgets):
            if i == 0:
                # First widget - no leading space needed
                content_parts.append(widget_text)
            else:
                # Subsequent widgets - add widget_text (which may have its own spacing)
                content_parts.append(widget_text)

        content = "".join(content_parts)

        # Simple mode: return plain text without gutter or backgrounds
        if simple_mode:
            # Strip ANSI codes and return plain text
            return _strip_ansi(content)

        # Build line: gutter + space + content
        # Check if any widget has a background color
        has_colored_widgets = any(
            _get_widget_bg_color(w.color) is not None for _, w, _ in active_widgets
        )

        gutter = str(solid(" ", T().dark[0], T().text_dim, gutter_width))
        if has_colored_widgets:
            # Don't apply row background - let widget colors show through
            # But DO reset background at the end to clear any widget backgrounds
            visible_len = len(_strip_ansi(content))
            # Account for leading space in padding calculation
            padding = max(0, (total_width - gutter_width) - visible_len - 1)
            # Apply dark background to leading space and padding, reset at end
            dr, dg, db = T().dark[0]
            dark_bg = f"\033[48;2;{dr};{dg};{db}m"
            content_line = f"{dark_bg} {content}{' ' * padding}\033[0m"
        else:
            # Apply standard dark background when no widgets have custom colors
            content_line = _bg_line(
                " " + content, T().dark[0], total_width - gutter_width
            )

        return gutter + content_line

    def _render_empty_row_placeholder(
        self, row: RowConfig, total_width: int, row_idx: int, selected_row: int
    ) -> str:
        """Render placeholder for empty row in navigation mode.

        Shows a dimmed indicator so users can see and select empty rows.

        Args:
            row: Row configuration
            total_width: Total available width
            row_idx: Row index for selection checking
            selected_row: Currently selected row index

        Returns:
            Rendered placeholder string
        """
        gutter_width = 1
        is_selected = row_idx == selected_row

        # Empty row placeholder text
        placeholder = _fg(f"(empty row {row.id})", T().text_dim)

        if is_selected:
            # Highlight when selected
            content = f" {placeholder}"
            content = f"\033[4m{content}\033[24m"  # Underline
        else:
            content = f" {placeholder}"

        # Build line with gutter and background
        gutter = str(solid(" ", T().dark[0], T().text_dim, gutter_width))
        content_line = _bg_line(content, T().dark[0], total_width - gutter_width)

        return gutter + content_line

    def _allocate_widths(
        self, active_widgets: list, natural_sizes: list, available: int
    ) -> list:
        """Allocate widths when total exceeds available space.

        Strategy:
        1. Give each widget at least its minimum width
        2. Distribute remaining space proportionally
        3. Compress larger widgets more aggressively

        Args:
            active_widgets: List of (idx, config, widget) tuples
            natural_sizes: Natural size of each widget
            available: Total available width for all widgets

        Returns:
            List of allocated widths
        """
        num_widgets = len(active_widgets)

        # Get minimum widths from widget registry
        min_widths = []
        for _, _, widget in active_widgets:
            min_width = max(widget.min_width, 5)  # At least 5 chars
            min_widths.append(min_width)

        total_min = sum(min_widths)

        # If even minimums don't fit, just use minimums and let truncation happen
        if total_min >= available:
            return min_widths

        # Calculate extra space to distribute
        extra = available - total_min

        # Distribute extra space proportionally based on natural size
        total_natural = sum(natural_sizes)
        allocated = []

        for i in range(num_widgets):
            min_w = min_widths[i]
            natural = natural_sizes[i]

            # Proportion of extra space based on natural size
            if total_natural > 0:
                extra_share = int(extra * natural / total_natural)
            else:
                extra_share = extra // num_widgets

            # Cap at natural size (don't over-allocate)
            alloc = min(min_w + extra_share, natural)
            allocated.append(alloc)

        return allocated

    def _render_widget_with_width(
        self,
        widget_config: WidgetConfig,
        widget,
        width: int,
        is_selected: bool = False,
        add_leading_space: bool = False,
    ) -> str:
        """Render a widget with specific allocated width.

        Args:
            widget_config: Widget configuration
            widget: Widget instance from registry
            width: Allocated width for this widget
            is_selected: Whether widget is selected
            add_leading_space: If True, add leading space with background (for spacing between widgets)

        Returns:
            Rendered widget string
        """
        try:
            logger.debug(
                f"[RENDER] widget='{widget_config.id}', color='{widget_config.color}'"
            )

            # Set widget_config on context for render function to access persisted config
            if self._context:
                self._context.widget_config = widget_config.config
                self._context.widget_id = widget_config.id

            rendered = widget.render(width, self._context)

            # Apply visual effect if configured (shimmer, pulse)
            effect_name = getattr(widget_config, "effect", "none")
            if effect_name != "none" and rendered:
                effect = _get_widget_effect(widget_config.id, effect_name)
                if effect:
                    # Use ANSI-aware method to preserve existing colors
                    if hasattr(effect, "apply_shimmer_ansi"):
                        rendered = effect.apply_shimmer_ansi(rendered)
                    elif hasattr(effect, "apply_pulse_ansi"):
                        rendered = effect.apply_pulse_ansi(rendered)
                    logger.debug(
                        f"[RENDER] Applied {effect_name} effect to '{widget_config.id}'"
                    )

            # Apply widget background color if configured
            bg_color = _get_widget_bg_color(widget_config.color)
            if bg_color:
                logger.debug(
                    f"[RENDER] Applying bg color {bg_color} to widget '{widget_config.id}'"
                )
                rendered = _apply_bg_color(
                    rendered, bg_color, add_leading_space=add_leading_space
                )
            else:
                logger.debug(
                    f"[RENDER] No bg color for widget '{widget_config.id}' (color='{widget_config.color}')"
                )
                # No background - just add leading space if needed (no trailing to avoid double spacing)
                if add_leading_space:
                    rendered = f" {rendered}"

            # Apply selection highlight if widget is selected (underline)
            if is_selected and rendered:
                rendered = f"\033[4m{rendered}\033[24m"

            return str(rendered)
        except Exception as e:
            logger.error(f"Error rendering widget '{widget_config.id}': {e}")
            return f"[{widget_config.id}:err]"

    def _render_edit_row(
        self, row: RowConfig, total_width: int, row_idx: int = 0
    ) -> str:
        """Render a row in edit mode with insertion slots between widgets.

        Shows '+' slots between all widgets. Empty rows show '+ (row N) +'.
        Selected slot highlights the '+' symbol. Selected widget uses underline.

        Args:
            row: Row configuration
            total_width: Total available width
            row_idx: Row index for selection checking

        Returns:
            Rendered row string with slots and gutter
        """
        # Gutter takes 1 character, leading space takes 1
        gutter_width = 1
        content_width = total_width - gutter_width - 1

        # Get navigation state for selection
        selected_row, selected_widget_idx, selected_slot_idx = -1, -1, -1
        selected_type = SelectionType.WIDGET
        if self._navigation_manager and hasattr(self._navigation_manager, "state"):
            nav_state = self._navigation_manager.state
            if hasattr(nav_state, "selected_row"):
                selected_row = nav_state.selected_row
            if hasattr(nav_state, "selected_widget_index"):
                selected_widget_idx = nav_state.selected_widget_index
            if hasattr(nav_state, "slot_index"):
                selected_slot_idx = nav_state.slot_index
            if hasattr(nav_state, "selected_type"):
                selected_type = nav_state.selected_type

        # Handle empty rows
        if not row.widgets:
            is_row_selected = (
                row_idx == selected_row and selected_type == SelectionType.SLOT
            )
            # Show '+ (empty row N) +' for empty rows
            slot_char = (
                _fg("[+]", T().primary[0])
                if is_row_selected
                else _fg("+", T().text_dim)
            )
            placeholder = f"(empty row {row.id})"
            placeholder_text = _fg(placeholder, T().text_dim)
            content = f" {slot_char} {placeholder_text} {slot_char} "
        else:
            # Build content with slots: + widget1 + widget2 + ...
            content_parts = []

            # Add slot before first widget
            slot_idx = 0
            is_slot_selected = (
                row_idx == selected_row
                and selected_type == SelectionType.SLOT
                and selected_slot_idx == slot_idx
            )
            slot_char = (
                _fg("[+]", T().primary[0])
                if is_slot_selected
                else _fg("+", T().text_dim)
            )
            content_parts.append(f" {slot_char}")

            # Render each widget with slot after
            for widget_idx, widget_config in enumerate(row.widgets):
                widget = self._widget_registry.get(widget_config.id)
                if not widget:
                    continue

                # Check if widget is selected
                is_widget_selected = (
                    row_idx == selected_row
                    and selected_type == SelectionType.WIDGET
                    and widget_idx == selected_widget_idx
                )

                # Render widget (give it natural width)
                # Add leading space for all widgets (edit mode always has leading space)
                try:
                    rendered = widget.render(content_width, self._context)
                    if rendered and _strip_ansi(rendered).strip():
                        # Apply visual effect if configured (shimmer, pulse)
                        effect_name = getattr(widget_config, "effect", "none")
                        if effect_name != "none":
                            effect = _get_widget_effect(widget_config.id, effect_name)
                            if effect:
                                if hasattr(effect, "apply_shimmer_ansi"):
                                    rendered = effect.apply_shimmer_ansi(rendered)
                                elif hasattr(effect, "apply_pulse_ansi"):
                                    rendered = effect.apply_pulse_ansi(rendered)

                        # Apply widget background color if configured
                        bg_color = _get_widget_bg_color(widget_config.color)
                        if bg_color:
                            rendered = _apply_bg_color(
                                rendered, bg_color, add_leading_space=True
                            )
                        else:
                            # No background - add leading space for separation
                            rendered = f" {rendered}"

                        # Apply underline if selected
                        if is_widget_selected:
                            rendered = f"\033[4m{rendered}\033[24m"

                        content_parts.append(rendered)
                except Exception as e:
                    logger.error(f"Error rendering widget '{widget_config.id}': {e}")

                # Add slot after widget
                slot_idx += 1
                is_slot_selected = (
                    row_idx == selected_row
                    and selected_type == SelectionType.SLOT
                    and selected_slot_idx == slot_idx
                )
                slot_char = (
                    _fg("[+]", T().primary[0])
                    if is_slot_selected
                    else _fg("+", T().text_dim)
                )
                content_parts.append(f" {slot_char}")

            content = "".join(content_parts)

        # Build line: gutter + space + content
        # In edit mode, don't apply row background as it would override widget background colors
        gutter = str(solid(" ", T().dark[0], T().text_dim, gutter_width))
        # Check if any widget has a background color
        has_colored_widgets = (
            any(_get_widget_bg_color(w.color) is not None for w in row.widgets)
            if row.widgets
            else False
        )

        if has_colored_widgets:
            # Don't apply row background - let widget colors show through
            # But DO reset background at the end to clear any widget backgrounds
            visible_len = len(_strip_ansi(content))
            padding = max(0, (total_width - gutter_width) - visible_len)
            # Apply dark background to content and padding, reset at end
            dr, dg, db = T().dark[0]
            dark_bg = f"\033[48;2;{dr};{dg};{db}m"
            content_line = f"{dark_bg}{content}{' ' * padding}\033[0m"
        else:
            # Apply standard dark background when no widgets have custom colors
            content_line = _bg_line(content, T().dark[0], total_width - gutter_width)

        return gutter + content_line

    def _render_widget(
        self,
        widget_config: WidgetConfig,
        available_width: int,
        is_selected: bool = False,
    ) -> str:
        """Render a single widget.

        Args:
            widget_config: Widget configuration
            available_width: Available width for the widget
            is_selected: Whether this widget is currently selected in navigation mode

        Returns:
            Rendered widget string
        """
        # Check if this widget is being edited inline
        inline_edit_output = None
        if self._navigation_manager and hasattr(self._navigation_manager, "state"):
            nav_state = self._navigation_manager.state
            if hasattr(nav_state, "inline_edit_state"):

                try:
                    # Get inline edit state synchronously for rendering
                    edit_state = nav_state.inline_edit_state
                    if (
                        edit_state
                        and edit_state.widget_id == widget_config.id
                        and edit_state.editor_output
                    ):
                        inline_edit_output = edit_state.editor_output
                        logger.debug(
                            f"Rendering inline editor for widget: {widget_config.id}"
                        )
                except Exception as e:
                    logger.debug(f"Error accessing inline edit state: {e}")

        # If inline editor is active, render the editor output instead of the widget
        if inline_edit_output:
            return str(inline_edit_output)

        widget = self._widget_registry.get(widget_config.id)
        if not widget:
            logger.warning(f"Widget '{widget_config.id}' not found in registry")
            return ""

        # Calculate widget width
        width = self._calculate_widget_width(widget_config, available_width)

        # Render widget
        try:
            rendered = widget.render(width, self._context)

            # Apply selection highlight if widget is selected (underline)
            if is_selected and rendered:
                rendered = f"\033[4m{rendered}\033[24m"

            return str(rendered)
        except Exception as e:
            logger.error(f"Error rendering widget '{widget_config.id}': {e}")
            return f"[{widget_config.id}:err]"

    def _calculate_widget_width(
        self, widget_config: WidgetConfig, available_width: int
    ) -> int:
        """Calculate actual widget width based on configuration.

        Args:
            widget_config: Widget configuration with width spec
            available_width: Total available width

        Returns:
            Calculated width in characters
        """
        width_spec = widget_config.width
        widget = self._widget_registry.get(widget_config.id)
        min_width = widget.min_width if widget else 5

        if width_spec.type == WidthType.AUTO:
            # Auto: widget determines its own width, but cap at available
            return available_width

        elif width_spec.type == WidthType.RELATIVE:
            # Relative: percentage of available width
            percent = width_spec.value or 25
            calculated = int(available_width * percent / 100)
            return max(min_width, calculated)

        elif width_spec.type == WidthType.FIXED:
            # Fixed: exact character count
            fixed = width_spec.value or 10
            return max(min_width, min(fixed, available_width))

        return available_width

    def _truncate_with_ansi(self, text: str, max_length: int) -> str:
        """Truncate text while preserving ANSI codes.

        Args:
            text: Text with possible ANSI codes
            max_length: Maximum visible length

        Returns:
            Truncated text
        """
        result = ""
        visible_count = 0
        i = 0

        while i < len(text) and visible_count < max_length:
            if text[i : i + 1] == "\033" and i + 1 < len(text) and text[i + 1] == "[":
                # Find end of ANSI sequence
                end = i + 2
                while end < len(text) and text[end] not in "mhlABCDEFGHJKSTfimpsuI":
                    end += 1
                if end < len(text):
                    end += 1
                result += text[i:end]
                i = end
            else:
                result += text[i]
                visible_count += 1
                i += 1

        return result

    def render_preview(self, row_id: Optional[int] = None) -> List[str]:
        """Render a preview of the status area or a single row.

        Used by the status setup UI to show real-time preview.

        Args:
            row_id: Optional specific row to preview (None = all)

        Returns:
            List of rendered preview lines
        """
        layout = self._layout_manager.get_layout()
        width = get_global_width()

        if row_id is not None:
            row = layout.get_row(row_id)
            if row:
                content = self._render_row(row, width)
                return [content] if content else []
            return []

        # Render all rows for full preview
        return self.render()

    def render_widget_preview(self, widget_id: str, width: int = 30) -> str:
        """Render a preview of a single widget.

        Used by the widget picker to show what a widget looks like.

        Args:
            widget_id: Widget to preview
            width: Preview width

        Returns:
            Rendered widget preview string
        """
        widget = self._widget_registry.get(widget_id)
        if not widget:
            return f"[{widget_id}: not found]"

        try:
            return widget.render(width, self._context)
        except Exception as e:
            logger.error(f"Error rendering widget preview '{widget_id}': {e}")
            return f"[{widget_id}: error]"
