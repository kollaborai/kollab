"""Configuration widget definitions for modal UI."""

import importlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List

from kollabor_tui.design_system.theme import THEMES

logger = logging.getLogger(__name__)


class ConfigWidgetDefinitions:
    """Defines which config values get which widgets in the modal."""

    @staticmethod
    def get_available_plugins() -> List[Dict[str, Any]]:
        """Dynamically discover available plugins for configuration.

        Scans the plugins directory for *_plugin.py files and extracts
        metadata from each plugin class.

        Returns:
            List of plugin widget dictionaries.
        """
        plugins: list[dict[str, Any]] = []

        # Find plugins directory
        plugins_dir = Path(__file__).parent.parent.parent.parent.parent / "plugins"
        if not plugins_dir.exists():
            logger.warning(f"Plugins directory not found: {plugins_dir}")
            return plugins

        # Scan for plugin files
        for plugin_file in sorted(plugins_dir.glob("*_plugin.py")):
            try:
                module_name = plugin_file.stem  # e.g., "terminal_plugin"
                plugin_id = module_name.replace("_plugin", "")  # e.g., "terminal"

                # Try to import and get metadata
                try:
                    module = importlib.import_module(f"plugins.{module_name}")

                    # Find the plugin class (ends with "Plugin")
                    plugin_class = None
                    for name in dir(module):
                        obj = getattr(module, name)
                        if (
                            isinstance(obj, type)
                            and name.endswith("Plugin")
                            and name not in ("Plugin", "BasePlugin")
                        ):
                            plugin_class = obj
                            break

                    if plugin_class:
                        # Get name and description from class attributes
                        instance_name = getattr(plugin_class, "name", None)
                        if instance_name is None:
                            # Try to get from a temporary instance or use default
                            instance_name = plugin_id.replace("_", " ").title()

                        description = getattr(plugin_class, "description", None)
                        if description is None:
                            description = f"{instance_name} plugin"

                        # Use class-level name/description or instance defaults
                        display_name = (
                            instance_name
                            if isinstance(instance_name, str)
                            else plugin_id.replace("_", " ").title()
                        )

                        plugins.append(
                            {
                                "type": "checkbox",
                                "label": (
                                    display_name.replace("_", " ").title()
                                    if display_name == plugin_id
                                    else display_name
                                ),
                                "config_path": f"plugins.{plugin_id}.enabled",
                                "help": (
                                    description
                                    if isinstance(description, str)
                                    else f"{display_name} plugin"
                                ),
                            }
                        )
                        logger.debug(f"Discovered plugin: {plugin_id}")

                except ImportError as e:
                    logger.debug(f"Could not import plugin {module_name}: {e}")
                    # Still add it with basic info
                    plugins.append(
                        {
                            "type": "checkbox",
                            "label": plugin_id.replace("_", " ").title(),
                            "config_path": f"plugins.{plugin_id}.enabled",
                            "help": f"{plugin_id.replace('_', ' ').title()} plugin",
                        }
                    )

            except Exception as e:
                logger.error(f"Error processing plugin file {plugin_file}: {e}")

        logger.info(f"Discovered {len(plugins)} plugins for configuration")
        return plugins

    @staticmethod
    def get_available_themes() -> List[str]:
        """Get all available themes including custom user themes.

        Checks ~/.kollab/themes/ for JSON theme files and merges them
        with built-in themes.

        Theme JSON format:
        {
            "name": "mytheme",
            "primary": [[r, g, b], ...],
            "secondary": [[r, g, b], ...],
            "response_bg": [[r, g, b], ...],
            "input_bg": [[r, g, b], ...],
            "dark": [[r, g, b], ...],
            "user_tag": [r, g, b],
            "ai_tag": [r, g, b],
            "tool_tag": [r, g, b],
            "thinking_tag": [r, g, b],
            "success": [[r, g, b], ...],
            "error": [[r, g, b], ...],
            "warning": [[r, g, b], ...],
            "text": [r, g, b],
            "text_dim": [r, g, b]
        }

        Returns:
            List of theme names (built-in + custom).
        """
        # Start with built-in themes
        theme_names = list(THEMES.keys())

        # Check for custom themes in ~/.kollab/themes/
        from kollabor_config.config_utils import get_config_directory

        themes_dir = Path(get_config_directory()) / "themes"

        if themes_dir.exists():
            for theme_file in themes_dir.glob("*.json"):
                try:
                    with open(theme_file, "r") as f:
                        theme_data = json.load(f)

                    theme_name = theme_data.get("name")
                    if theme_name and theme_name not in theme_names:
                        theme_names.append(theme_name)
                        logger.debug(
                            f"Loaded custom theme: {theme_name} from {theme_file}"
                        )
                except Exception as e:
                    logger.warning(f"Error loading theme file {theme_file}: {e}")

        return sorted(theme_names)

    @staticmethod
    def get_plugin_config_sections() -> List[Dict[str, Any]]:
        """Dynamically collect config widget sections from plugins.

        Looks for get_config_widgets() method on each plugin class.

        Returns:
            List of section definitions from plugins.
        """
        sections: List[Dict[str, Any]] = []

        plugins_dir = Path(__file__).parent.parent.parent.parent.parent / "plugins"
        if not plugins_dir.exists():
            return sections

        discovery_targets: List[str] = []
        covered_subdirs: set = set()

        for plugin_file in sorted(plugins_dir.glob("*_plugin.py")):
            module_name = f"plugins.{plugin_file.stem}"
            discovery_targets.append(module_name)
            subdir_name = plugin_file.stem.replace("_plugin", "")
            covered_subdirs.add(subdir_name)

        for subdir in sorted(plugins_dir.iterdir()):
            if not subdir.is_dir() or subdir.name.startswith(("_", ".")):
                continue
            if subdir.name in covered_subdirs:
                continue
            if (subdir / "plugin.py").exists():
                discovery_targets.append(f"plugins.{subdir.name}.plugin")
            elif (subdir / "__init__.py").exists():
                discovery_targets.append(f"plugins.{subdir.name}")

        for module_name in discovery_targets:
            try:
                module = importlib.import_module(module_name)

                plugin_class = None
                for name in dir(module):
                    obj = getattr(module, name)
                    if (
                        isinstance(obj, type)
                        and name.endswith("Plugin")
                        and name not in ("Plugin", "BasePlugin")
                    ):
                        if hasattr(obj, "get_config_widgets"):
                            plugin_class = obj
                            break

                if plugin_class is None:
                    continue

                widget_section = plugin_class.get_config_widgets()
                if widget_section:
                    sections.append(widget_section)
                    logger.debug(f"Loaded config widgets from {plugin_class.__name__}")
            except Exception as e:
                logger.debug(f"Could not load config widgets from {module_name}: {e}")

        return sections

    @staticmethod
    def get_config_modal_definition() -> Dict[str, Any]:
        """Get the complete modal definition for /config command.

        Returns:
            Dictionary defining the modal layout and widgets.
        """
        # Get plugin widgets and available themes dynamically
        plugin_widgets = ConfigWidgetDefinitions.get_available_plugins()
        available_themes = ConfigWidgetDefinitions.get_available_themes()

        return {
            "title": "System Configuration",
            "footer": "↑↓ navigate • Enter toggle • / search • Ctrl+S save • Esc cancel",
            # Width and height are dynamic (terminal_width - 2, terminal_height - 4)
            "sections": [
                {
                    "title": "Terminal Settings",
                    "widgets": [
                        {
                            "type": "dropdown",
                            "label": "UI Width Mode",
                            "config_path": "terminal.global_width_mode",
                            "options": ["80%", "90%", "auto", "full"],
                            "help": "UI width as percentage of terminal, auto (width-4), or full",
                        },
                        {
                            "type": "slider",
                            "label": "Min Width",
                            "config_path": "terminal.global_width_min",
                            "min_value": 20,
                            "max_value": 80,
                            "step": 5,
                            "help": "Minimum UI width in columns",
                        },
                        {
                            "type": "slider",
                            "label": "Max Width",
                            "config_path": "terminal.global_width_max",
                            "min_value": 60,
                            "max_value": 200,
                            "step": 10,
                            "help": "Maximum UI width in columns (0 for no limit)",
                        },
                        {
                            "type": "slider",
                            "label": "Render FPS",
                            "config_path": "terminal.render_fps",
                            "min_value": 1,
                            "max_value": 60,
                            "step": 1,
                            "help": "Terminal refresh rate (1-60 FPS)",
                        },
                        {
                            "type": "slider",
                            "label": "Status Lines",
                            "config_path": "terminal.status_lines",
                            "min_value": 1,
                            "max_value": 10,
                            "step": 1,
                            "help": "Number of status lines to display",
                        },
                        {
                            "type": "dropdown",
                            "label": "Thinking Effect",
                            "config_path": "terminal.thinking_effect",
                            "options": ["shimmer", "pulse", "wave", "none"],
                            "help": "Visual effect for thinking animations",
                        },
                        {
                            "type": "dropdown",
                            "label": "UI Theme",
                            "config_path": "kollabor.ui.theme",
                            "options": available_themes,
                            "help": "Color theme for the interface",
                        },
                        {
                            "type": "dropdown",
                            "label": "Message Renderer",
                            "config_path": "kollabor.ui.renderer",
                            "options": ["clean", "modern", "simple"],
                            "help": "Rendering style: modern (gradients), clean (tags), simple (plain)",
                        },
                        {
                            "type": "dropdown",
                            "label": "Prompt Character",
                            "config_path": "kollabor.ui.prompt_char",
                            "options": [
                                "chevron",
                                "arrow",
                                "angle",
                                "dot",
                                "diamond",
                                "braille",
                                "dash",
                            ],
                            "help": "Input prompt: chevron ❯, arrow ▶, dot ●, diamond ◆, braille ⠠⠵",
                        },
                        {
                            "type": "slider",
                            "label": "Shimmer Speed",
                            "config_path": "terminal.shimmer_speed",
                            "min_value": 1,
                            "max_value": 10,
                            "step": 1,
                            "help": "Speed of shimmer animation effect",
                        },
                        {
                            "type": "checkbox",
                            "label": "Enable Render Cache",
                            "config_path": "terminal.render_cache_enabled",
                            "help": "Cache renders to reduce unnecessary terminal I/O when idle",
                        },
                        {
                            "type": "checkbox",
                            "label": "Tool Spinner Enabled",
                            "config_path": "terminal.tool_spinner_enabled",
                            "help": "Show animated spinner in tool tags during execution",
                        },
                        {
                            "type": "dropdown",
                            "label": "Tool Spinner Style",
                            "config_path": "terminal.tool_spinner_style",
                            "options": ["braille", "classic", "custom"],
                            "help": "Spinner animation style for tool execution",
                        },
                        {
                            "type": "slider",
                            "label": "Tool Spinner Speed (ms)",
                            "config_path": "terminal.tool_spinner_speed_ms",
                            "min_value": 50,
                            "max_value": 500,
                            "step": 10,
                            "help": "Speed of tool spinner animation (milliseconds per frame)",
                        },
                    ],
                },
                {
                    "title": "Input Settings",
                    "widgets": [
                        {
                            "type": "checkbox",
                            "label": "Ctrl+C Exit",
                            "config_path": "input.ctrl_c_exit",
                            "help": "Allow Ctrl+C to exit application",
                        },
                        {
                            "type": "checkbox",
                            "label": "Backspace Enabled",
                            "config_path": "input.backspace_enabled",
                            "help": "Enable backspace key for text editing",
                        },
                        {
                            "type": "slider",
                            "label": "History Limit",
                            "config_path": "input.history_limit",
                            "min_value": 10,
                            "max_value": 1000,
                            "step": 10,
                            "help": "Maximum number of history entries",
                        },
                    ],
                },
                {
                    "title": "Application Settings",
                    "widgets": [
                        {
                            "type": "text_input",
                            "label": "Application Name",
                            "config_path": "application.name",
                            "placeholder": "Kollab",
                            "help": "Display name for the application",
                        },
                        {
                            "type": "label",
                            "label": "Version",
                            "config_path": "application.version",
                            "help": "Application version (read-only, from pyproject.toml)",
                        },
                    ],
                },
                {
                    "title": "LLM Settings",
                    "widgets": [
                        {
                            "type": "slider",
                            "label": "Max History",
                            "config_path": "kollabor.llm.max_history",
                            "min_value": 10,
                            "max_value": 200,
                            "step": 10,
                            "help": "Maximum conversation history entries to keep",
                        },
                        {
                            "type": "checkbox",
                            "label": "Enable Streaming",
                            "config_path": "kollabor.llm.enable_streaming",
                            "help": "Stream responses as they arrive",
                        },
                        {
                            "type": "slider",
                            "label": "Processing Delay (sec)",
                            "config_path": "kollabor.llm.processing_delay",
                            "min_value": 0.0,
                            "max_value": 1.0,
                            "step": 0.1,
                            "help": "Delay between processing steps",
                        },
                        {
                            "type": "slider",
                            "label": "Thinking Delay (sec)",
                            "config_path": "kollabor.llm.thinking_delay",
                            "min_value": 0.0,
                            "max_value": 1.0,
                            "step": 0.1,
                            "help": "Delay for thinking animation display",
                        },
                    ],
                },
                {
                    "title": "Tool Execution Timeouts",
                    "widgets": [
                        {
                            "type": "slider",
                            "label": "Terminal Timeout (sec)",
                            "config_path": "kollabor.llm.terminal_timeout",
                            "min_value": 10,
                            "max_value": 300,
                            "step": 10,
                            "help": "Timeout for terminal commands in seconds",
                        },
                        {
                            "type": "slider",
                            "label": "MCP Timeout (sec)",
                            "config_path": "kollabor.llm.mcp_timeout",
                            "min_value": 10,
                            "max_value": 300,
                            "step": 10,
                            "help": "Timeout for MCP tool calls in seconds",
                        },
                    ],
                },
                {
                    "title": "Logging",
                    "widgets": [
                        {
                            "type": "dropdown",
                            "label": "Log Level",
                            "config_path": "logging.level",
                            "options": ["DEBUG", "INFO", "WARNING", "ERROR"],
                            "help": "Application logging verbosity",
                        }
                    ],
                },
                {
                    "title": "Hooks",
                    "widgets": [
                        {
                            "type": "slider",
                            "label": "Default Timeout (sec)",
                            "config_path": "hooks.default_timeout",
                            "min_value": 5,
                            "max_value": 120,
                            "step": 5,
                            "help": "Default hook execution timeout",
                        },
                        {
                            "type": "slider",
                            "label": "Default Retries",
                            "config_path": "hooks.default_retries",
                            "min_value": 0,
                            "max_value": 10,
                            "step": 1,
                            "help": "Number of retry attempts for failed hooks",
                        },
                    ],
                },
                {"title": "Plugin Settings", "widgets": plugin_widgets},
                # Plugin config sections are loaded dynamically below
            ]
            + ConfigWidgetDefinitions.get_plugin_config_sections(),
            "actions": [
                {
                    "key": "Ctrl+S",
                    "label": "Save",
                    "action": "save",
                    "style": "primary",
                },
                {
                    "key": "Escape",
                    "label": "Cancel",
                    "action": "cancel",
                    "style": "secondary",
                },
            ],
        }

    @staticmethod
    def create_widgets_from_definition(
        config_service, definition: Dict[str, Any]
    ) -> List[Any]:
        """Create widget instances from modal definition.

        Args:
            config_service: ConfigService for reading current values.
            definition: Modal definition dictionary.

        Returns:
            List of instantiated widgets.
        """
        widgets = []

        try:
            from kollabor_tui.widgets.checkbox import CheckboxWidget
            from kollabor_tui.widgets.dropdown import DropdownWidget
            from kollabor_tui.widgets.label import LabelWidget
            from kollabor_tui.widgets.slider import SliderWidget
            from kollabor_tui.widgets.text_input import TextInputWidget

            widget_classes = {
                "checkbox": CheckboxWidget,
                "dropdown": DropdownWidget,
                "text_input": TextInputWidget,
                "slider": SliderWidget,
                "label": LabelWidget,
            }

            for section in definition.get("sections", []):
                for widget_def in section.get("widgets", []):
                    widget_type = widget_def["type"]
                    widget_class = widget_classes.get(widget_type)

                    if not widget_class:
                        logger.error(f"Unknown widget type: {widget_type}")
                        continue

                    # Get current value from config (optional for labels)
                    config_path = widget_def.get("config_path", "")
                    if config_path:
                        current_value = config_service.get(config_path)
                    else:
                        # For label widgets, use the "value" field directly
                        current_value = widget_def.get("value", "")

                    # Create widget with configuration
                    widget = widget_class(
                        label=widget_def["label"],
                        config_path=config_path,
                        help_text=widget_def.get("help", ""),
                        current_value=current_value,
                        **{
                            k: v
                            for k, v in widget_def.items()
                            if k
                            not in ["type", "label", "config_path", "help", "value"]
                        },
                    )

                    widgets.append(widget)
                    logger.debug(f"Created {widget_type} widget for {config_path}")

        except Exception as e:
            logger.error(f"Error creating widgets from definition: {e}")

        logger.info(f"Created {len(widgets)} widgets from definition")
        return widgets

    @staticmethod
    def get_widget_navigation_info() -> Dict[str, str]:
        """Get navigation key information for modal help.

        Returns:
            Dictionary mapping keys to their descriptions.
        """
        return {
            "up_down": "Navigate between widgets",
            "left_right": "Adjust slider values",
            "enter": "Toggle checkbox",
            "space": "Toggle checkbox",
            "tab": "Next widget",
            "shift_tab": "Previous widget",
            "ctrl_s": "Save all changes",
            "escape": "Cancel and exit",
        }
