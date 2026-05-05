"""Status area system with customizable widget layout.

This module provides a widget-based status area system where users can
configure which widgets to display and how to arrange them.

Components:
    - StatusWidgetRegistry: Central registry for status widgets
    - StatusWidget: Base class for widget implementations
    - StatusLayoutManager: Load/save layout configuration
    - LayoutRenderer: Render status area from layout config
    - Core widgets: cwd, profile, model, endpoint, status, stats, agent, skills, tasks
    - Interactive widgets: temperature, max-tokens, profile-switcher
"""

from .core_widget_modals import CORE_WIDGET_MODALS, get_modal_for_widget
from .core_widgets import WidgetContext, register_core_widgets
from .help_system import generate_help_text, show_first_run_help, show_help_overlay
from .inline_editor_service import InlineEditorService
from .inline_editors import (
    BaseInlineEditor,
    EditorResult,
    InlineDropdownEditor,
    InlineSliderEditor,
    InlineTextEditor,
)
from .interaction_handler import (
    InteractionState,
    WidgetInteractionHandler,
)
from .interaction_handler import (
    InteractionType as WidgetInteractionType,
)
from .interactive_widgets import (
    WIDGET_HANDLERS,
    MaxTokensWidget,
    ProfileQuickSwitcher,
    TemperatureWidget,
    handle_widget_activation,
    register_interactive_widgets,
)
from .layout_manager import (
    RowConfig,
    StatusLayout,
    StatusLayoutManager,
    WidgetConfig,
)
from .layout_renderer import StatusLayoutRenderer
from .mcp_status_view import (
    MCPStatusView,
    MCPToolDetailView,
    render_mcp_status,
    render_mcp_tool_detail,
)
from .navigation_manager import (
    StatusNavigationManager,
    StatusNavigationState,
)
from .permission_status_view import PermissionStatusView
from .plugin_api import StatusWidgetAPI
from .script_action_handlers import (
    ActionResult,
    ActionStatus,
    ScriptActionHandler,
    display_command_output,
    execute_action_script,
    execute_action_script_sync,
    format_action_result,
    parse_action_result,
    validate_action_key,
)
from .script_modal_handlers import (
    ScriptModalError,
    activate_script_widget,
    execute_script_modal,
    normalize_modal_option,
    parse_modal_json,
    route_modal_action,
    validate_modal_config,
)
from .script_refresh_scheduler import (
    RefreshMode,
    ScriptWidgetRefreshScheduler,
    WidgetSchedule,
)
from .script_widgets import (
    InteractionType as ScriptInteractionType,
)
from .script_widgets import (
    RefreshType,
    ScriptWidget,
    ScriptWidgetManager,
    register_script_widgets,
)
from .status_menu_renderer import StatusMenuRenderer
from .system_monitor import (
    SystemDataCollector,
    color_threshold,
    get_status_label,
    get_sysmon_config,
    get_system_collector,
    render_sysmon,
)
from .toggle_handler import (
    ToggleHandler,
    ToggleWidgetContext,
    create_toggle_handler_from_widget,
)
from .widget_picker import PickerState, WidgetPickerModal
from .widget_registry import (
    StatusWidget,
    StatusWidgetRegistry,
    WidgetCategory,
    WidgetWidth,
    WidthType,
)

__all__ = [
    # Widget Registry
    "StatusWidgetRegistry",
    "StatusWidget",
    "WidgetWidth",
    "WidthType",
    "WidgetCategory",
    # Layout Manager
    "StatusLayoutManager",
    "StatusLayout",
    "RowConfig",
    "WidgetConfig",
    # Layout Renderer
    "StatusLayoutRenderer",
    # Plugin API
    "StatusWidgetAPI",
    # Navigation
    "StatusNavigationManager",
    "StatusNavigationState",
    # Core Widgets
    "register_core_widgets",
    "WidgetContext",
    # Core Widget Modals
    "CORE_WIDGET_MODALS",
    "get_modal_for_widget",
    # Help System
    "show_first_run_help",
    "show_help_overlay",
    "generate_help_text",
    # Inline Editors
    "EditorResult",
    "BaseInlineEditor",
    "InlineSliderEditor",
    "InlineTextEditor",
    "InlineDropdownEditor",
    "InlineEditorService",
    # Toggle Handler
    "ToggleHandler",
    "ToggleWidgetContext",
    "create_toggle_handler_from_widget",
    # Interactive Widgets
    "TemperatureWidget",
    "MaxTokensWidget",
    "ProfileQuickSwitcher",
    "register_interactive_widgets",
    "handle_widget_activation",
    "WIDGET_HANDLERS",
    # Script Widgets
    "ScriptWidgetManager",
    "ScriptWidget",
    "RefreshType",
    "ScriptInteractionType",
    "register_script_widgets",
    # Script Widget Refresh Scheduler
    "ScriptWidgetRefreshScheduler",
    "RefreshMode",
    "WidgetSchedule",
    # Script Modal Handlers
    "execute_script_modal",
    "parse_modal_json",
    "route_modal_action",
    "activate_script_widget",
    "ScriptModalError",
    "validate_modal_config",
    "normalize_modal_option",
    # Script Action Handlers
    "ActionStatus",
    "ActionResult",
    "execute_action_script",
    "execute_action_script_sync",
    "parse_action_result",
    "format_action_result",
    "display_command_output",
    "validate_action_key",
    "ScriptActionHandler",
    # Widget Picker
    "WidgetPickerModal",
    "PickerState",
    # System Monitor
    "render_sysmon",
    "get_system_collector",
    "SystemDataCollector",
    "get_sysmon_config",
    "color_threshold",
    "get_status_label",
    # Widget Interaction Handler
    "WidgetInteractionHandler",
    "WidgetInteractionType",
    "InteractionState",
    # Permission Status View
    "PermissionStatusView",
    # MCP Status View
    "MCPStatusView",
    "MCPToolDetailView",
    "render_mcp_status",
    "render_mcp_tool_detail",
    # Status Menu Renderer
    "StatusMenuRenderer",
]
