"""Widget Showcase - Interactive gallery for browsing and testing widgets.

This is like Storybook but for terminal apps. It provides an interactive
gallery to view, test, and explore all available widgets.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

from kollabor_tui.design_system import S
from kollabor_tui.widgets import (
    CheckboxWidget,
    DropdownWidget,
    LabelWidget,
    SliderWidget,
    TextInputWidget,
)


@dataclass
class WidgetShowcaseItem:
    """Represents a widget in the showcase.

    Attributes:
        name: Widget class name
        description: Human-readable description
        category: Category grouping (core/advanced)
        example_config: Example configuration for demo
        usage_example: Code usage example
        features: List of key features
    """

    name: str
    description: str
    category: str
    example_config: dict
    usage_example: str
    features: List[str]


class WidgetShowcase:
    """Interactive widget gallery for terminal UI components.

    Provides categorized display of all widgets with interactive demos,
    code examples, and feature lists. Similar to Storybook for web apps.

    Usage:
        showcase = WidgetShowcase()
        showcase.display_gallery()  # Show all widgets
        showcase.display_widget("MultiSelectWidget")  # Show specific widget
    """

    def __init__(self):
        """Initialize widget showcase with all available widgets."""
        self.widgets: Dict[str, WidgetShowcaseItem] = {}
        self._register_widgets()

    def _register_widgets(self):
        """Register all available widgets with showcase data."""

        # === CORE WIDGETS ===

        self.widgets["CheckboxWidget"] = WidgetShowcaseItem(
            name="CheckboxWidget",
            description="Boolean toggle with checkmark symbol",
            category="core",
            example_config={"label": "Enable Debug Mode", "default": True},
            usage_example="""
widget = CheckboxWidget(
    config={"label": "Enable Debug Mode", "default": True},
    config_path="kollabor.debug.enabled"
)
""",
            features=[
                "Simple on/off toggle",
                "Configurable default state",
                "Visual checkmark indicator",
                "Keyboard and mouse support",
            ],
        )

        self.widgets["DropdownWidget"] = WidgetShowcaseItem(
            name="DropdownWidget",
            description="Select single option from list with expandable menu",
            category="core",
            example_config={
                "label": "Select Model",
                "options": ["gpt-4", "gpt-3.5-turbo", "claude-3-opus"],
                "default": "gpt-4",
            },
            usage_example="""
widget = DropdownWidget(
    config={
        "label": "Select Model",
        "options": ["gpt-4", "gpt-3.5-turbo", "claude-3-opus"],
        "default": "gpt-4"
    },
    config_path="kollabor.llm.model"
)
""",
            features=[
                "Single selection from list",
                "Expandable dropdown menu",
                "Arrow key navigation",
                "Search/filter capability",
            ],
        )

        self.widgets["TextInputWidget"] = WidgetShowcaseItem(
            name="TextInputWidget",
            description="Single-line text input with cursor",
            category="core",
            example_config={
                "label": "API Key",
                "placeholder": "sk-...",
                "max_length": 100,
            },
            usage_example="""
widget = TextInputWidget(
    config={
        "label": "API Key",
        "placeholder": "sk-...",
        "max_length": 100
    },
    config_path="kollabor.api.key"
)
""",
            features=[
                "Single-line text entry",
                "Cursor position tracking",
                "Character limit support",
                "Placeholder text",
            ],
        )

        self.widgets["SliderWidget"] = WidgetShowcaseItem(
            name="SliderWidget",
            description="Numeric slider with visual progress bar",
            category="core",
            example_config={
                "label": "Temperature",
                "min_value": 0.0,
                "max_value": 2.0,
                "step": 0.1,
                "default": 0.7,
            },
            usage_example="""
widget = SliderWidget(
    config={
        "label": "Temperature",
        "min_value": 0.0,
        "max_value": 2.0,
        "step": 0.1,
        "default": 0.7
    },
    config_path="kollabor.llm.temperature"
)
""",
            features=[
                "Visual slider with bar",
                "Configurable range and step",
                "Precise numeric control",
                "Real-time value display",
            ],
        )

        self.widgets["LabelWidget"] = WidgetShowcaseItem(
            name="LabelWidget",
            description="Static text display for information",
            category="core",
            example_config={
                "label": "Welcome to Kollab",
                "text": "This is a label widget",
            },
            usage_example="""
widget = LabelWidget(
    config={
        "label": "Welcome",
        "text": "This is a label widget"
    },
    config_path=""
)
""",
            features=[
                "Static text display",
                "No user interaction",
                "Color support",
                "Multi-line text",
            ],
        )

        # === ADVANCED WIDGETS ===

        self.widgets["MultiSelectWidget"] = WidgetShowcaseItem(
            name="MultiSelectWidget",
            description="Select multiple items from list with checkboxes",
            category="advanced",
            example_config={
                "label": "Active Plugins",
                "options": ["enhanced-input", "hook-monitor", "terminal-integration"],
                "selected_indices": [0, 2],
                "min_selections": 1,
                "max_selections": 3,
            },
            usage_example="""
widget = MultiSelectWidget(
    config={
        "label": "Active Plugins",
        "options": ["enhanced-input", "hook-monitor", "terminal"],
        "selected_indices": [0, 2],
        "min_selections": 1
    },
    config_path="kollabor.plugins.active"
)
""",
            features=[
                "Multiple selection support",
                "Checkbox indicators",
                "Min/max constraints",
                "Select all / Deselect all (Ctrl+A/D)",
                "Return indices or values",
            ],
        )

        self.widgets["TextAreaWidget"] = WidgetShowcaseItem(
            name="TextAreaWidget",
            description="Multi-line text input with scrolling",
            category="advanced",
            example_config={
                "label": "System Prompt",
                "rows": 10,
                "cols": 60,
                "max_length": 5000,
                "placeholder": "Enter your system prompt...",
                "line_wrap": True,
            },
            usage_example="""
widget = TextAreaWidget(
    config={
        "label": "System Prompt",
        "rows": 10,
        "cols": 60,
        "max_length": 5000
    },
    config_path="kollabor.system_prompt"
)
""",
            features=[
                "Multi-line text input",
                "Line wrapping support",
                "Scroll navigation",
                "Line numbers option",
                "Character counter",
                "Keyboard shortcuts (Ctrl+D, Ctrl+K)",
            ],
        )

        self.widgets["SearchableDropdownWidget"] = WidgetShowcaseItem(
            name="SearchableDropdownWidget",
            description="Dropdown with real-time search filtering",
            category="advanced",
            example_config={
                "label": "Select Command",
                "options": [
                    "/save",
                    "/profile",
                    "/terminal",
                    "/matrix",
                    "/version",
                    "/branch",
                    "/help",
                    "/status",
                ],
                "case_sensitive": False,
                "min_chars_to_filter": 1,
            },
            usage_example="""
widget = SearchableDropdownWidget(
    config={
        "label": "Select Command",
        "options": ["/save", "/profile", "/terminal", ...],
        "case_sensitive": False
    },
    config_path="kollabor.default_command"
)
""",
            features=[
                "Real-time search filtering",
                "Highlight matching text",
                "Match count display",
                "Case-sensitive option",
                "Perfect for long lists",
            ],
        )

        self.widgets["SpinBoxWidget"] = WidgetShowcaseItem(
            name="SpinBoxWidget",
            description="Numeric input with increment/decrement buttons",
            category="advanced",
            example_config={
                "label": "Max Tokens",
                "min_value": 1,
                "max_value": 128000,
                "step": 1000,
                "decimal_places": 0,
                "unit": "tokens",
            },
            usage_example="""
widget = SpinBoxWidget(
    config={
        "label": "Max Tokens",
        "min_value": 1,
        "max_value": 128000,
        "step": 1000,
        "unit": "tokens"
    },
    config_path="kollabor.llm.max_tokens"
)
""",
            features=[
                "Increment/decrement buttons",
                "Precise numeric entry",
                "Configurable decimals",
                "Unit display",
                "Range constraints",
                "Page up/down for larger steps",
            ],
        )

        self.widgets["TreeViewWidget"] = WidgetShowcaseItem(
            name="TreeViewWidget",
            description="Hierarchical data display with expand/collapse",
            category="advanced",
            example_config={
                "label": "Project Structure",
                "tree_data": {
                    "label": "kollab",
                    "children": [
                        {
                            "label": "core",
                            "children": [
                                {"label": "llm"},
                                {"label": "ui"},
                                {"label": "io"},
                            ],
                        },
                        {"label": "plugins"},
                        {"label": "tests"},
                    ],
                },
                "expand_depth": 1,
                "show_icons": True,
            },
            usage_example="""
from kollabor_tui.widgets import TreeNode, TreeViewWidget

root = TreeNode(id="root", label="Project")
child = TreeNode(id="child1", label="core")
root.add_child(child)

widget = TreeViewWidget(
    config={"label": "Structure", "tree_data": root},
    config_path=""
)
""",
            features=[
                "Hierarchical navigation",
                "Expand/collapse nodes",
                "Cursor position tracking",
                "File/directory icons",
                "Select multiple nodes",
                "Expand/collapse all (Ctrl+E/C)",
            ],
        )

        self.widgets["ProgressWidget"] = WidgetShowcaseItem(
            name="ProgressWidget",
            description="Progress bar with percentage and ETA",
            category="advanced",
            example_config={
                "label": "Processing Files",
                "current": 45,
                "total": 100,
                "show_percentage": True,
                "status_text": "Processing...",
                "use_gradient": True,
                "animate": True,
            },
            usage_example="""
widget = ProgressWidget(
    config={
        "label": "Processing",
        "current": 45,
        "total": 100,
        "status_text": "Processing..."
    },
    config_path=""
)
widget.update_progress(50, 100)
""",
            features=[
                "Visual progress bar",
                "Percentage display",
                "Fraction display (45/100)",
                "ETA calculation",
                "Color gradients",
                "Animated progress",
                "Status messages",
            ],
        )

        self.widgets["FileBrowserWidget"] = WidgetShowcaseItem(
            name="FileBrowserWidget",
            description="Filesystem navigation and path selection",
            category="advanced",
            example_config={
                "label": "Select Export Path",
                "start_dir": "~",
                "file_filter": "*.json",
                "show_hidden": False,
                "select_dirs_only": False,
            },
            usage_example="""
widget = FileBrowserWidget(
    config={
        "label": "Select File",
        "start_dir": "~",
        "file_filter": "*.json"
    },
    config_path="export.path"
)
""",
            features=[
                "Directory navigation",
                "File filtering with patterns",
                "Hidden file toggle (Ctrl+H)",
                "File size display",
                "Parent directory navigation",
                "Home directory (Ctrl+~)",
                "Directory-only mode",
            ],
        )

    def get_widget(self, name: str) -> Optional[WidgetShowcaseItem]:
        """Get widget showcase item by name.

        Args:
            name: Widget class name.

        Returns:
            WidgetShowcaseItem or None if not found.
        """
        return self.widgets.get(name)

    def get_all_widgets(self) -> List[WidgetShowcaseItem]:
        """Get all registered widgets.

        Returns:
            List of all WidgetShowcaseItem objects.
        """
        return list(self.widgets.values())

    def get_widgets_by_category(self, category: str) -> List[WidgetShowcaseItem]:
        """Get widgets filtered by category.

        Args:
            category: Category name ("core" or "advanced").

        Returns:
            List of widgets in category.
        """
        return [w for w in self.widgets.values() if w.category == category]

    def get_categories(self) -> List[str]:
        """Get all unique categories.

        Returns:
            List of category names.
        """
        return sorted(set(w.category for w in self.widgets.values()))

    def render_widget_info(self, name: str) -> str:
        """Render detailed widget information as text.

        Args:
            name: Widget name.

        Returns:
            Formatted text with widget details.
        """
        widget = self.get_widget(name)
        if not widget:
            return f"Widget '{name}' not found"

        lines = [
            f"=== {widget.name} ===",
            "",
            f"Category: {widget.category.title()}",
            f"Description: {widget.description}",
            "",
            "Features:",
        ]

        for feature in widget.features:
            lines.append(f"  - {feature}")

        lines.extend(
            [
                "",
                "Example Config:",
                "```",
            ]
        )

        # Pretty print config
        for line in self._format_dict(widget.example_config).split("\n"):
            lines.append(line)

        lines.extend(
            [
                "```",
                "",
                "Usage Example:",
                "```python",
            ]
        )

        lines.extend(widget.usage_example.strip().split("\n"))
        lines.extend(
            [
                "```",
                "",
            ]
        )

        return "\n".join(lines)

    def _format_dict(self, d: dict, indent: int = 0) -> str:
        """Format dictionary for display.

        Args:
            d: Dictionary to format.
            indent: Indentation level.

        Returns:
            Formatted string.
        """
        lines = []
        prefix = "  " * indent

        for key, value in d.items():
            if isinstance(value, dict):
                lines.append(f"{prefix}{key}:")
                lines.append(self._format_dict(value, indent + 1))
            elif isinstance(value, list):
                lines.append(f"{prefix}{key}: [")
                for item in value:
                    lines.append(f"{prefix}  {item},")
                lines.append(f"{prefix}]")
            else:
                lines.append(f"{prefix}{key}: {repr(value)}")

        return "\n".join(lines)

    def render_modern_preview(self, width: int = 50) -> str:
        """Render live preview of all core widgets with modern styling.

        Args:
            width: Width of rendered widgets.

        Returns:
            Multi-line string with all widget previews.
        """
        lines = []
        lines.append(f"{S.BOLD}=== Widget Design System Preview ==={S.RESET}")
        lines.append("")

        # Checkbox (checked, focused)
        cb = CheckboxWidget(
            config={"label": "Enable dark mode", "type": "checkbox"},
            config_path="demo.enabled",
        )
        cb.focused = True
        cb_output = cb.render_modern(width=width)
        lines.append(f"{S.DIM}checkbox (focused, checked):{S.RESET}")
        lines.extend(cb_output[0].split("\n") if cb_output else [])
        lines.append("")

        # Checkbox (unchecked, unfocused)
        cb2 = CheckboxWidget(
            config={"label": "Show timestamps", "type": "checkbox", "default": False},
            config_path="demo.timestamps",
        )
        cb2.focused = False
        cb2_output = cb2.render_modern(width=width)
        lines.append(f"{S.DIM}checkbox (unfocused, unchecked):{S.RESET}")
        lines.extend(cb2_output[0].split("\n") if cb2_output else [])
        lines.append("")

        # Dropdown (focused)
        dd = DropdownWidget(
            config={
                "label": "Model",
                "type": "dropdown",
                "options": ["gpt-4", "gpt-3.5-turbo", "claude-3"],
            },
            config_path="demo.model",
        )
        dd.focused = True
        dd_output = dd.render_modern(width=width)
        lines.append(f"{S.DIM}dropdown (focused):{S.RESET}")
        lines.extend(dd_output[0].split("\n") if dd_output else [])
        lines.append("")

        # Slider (focused)
        sl = SliderWidget(
            config={
                "label": "Temperature",
                "type": "slider",
                "min": 0.0,
                "max": 2.0,
                "step": 0.1,
            },
            config_path="demo.temperature",
        )
        sl.set_value(0.7)
        sl.focused = True
        sl_output = sl.render_modern(width=width)
        lines.append(f"{S.DIM}slider (focused, value=0.7):{S.RESET}")
        lines.extend(sl_output[0].split("\n") if sl_output else [])
        lines.append("")

        # Text input (focused)
        ti = TextInputWidget(
            config={"label": "API Key", "type": "text", "placeholder": "sk-..."},
            config_path="demo.api_key",
        )
        ti.set_value("sk-abc123")
        ti.focused = True
        ti_output = ti.render_modern(width=width)
        lines.append(f"{S.DIM}text input (focused):{S.RESET}")
        lines.extend(ti_output[0].split("\n") if ti_output else [])
        lines.append("")

        # Labels with different styles
        for style in ["success", "warning", "error", "info"]:
            label = LabelWidget(
                label=style.title(), value=f"This is a {style} message", config_path=""
            )
            label_output = label.render_modern(style=style, width=width)
            lines.append(f"{S.DIM}label ({style}):{S.RESET}")
            lines.extend(label_output[0].split("\n") if label_output else [])
            lines.append("")

        return "\n".join(lines)


# Singleton instance
_showcase_instance = None


def get_widget_showcase() -> WidgetShowcase:
    """Get singleton widget showcase instance.

    Returns:
        WidgetShowcase instance.
    """
    global _showcase_instance
    if _showcase_instance is None:
        _showcase_instance = WidgetShowcase()
    return _showcase_instance
