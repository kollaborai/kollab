"""kollabor-tui: Terminal UI primitives for Kollabor.

Provides design system, key parsing, terminal state management,
visual effects, and core rendering components as reusable,
standalone components.
"""

# Modal system - lazy imports to avoid circular import with kollabor.ui
# Buffer manager
from .buffer_manager import BufferManager

# Pluggable renderer system
from .clean_renderer import CleanRenderer

# Config UI components
from .config_merger import ConfigMerger
from .config_widgets import ConfigWidgetDefinitions

# Fullscreen plugin framework
from .fullscreen import (
    FullScreenManager,
    FullScreenPlugin,
    FullScreenRenderer,
    FullScreenSession,
)

# Input error handling
from .input_errors import (
    ErrorRecoveryStrategy,
    ErrorSeverity,
    ErrorType,
    InputError,
    InputErrorHandler,
)

# Key parsing
from .key_parser import KeyParser, KeyPress

# Menu renderer
from .menu_renderer import CommandMenuRenderer

# Message coordination
from .message_coordinator import MessageDisplayCoordinator

# Message display service
from .message_display_service import MessageDisplayService

# Message rendering
from .message_renderer import (
    DisplayFilterRegistry,
    MessageFormat,
    MessageRenderer,
    MessageType,
    ModernMessageRenderer,
)
from .modals import (
    ConfirmationModal,
    Modal,
)

# Profile modal builders
from .profile_modal_builder import (
    build_create_profile_modal,
    build_delete_profile_confirm_modal,
    build_edit_profile_modal,
    build_profiles_modal,
)

# Layout
from .render_layout import (
    AreaAlignment,
    LayoutArea,
    LayoutManager,
    LayoutMode,
    ScreenRegion,
    ThinkingAnimationManager,
)

# Render loop
from .render_loop import (
    EventDrivenRenderLoop,
    RenderLoopStats,
    RenderTrigger,
)
from .renderer_protocol import MessageRendererProtocol, get_renderer
from .simple_renderer import SimpleRenderer

# Status renderer (from layout_renderer - canonical location)
from .status.layout_renderer import (
    render_mode_indicator,
    render_selected_widget,
)

# Terminal renderer
from .terminal_renderer import TerminalRenderer

# Terminal state management
from .terminal_state import (
    TerminalState,
    get_global_terminal_state,
    get_global_width,
    get_terminal_height,
    get_terminal_size,
    get_terminal_width,
    set_global_terminal_state,
)

# Thinking display
from .thinking_display import ThinkingDisplayFormatter

# Tool display formatting
from .tool_display import (
    extract_file_display_info,
    extract_tool_info,
    extract_tool_name_args,
    format_edit_diff,
    format_tool_header,
    format_tool_output,
    format_tool_result,
    get_result_summary_modern,
    get_tool_result_summary,
    should_show_output,
    truncate_tool_args,
)

# Tool spinner
from .tool_spinner import ToolSpinnerManager, get_tool_spinner, is_tool_spinner_enabled

# Visual effects
from .visual_effects import (
    ColorSupport,
    get_color_support,
    set_color_support,
)
from .widget_showcase import WidgetShowcase, get_widget_showcase

# Widgets
from .widgets import (
    BaseWidget,
    CheckboxWidget,
    DropdownWidget,
    FileBrowserWidget,
    LabelWidget,
    MultiSelectWidget,
    ProgressWidget,
    SearchableDropdownWidget,
    SliderWidget,
    SpinBoxWidget,
    TextAreaWidget,
    TextInputWidget,
    TreeNode,
    TreeViewWidget,
)

__all__ = [
    # Modal system
    "Modal",
    "ConfirmationModal",
    "ModalRenderer",
    "ModalStateManager",
    "ModalLayout",
    "ModalDisplayMode",
    "TerminalSnapshot",
    "ModalOverlayRenderer",
    "ModalState",
    "ModalActionHandler",
    # Terminal state
    "TerminalState",
    "get_global_terminal_state",
    "get_terminal_size",
    "get_terminal_width",
    "get_terminal_height",
    "get_global_width",
    "set_global_terminal_state",
    # Key parsing
    "KeyParser",
    "KeyPress",
    # Visual effects
    "ColorSupport",
    "set_color_support",
    "get_color_support",
    # Render loop
    "EventDrivenRenderLoop",
    "RenderTrigger",
    "RenderLoopStats",
    # Layout
    "LayoutMode",
    "AreaAlignment",
    "ScreenRegion",
    "LayoutArea",
    "ThinkingAnimationManager",
    "LayoutManager",
    # Message rendering
    "DisplayFilterRegistry",
    "MessageType",
    "MessageFormat",
    "ModernMessageRenderer",
    "MessageRenderer",
    "MessageRendererProtocol",
    "CleanRenderer",
    "SimpleRenderer",
    "get_renderer",
    # Terminal renderer
    "TerminalRenderer",
    # Input error handling
    "InputErrorHandler",
    "ErrorType",
    "ErrorSeverity",
    "ErrorRecoveryStrategy",
    "InputError",
    # Config UI
    "ConfigMerger",
    "ConfigWidgetDefinitions",
    "WidgetShowcase",
    "get_widget_showcase",
    # Widgets
    "BaseWidget",
    "CheckboxWidget",
    "DropdownWidget",
    "TextInputWidget",
    "SliderWidget",
    "LabelWidget",
    "MultiSelectWidget",
    "TextAreaWidget",
    "SearchableDropdownWidget",
    "SpinBoxWidget",
    "TreeViewWidget",
    "TreeNode",
    "ProgressWidget",
    "FileBrowserWidget",
    # Buffer manager
    "BufferManager",
    # Message coordination
    "MessageDisplayCoordinator",
    # Tool spinner
    "ToolSpinnerManager",
    "get_tool_spinner",
    "is_tool_spinner_enabled",
    # Status renderer
    "render_selected_widget",
    "render_mode_indicator",
    # Menu renderer
    "CommandMenuRenderer",
    # Tool display
    "format_tool_header",
    "extract_file_display_info",
    "format_tool_result",
    "extract_tool_info",
    "extract_tool_name_args",
    "truncate_tool_args",
    "get_result_summary_modern",
    "get_tool_result_summary",
    "should_show_output",
    "format_tool_output",
    "format_edit_diff",
    # Message display service
    "MessageDisplayService",
    # Thinking display
    "ThinkingDisplayFormatter",
    # Profile modal builders
    "build_profiles_modal",
    "build_create_profile_modal",
    "build_edit_profile_modal",
    "build_delete_profile_confirm_modal",
    # Fullscreen plugin framework
    "FullScreenManager",
    "FullScreenRenderer",
    "FullScreenPlugin",
    "FullScreenSession",
]


# Lazy imports for modal classes that depend on kollabor.ui (avoiding circular imports)
def __getattr__(name: str):
    """Lazy import modal classes to avoid circular import with kollabor.ui."""
    if name == "ModalRenderer":
        from .modals.modal_renderer import ModalRenderer

        return ModalRenderer
    elif name == "ModalStateManager":
        from .modals.modal_state_manager import ModalStateManager

        return ModalStateManager
    elif name == "ModalLayout":
        from .modals.modal_state_manager import ModalLayout

        return ModalLayout
    elif name == "ModalDisplayMode":
        from .modals.modal_state_manager import ModalDisplayMode

        return ModalDisplayMode
    elif name == "TerminalSnapshot":
        from .modals.modal_state_manager import TerminalSnapshot

        return TerminalSnapshot
    elif name == "ModalOverlayRenderer":
        from .modals.modal_overlay_renderer import ModalOverlayRenderer

        return ModalOverlayRenderer
    elif name == "ModalState":
        from .modals.modal_overlay_renderer import ModalState

        return ModalState
    elif name == "ModalActionHandler":
        from .modals.modal_actions import ModalActionHandler

        return ModalActionHandler
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
