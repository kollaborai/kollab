"""Configuration widget definitions for modal UI."""

import importlib
import importlib.util
import json
import logging
import pkgutil
import sys
import types
from pathlib import Path
from typing import Any, Dict, List

from kollabor_tui.design_system.theme import THEMES

logger = logging.getLogger(__name__)


class ConfigWidgetDefinitions:
    """Defines which config values get which widgets in the modal."""

    @staticmethod
    def _packaged_plugins_dir() -> Path | None:
        module_path = Path(__file__).resolve()
        for parent in module_path.parents:
            candidate = parent / "plugins"
            if candidate.is_dir() and (candidate / "__init__.py").exists():
                return candidate
        return None

    @staticmethod
    def _module_is_from_plugins_dir(module: Any, plugins_dir: Path) -> bool:
        try:
            plugins_dir = plugins_dir.resolve()
            module_file = getattr(module, "__file__", None)
            if module_file:
                return Path(module_file).resolve().is_relative_to(plugins_dir)

            module_paths = getattr(module, "__path__", [])
            for module_path in module_paths:
                resolved_path = Path(module_path).resolve()
                if resolved_path == plugins_dir or resolved_path.is_relative_to(
                    plugins_dir
                ):
                    return True
        except Exception as e:
            logger.debug(f"Could not verify config plugin module root: {e}")

        return False

    @staticmethod
    def _drop_stale_module_cache(plugins_dir: Path, module_path: str) -> None:
        parts = ["plugins", *module_path.split(".")]
        stale_roots: list[str] = []

        for index in range(2, len(parts) + 1):
            module_name = ".".join(parts[:index])
            module = sys.modules.get(module_name)
            if module and not ConfigWidgetDefinitions._module_is_from_plugins_dir(
                module,
                plugins_dir,
            ):
                stale_roots.append(module_name)

        if not stale_roots:
            return

        for module_name in list(sys.modules):
            if any(
                module_name == stale_root or module_name.startswith(f"{stale_root}.")
                for stale_root in stale_roots
            ):
                del sys.modules[module_name]

    @staticmethod
    def _prepare_plugins_import_root(
        plugins_dir: Path,
        module_path: str | None = None,
    ) -> None:
        try:
            plugins_dir = plugins_dir.resolve()

            current_plugins = sys.modules.get("plugins")
            if current_plugins and hasattr(current_plugins, "__path__"):
                root_path = str(plugins_dir)
                search_paths = [
                    str(Path(path).resolve())
                    for path in getattr(current_plugins, "__path__", [])
                    if Path(path).resolve() != plugins_dir
                ]
                current_plugins.__path__ = [root_path, *search_paths]  # type: ignore[attr-defined]
                if module_path:
                    ConfigWidgetDefinitions._drop_stale_module_cache(
                        plugins_dir,
                        module_path,
                    )
                importlib.invalidate_caches()
                return

            init_file = plugins_dir / "__init__.py"
            if init_file.exists():
                spec = importlib.util.spec_from_file_location(
                    "plugins",
                    init_file,
                    submodule_search_locations=[str(plugins_dir)],
                )
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    sys.modules["plugins"] = module
                    spec.loader.exec_module(module)
            else:
                module = types.ModuleType("plugins")
                module.__file__ = str(plugins_dir)
                module.__path__ = [str(plugins_dir)]  # type: ignore[attr-defined]
                sys.modules["plugins"] = module

            if module_path:
                ConfigWidgetDefinitions._drop_stale_module_cache(
                    plugins_dir,
                    module_path,
                )
            importlib.invalidate_caches()
        except Exception as e:
            logger.debug(f"Could not prepare config plugin import root: {e}")

    @staticmethod
    def _prepare_plugin_module_import(module_name: str) -> None:
        plugins_dir = ConfigWidgetDefinitions._packaged_plugins_dir()
        if plugins_dir is None or not module_name.startswith("plugins."):
            return

        ConfigWidgetDefinitions._prepare_plugins_import_root(
            plugins_dir,
            module_name.removeprefix("plugins."),
        )

    @staticmethod
    def _discover_plugin_modules() -> List[tuple[str, str]]:
        """Discover plugin modules from source checkouts and installed packages."""
        targets: list[tuple[str, str]] = []

        plugins_dir = ConfigWidgetDefinitions._packaged_plugins_dir()
        if plugins_dir is not None:
            ConfigWidgetDefinitions._prepare_plugins_import_root(plugins_dir)
            covered_subdirs: set[str] = set()

            for plugin_file in sorted(plugins_dir.glob("*_plugin.py")):
                plugin_id = plugin_file.stem.replace("_plugin", "")
                targets.append((plugin_id, f"plugins.{plugin_file.stem}"))
                covered_subdirs.add(plugin_id)

            for subdir in sorted(plugins_dir.iterdir()):
                if not subdir.is_dir() or subdir.name.startswith(("_", ".")):
                    continue
                if subdir.name in covered_subdirs:
                    continue
                if (subdir / "plugin.py").exists():
                    targets.append((subdir.name, f"plugins.{subdir.name}.plugin"))
                    covered_subdirs.add(subdir.name)

            return ConfigWidgetDefinitions._dedupe_plugin_targets(targets)

        try:
            import plugins as plugins_package
        except ImportError as e:
            logger.debug(f"Could not import plugins package: {e}")
            return ConfigWidgetDefinitions._dedupe_plugin_targets(targets)

        for module_info in pkgutil.iter_modules(plugins_package.__path__):
            if module_info.name.startswith(("_", ".")):
                continue

            if module_info.name.endswith("_plugin"):
                plugin_id = module_info.name.removesuffix("_plugin")
                targets.append((plugin_id, f"plugins.{module_info.name}"))
                continue

            if module_info.ispkg:
                plugin_module = f"plugins.{module_info.name}.plugin"
                try:
                    if importlib.util.find_spec(plugin_module) is not None:
                        targets.append((module_info.name, plugin_module))
                except (ImportError, ModuleNotFoundError, ValueError):
                    logger.debug(f"Could not find plugin module {plugin_module}")

        return ConfigWidgetDefinitions._dedupe_plugin_targets(targets)

    @staticmethod
    def _dedupe_plugin_targets(targets: List[tuple[str, str]]) -> List[tuple[str, str]]:
        seen_plugin_ids: set[str] = set()
        seen_modules: set[str] = set()
        deduped: list[tuple[str, str]] = []

        for plugin_id, module_name in targets:
            if plugin_id in seen_plugin_ids or module_name in seen_modules:
                continue
            seen_plugin_ids.add(plugin_id)
            seen_modules.add(module_name)
            deduped.append((plugin_id, module_name))

        return deduped

    @staticmethod
    def _find_plugin_class(
        module: Any, require_config_widgets: bool = False
    ) -> type | None:
        for name in dir(module):
            obj = getattr(module, name)
            if (
                isinstance(obj, type)
                and name.endswith("Plugin")
                and name not in ("Plugin", "BasePlugin")
            ):
                if require_config_widgets and not hasattr(obj, "get_config_widgets"):
                    continue
                return obj

        return None

    @staticmethod
    def get_available_plugins() -> List[Dict[str, Any]]:
        """Dynamically discover available plugins for configuration.

        Scans the plugins package for plugin modules and extracts metadata
        from each plugin class.

        Returns:
            List of plugin widget dictionaries.
        """
        plugins: list[dict[str, Any]] = []

        for (
            plugin_id,
            module_name,
        ) in ConfigWidgetDefinitions._discover_plugin_modules():
            try:
                try:
                    ConfigWidgetDefinitions._prepare_plugin_module_import(module_name)
                    module = importlib.import_module(module_name)
                    plugin_class = ConfigWidgetDefinitions._find_plugin_class(module)

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
                logger.error(f"Error processing plugin {module_name}: {e}")

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

        for _, module_name in ConfigWidgetDefinitions._discover_plugin_modules():
            try:
                ConfigWidgetDefinitions._prepare_plugin_module_import(module_name)
                module = importlib.import_module(module_name)
                plugin_class = ConfigWidgetDefinitions._find_plugin_class(
                    module,
                    require_config_widgets=True,
                )

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
                        {
                            "type": "checkbox",
                            "label": "Auto Update Kollab",
                            "config_path": "kollabor.updates.auto_update_enabled",
                            "help": "Automatically update Kollab when a newer release is available",
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
