"""Configuration widget definitions for modal UI."""

import json
import logging
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
    def get_available_plugins() -> List[Dict[str, Any]]:
        """Build plugin rows from the same registry used by runtime loading."""
        from kollabor_plugins.registry import plugin_entries

        rows = []
        for entry in plugin_entries(ConfigWidgetDefinitions._packaged_plugins_dir()):
            plugin_id = entry["id"]
            name = entry.get("name") or plugin_id.replace("_", " ").title()
            kind = entry.get("kind", "built-in")
            path = f"plugin-registry.{plugin_id}.enabled" if kind == "installed" else f"plugins.{plugin_id}.enabled"
            rows.append(
                {
                    "type": "checkbox",
                    "label": name,
                    "display_name": name,
                    "plugin_kind": kind,
                    "config_path": path,
                    "help": f"{name} — {entry.get('state', 'unknown')}",
                }
            )
        return rows

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
                        logger.debug(f"Loaded custom theme: {theme_name} from {theme_file}")
                except Exception as e:
                    logger.warning(f"Error loading theme file {theme_file}: {e}")

        return sorted(theme_names)

    @staticmethod
    def get_plugin_config_sections() -> List[Dict[str, Any]]:
        """Collect plugin settings from the common registry listing."""
        from kollabor_plugins.registry import plugin_entries

        sections = []
        for entry in plugin_entries(ConfigWidgetDefinitions._packaged_plugins_dir()):
            if entry.get("kind") == "installed":
                widgets = []
                for service in entry.get("services", []):
                    if isinstance(service, dict) and isinstance(service.get("name"), str):
                        widgets.append(
                            {
                                "type": "checkbox",
                                "label": f"Background: {service['name']}",
                                "config_path": f"plugin-service.{entry['id']}.{service['name']}",
                                "help": f"State: {service.get('state', 'unknown')}; toggle to start or stop",
                            }
                        )
                for widget in entry.get("settings", []):
                    if isinstance(widget, dict) and isinstance(widget.get("config_path"), str):
                        widgets.append(
                            {**widget, "config_path": f"plugin-values.{entry['id']}.{widget['config_path']}"}
                        )
                if widgets:
                    sections.append({"title": entry.get("name", entry["id"]), "widgets": widgets})
            elif isinstance(entry.get("settings_section"), dict):
                sections.append(entry["settings_section"])
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
                        {
                            "type": "slider",
                            "label": "Paste Placeholder Threshold",
                            "config_path": "input.paste_min_chars",
                            "min_value": 50,
                            "max_value": 5000,
                            "step": 50,
                            "help": "Pastes with at least this many characters show as "
                            "[Pasted #N]; shorter ones go in as text",
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
                        {
                            "type": "checkbox",
                            "label": "Restart Agent Button",
                            "config_path": "kollabor.updates.restart_agents",
                            "help": (
                                "Offer a Restart Agent button in the web chat of an agent that runs an older "
                                "version than the engine; with it off the chat points at /restart"
                            ),
                        },
                    ],
                },
                {
                    "title": "Code And Terminal",
                    "widgets": [
                        {
                            "type": "checkbox",
                            "label": "Code, Git And Terminal Views",
                            "config_path": "kollabor.code.enabled",
                            "help": "Off: this daemon refuses every call from the web UI's Code, Git and Terminal",
                        },
                        {
                            "type": "checkbox",
                            "label": "Read Only",
                            "config_path": "kollabor.code.read_only",
                            "help": "On: no file writes, git writes or terminal input from the web UI",
                        },
                        {
                            "type": "checkbox",
                            "label": "Terminal",
                            "config_path": "kollabor.code.terminal.enabled",
                            "help": "Off: the web UI opens no shells on this computer",
                        },
                        {
                            "type": "checkbox",
                            "label": "Pass Secrets To Terminal",
                            "config_path": "kollabor.code.terminal.pass_secret_env",
                            "help": "Let new terminals see variables named like TOKEN, SECRET, API_KEY or PASSWORD",
                        },
                        {
                            "type": "checkbox",
                            "label": "Pass Kollab Variables To Terminal",
                            "config_path": "kollabor.code.terminal.pass_kollab_env",
                            "help": "Let new terminals see KOLLAB_* and KOLLABOR_* variables",
                        },
                        {
                            "type": "text_input",
                            "label": "Terminal Shell",
                            "config_path": "kollabor.code.terminal.shell",
                            "help": "Shell program, such as /bin/bash. Empty uses $SHELL",
                        },
                        {
                            "type": "checkbox",
                            "label": "Terminal Login Shell",
                            "config_path": "kollabor.code.terminal.login",
                            "help": "Start the shell with -l so it reads your login files",
                        },
                        {
                            "type": "text_input",
                            "label": "Terminal Start Command",
                            "config_path": "kollabor.code.terminal.start_command",
                            "help": "One line typed into every new terminal, such as: source .venv/bin/activate",
                        },
                        {
                            "type": "spinbox",
                            "label": "Most Terminals",
                            "config_path": "kollabor.code.terminal.max_shells",
                            "min_value": 1,
                            "max_value": 64,
                            "step": 1,
                            "help": "Shells this daemon keeps at once",
                        },
                        {
                            "type": "spinbox",
                            "label": "Idle Terminal Minutes",
                            "config_path": "kollabor.code.terminal.idle_reap_minutes",
                            "min_value": 0,
                            "max_value": 1440,
                            "step": 5,
                            "help": "Minutes a terminal nobody watches stays alive. 0 keeps it until the daemon stops",
                        },
                        {
                            "type": "spinbox",
                            "label": "Terminal Scrollback KB",
                            "config_path": "kollabor.code.terminal.scrollback_kb",
                            "min_value": 16,
                            "max_value": 65536,
                            "step": 64,
                            "help": "Output kept for a viewer that reconnects",
                        },
                        {
                            "type": "spinbox",
                            "label": "Largest Editable File KB",
                            "config_path": "kollabor.code.files.max_file_kb",
                            "min_value": 16,
                            "max_value": 1048576,
                            "step": 256,
                            "help": "Largest file the editor opens or saves",
                        },
                        {
                            "type": "checkbox",
                            "label": "Show Hidden Files",
                            "config_path": "kollabor.code.files.show_hidden",
                            "help": "List dot-files and skipped folders such as node_modules and .git in the file tree",
                        },
                        {
                            "type": "checkbox",
                            "label": "Follow Symlinks Outside The Folder",
                            "config_path": "kollabor.code.files.follow_symlinks",
                            "help": "Read and list a symlink even when it points outside the folder",
                        },
                        {
                            "type": "checkbox",
                            "label": "Commit With Files Outside The Folder",
                            "config_path": "kollabor.code.git.allow_outside_workspace",
                            "help": "Let Commit go ahead when staged files sit outside the session's folder",
                        },
                        {
                            "type": "checkbox",
                            "label": "Git Read Only",
                            "config_path": "kollabor.code.git.read_only",
                            "help": "On: the Git view can look but not stage, commit, switch or push",
                        },
                    ],
                },
                {
                    "title": "LLM Settings",
                    "widgets": [
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
    def create_widgets_from_definition(config_service, definition: Dict[str, Any]) -> List[Any]:
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
                            if k not in ["type", "label", "config_path", "help", "value"]
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
