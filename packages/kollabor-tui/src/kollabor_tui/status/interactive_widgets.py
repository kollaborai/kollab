"""Interactive status widgets for the Kollab.

Provides interactive widgets that can be embedded in the status area
for controlling LLM parameters like temperature, max tokens, and profile
selection.

Widgets:
    TemperatureWidget: Inline slider for LLM temperature (0.0-2.0)
    MaxTokensWidget: Inline slider for max tokens (100-8000)
    ProfileQuickSwitcher: Dropdown for quick profile switching

Each widget has a render_fn for display and on_activate handler for
interactive control.
"""

import logging
from typing import Any, Dict, List, Optional

from kollabor_tui.design_system import T, inline_dropdown, inline_slider

from .utils import fg as _fg
from .utils import truncate as _truncate

logger = logging.getLogger(__name__)


# =============================================================================
# INTERACTIVE WIDGET CLASSES
# =============================================================================


class InteractiveWidget:
    """Base class for interactive status widgets.

    Provides a common interface for widgets that have both display
    and interactive activation behavior.
    """

    def __init__(
        self,
        widget_id: str,
        name: str,
        description: str,
        category: str = "interactive",
        min_width: int = 10,
    ):
        """Initialize the interactive widget.

        Args:
            widget_id: Unique identifier for the widget
            name: Display name for the widget picker
            description: Brief description of what the widget shows/controls
            category: Widget category for organization
            min_width: Minimum width in characters
        """
        self.id = widget_id
        self.name = name
        self.description = description
        self.category = category
        self.min_width = min_width

    def render(self, width: int, context: Any = None) -> str:
        """Render the widget content.

        Args:
            width: Available width in characters
            context: Optional context with services (profile_manager, etc.)

        Returns:
            Rendered widget content as string (may include ANSI codes)
        """
        raise NotImplementedError("Subclasses must implement render()")

    async def on_activate(self, widget_id: str, context: Any) -> Dict[str, Any]:
        """Handle widget activation (user selected this widget).

        Args:
            widget_id: ID of activated widget
            context: Widget context with services

        Returns:
            Activation result (modal definition, new state, etc.)
        """
        raise NotImplementedError("Subclasses must implement on_activate()")


class TemperatureWidget(InteractiveWidget):
    """Interactive temperature control widget.

    Displays current temperature as an inline slider. When activated,
    provides interactive controls for adjusting temperature.

    Configuration:
        min: Minimum temperature (default: 0.0)
        max: Maximum temperature (default: 2.0)
        step: Temperature step size (default: 0.1)
        presets: Quick preset values [0.1, 0.5, 0.7, 1.0, 1.5]

    Updates profile_manager on value change.
    """

    def __init__(
        self,
        min_temp: float = 0.0,
        max_temp: float = 2.0,
        step: float = 0.1,
        presets: Optional[List[float]] = None,
    ):
        """Initialize the temperature widget.

        Args:
            min_temp: Minimum temperature value
            max_temp: Maximum temperature value
            step: Step size for adjustments
            presets: Quick preset values
        """
        super().__init__(
            widget_id="temperature",
            name="Temperature",
            description="Model temperature control (0.0-2.0)",
            category="llm",
            min_width=12,
        )
        self.min_temp = min_temp
        self.max_temp = max_temp
        self.step = step
        self.presets = presets or [0.1, 0.5, 0.7, 1.0, 1.5]

    def render(self, width: int, context: Any = None) -> str:
        """Render the temperature widget as an inline slider.

        Args:
            width: Available width
            context: Widget context with profile_manager

        Returns:
            Rendered temperature slider
        """
        try:
            current_temp = 0.7
            if context and hasattr(context, "profile_manager"):
                profile = context.profile_manager.get_active_profile()
                if profile:
                    current_temp = profile.get_temperature()

            # Calculate slider width (reserve space for value display)
            value_width = 5  # "X.XX"
            slider_width = max(4, width - value_width - 3)  # -3 for brackets and space

            # Use inline_slider from design system
            slider = inline_slider(
                value=current_temp,
                min_val=self.min_temp,
                max_val=self.max_temp,
                width=slider_width,
                show_value=True,
            )

            # Add icon prefix
            icon = _fg(
                "T", T().secondary[0] if hasattr(T(), "secondary") else T().ai_tag
            )

            return f"{icon} {slider}"
        except Exception as e:
            logger.error(f"temperature widget render error: {e}")
            return _fg("T ?", T().text_dim)

    async def on_activate(self, widget_id: str, context: Any) -> Dict[str, Any]:
        """Handle widget activation - show interactive slider.

        Args:
            widget_id: ID of activated widget
            context: Widget context with profile_manager

        Returns:
            Slider configuration for interactive mode
        """
        current_temp = 0.7
        if context and hasattr(context, "profile_manager"):
            profile = context.profile_manager.get_active_profile()
            if profile:
                current_temp = profile.get_temperature()

        return {
            "type": "slider",
            "title": "Temperature",
            "current": current_temp,
            "min": self.min_temp,
            "max": self.max_temp,
            "step": self.step,
            "presets": self.presets,
            "on_change": "update_temperature",
        }


class MaxTokensWidget(InteractiveWidget):
    """Interactive max tokens control widget.

    Displays current max_tokens setting as an inline slider.
    When activated, provides interactive controls for adjustment.

    Configuration:
        min: Minimum tokens (default: 100)
        max: Maximum tokens (default: 8000)
        step: Step size (default: 100)

    Updates profile_manager on value change.
    """

    def __init__(
        self,
        min_tokens: int = 100,
        max_tokens: int = 8000,
        step: int = 100,
    ):
        """Initialize the max tokens widget.

        Args:
            min_tokens: Minimum token count
            max_tokens: Maximum token count
            step: Step size for adjustments
        """
        super().__init__(
            widget_id="max-tokens",
            name="Max Tokens",
            description="Maximum response tokens (100-8000)",
            category="llm",
            min_width=12,
        )
        self.min_tokens = min_tokens
        self.max_tokens = max_tokens
        self.step = step

    def render(self, width: int, context: Any = None) -> str:
        """Render the max tokens widget as an inline slider.

        Args:
            width: Available width
            context: Widget context with profile_manager

        Returns:
            Rendered max tokens slider
        """
        try:
            current_tokens = None
            if context and hasattr(context, "profile_manager"):
                profile = context.profile_manager.get_active_profile()
                if profile:
                    current_tokens = profile.get_max_tokens()

            # If not set, show API default indicator
            if current_tokens is None:
                return _fg("tok default", T().text_dim)

            # Calculate slider width
            value_width = len(str(current_tokens)) + 1
            slider_width = max(4, width - value_width - 3)

            # Use inline_slider from design system
            slider = inline_slider(
                value=current_tokens,
                min_val=self.min_tokens,
                max_val=self.max_tokens,
                width=slider_width,
                show_value=True,
            )

            # Add icon prefix
            icon = _fg(
                "#", T().secondary[0] if hasattr(T(), "secondary") else T().ai_tag
            )

            return f"{icon} {slider}"
        except Exception as e:
            logger.error(f"max-tokens widget render error: {e}")
            return _fg("# ?", T().text_dim)

    async def on_activate(self, widget_id: str, context: Any) -> Dict[str, Any]:
        """Handle widget activation - show interactive slider.

        Args:
            widget_id: ID of activated widget
            context: Widget context with profile_manager

        Returns:
            Slider configuration for interactive mode
        """
        current_tokens = None
        if context and hasattr(context, "profile_manager"):
            profile = context.profile_manager.get_active_profile()
            if profile:
                current_tokens = profile.get_max_tokens()

        # Default to sensible value if not set
        if current_tokens is None:
            current_tokens = 4096

        return {
            "type": "slider",
            "title": "Max Tokens",
            "current": current_tokens,
            "min": self.min_tokens,
            "max": self.max_tokens,
            "step": self.step,
            "on_change": "update_max_tokens",
        }


class ProfileQuickSwitcher(InteractiveWidget):
    """Interactive profile quick switcher widget.

    Displays current profile name as an inline dropdown. When activated,
    shows a list of available profiles for quick switching.

    Reads profiles from profile_manager and switches active profile
    on selection.
    """

    def __init__(self):
        """Initialize the profile quick switcher widget."""
        super().__init__(
            widget_id="profile-switcher",
            name="Profile Switcher",
            description="Quick switch between LLM profiles",
            category="llm",
            min_width=12,
        )

    def render(self, width: int, context: Any = None) -> str:
        """Render the profile switcher as an inline dropdown.

        Args:
            width: Available width
            context: Widget context with profile_manager

        Returns:
            Rendered profile dropdown
        """
        try:
            profile_name = "default"
            if context and hasattr(context, "profile_manager"):
                profile = context.profile_manager.get_active_profile()
                if profile:
                    profile_name = profile.name

            # Truncate if needed
            max_display_width = width - 4  # Reserve space for brackets and arrow
            display = _truncate(profile_name, max_display_width)

            # Use inline_dropdown from design system
            dropdown = inline_dropdown(display, max_width=max_display_width)

            return dropdown
        except Exception as e:
            logger.error(f"profile-switcher widget render error: {e}")
            return _fg("[?]", T().text_dim)

    async def on_activate(self, widget_id: str, context: Any) -> Dict[str, Any]:
        """Handle widget activation - show profile selection dropdown.

        Args:
            widget_id: ID of activated widget
            context: Widget context with profile_manager

        Returns:
            Dropdown configuration with profile list
        """
        profiles = []
        current_profile = "default"

        if context and hasattr(context, "profile_manager"):
            current_profile_obj = context.profile_manager.get_active_profile()
            if current_profile_obj:
                current_profile = current_profile_obj.name

            # Get all available profiles
            for profile in context.profile_manager.list_profiles():
                profiles.append(
                    {
                        "value": profile.name,
                        "label": profile.name,
                        "description": profile.description
                        or f"{profile.provider} - {profile.model}",
                    }
                )

        return {
            "type": "dropdown",
            "title": "Select Profile",
            "current": current_profile,
            "options": profiles,
            "on_select": "switch_profile",
        }


class TestTextInputWidget(InteractiveWidget):
    """Test widget for inline text input verification.

    Displays a test text field that can be edited inline. Used for
    verification testing of the inline text input feature.

    Features:
    - Text input with cursor position tracking
    - Backspace/Delete for editing
    - Left/Right arrows for cursor movement
    - Home/End for navigation
    - Enter to confirm, Esc to cancel
    """

    # Class variable to store the test text value
    _test_text = "test"

    def __init__(self):
        """Initialize the test text input widget."""
        super().__init__(
            widget_id="test-text-input",
            name="Test Text Input",
            description="Test widget for inline text input verification",
            category="test",
            min_width=15,
        )

    def render(self, width: int, context: Any = None) -> str:
        """Render the test text input widget.

        Args:
            width: Available width
            context: Widget context (not used)

        Returns:
            Rendered text input display
        """
        try:
            display_text = TestTextInputWidget._test_text
            max_display_width = width - 3  # Reserve for "t:" prefix

            # Truncate if needed
            if len(display_text) > max_display_width:
                display_text = _truncate(display_text, max_display_width)

            icon = _fg("t", T().primary[0])
            text = _fg(display_text, T().text)

            return f"{icon}:{text}"
        except Exception as e:
            logger.error(f"test-text-input widget render error: {e}")
            return _fg("t?", T().text_dim)

    async def on_activate(self, widget_id: str, context: Any) -> Dict[str, Any]:
        """Handle widget activation - show inline text editor.

        Args:
            widget_id: ID of activated widget
            context: Widget context

        Returns:
            Text editor configuration
        """
        return {
            "type": "text",
            "current": TestTextInputWidget._test_text,
            "max_length": 50,
            "placeholder": "type to edit...",
            "on_save": self._save_text,
        }

    async def _save_text(self, value: str) -> None:
        """Save the edited text value.

        Args:
            value: New text value to save
        """
        TestTextInputWidget._test_text = value
        logger.info(f"Test text saved: {value}")


# =============================================================================
# MODULE-LEVEL WIDGET INSTANCES (reused across render frames)
# =============================================================================

_temperature_widget = TemperatureWidget()
_max_tokens_widget = MaxTokensWidget()
_profile_switcher_widget = ProfileQuickSwitcher()
_test_text_input_widget = TestTextInputWidget()

# Widget lookup table for activation handlers
WIDGET_HANDLERS: Dict[str, InteractiveWidget] = {
    "temperature": _temperature_widget,
    "max-tokens": _max_tokens_widget,
    "profile-switcher": _profile_switcher_widget,
    "test-text-input": _test_text_input_widget,
}


# =============================================================================
# REGISTRATION FUNCTION
# =============================================================================


def register_interactive_widgets(registry: Any) -> None:
    """Register all interactive widgets with the widget registry.

    Args:
        registry: StatusWidgetRegistry instance to register widgets with
    """
    logger.info("Registering interactive status widgets...")

    registry.register(
        id="temperature",
        name="Temperature",
        description="Model temperature control (0.0-2.0)",
        render_fn=_temperature_widget.render,
        category="interactive",
        default_width="16ch",
        min_width=12,
    )

    registry.register(
        id="max-tokens",
        name="Max Tokens",
        description="Maximum response tokens (100-8000)",
        render_fn=_max_tokens_widget.render,
        category="interactive",
        default_width="16ch",
        min_width=12,
    )

    registry.register(
        id="profile-switcher",
        name="Profile Switcher",
        description="Quick switch between LLM profiles",
        render_fn=_profile_switcher_widget.render,
        category="interactive",
        default_width="auto",
        min_width=12,
    )

    registry.register(
        id="test-text-input",
        name="Test Text Input",
        description="Test widget for inline text input verification",
        render_fn=_test_text_input_widget.render,
        category="test",
        default_width="20ch",
        min_width=15,
        interactive=True,
        interaction_type="inline_edit",
        on_activate=lambda widget_id, context: handle_widget_activation(
            widget_id, context
        ),
    )

    logger.info("Registered 4 interactive widgets")


async def handle_widget_activation(widget_id: str, context: Any) -> Dict[str, Any]:
    """Handle widget activation by routing to appropriate widget instance.

    Args:
        widget_id: ID of the widget to activate
        context: Widget context with services

    Returns:
        Activation result from widget's on_activate method

    Raises:
        KeyError: If widget_id is not found in handlers
    """
    if widget_id not in WIDGET_HANDLERS:
        logger.error(f"Unknown interactive widget: {widget_id}")
        raise KeyError(f"No handler found for widget: {widget_id}")

    widget = WIDGET_HANDLERS[widget_id]
    return await widget.on_activate(widget_id, context)
