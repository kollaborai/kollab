"""Widget system for modal UI components.

This package provides interactive widgets for use in modal dialogs:

Core Widgets:
- BaseWidget: Foundation class for all widgets
- CheckboxWidget: Boolean toggle with ✓ symbol
- DropdownWidget: Option selection with ▼ indicator
- TextInputWidget: Text entry with cursor ▌
- SliderWidget: Numeric slider with █░ visual bar
- LabelWidget: Static text display

Advanced Widgets:
- MultiSelectWidget: Select multiple items from a list with checkboxes
- TextAreaWidget: Multi-line text input with scrolling
- SearchableDropdownWidget: Dropdown with real-time search filtering
- SpinBoxWidget: Numeric input with increment/decrement buttons
- TreeViewWidget: Hierarchical data display with expand/collapse
- ProgressWidget: Progress bar with percentage and ETA
- FileBrowserWidget: Filesystem navigation and path selection

All widgets integrate with the design system and configuration management.
"""

from .base_widget import BaseWidget
from .checkbox import CheckboxWidget
from .dropdown import DropdownWidget
from .file_browser import FileBrowserWidget
from .label import LabelWidget

# Advanced widgets
from .multi_select import MultiSelectWidget
from .progress import ProgressWidget
from .searchable_dropdown import SearchableDropdownWidget
from .slider import SliderWidget
from .spin_box import SpinBoxWidget
from .text_area import TextAreaWidget
from .text_input import TextInputWidget
from .tree_view import TreeNode, TreeViewWidget

__all__ = [
    # Core widgets
    "BaseWidget",
    "CheckboxWidget",
    "DropdownWidget",
    "TextInputWidget",
    "SliderWidget",
    "LabelWidget",
    # Advanced widgets
    "MultiSelectWidget",
    "TextAreaWidget",
    "SearchableDropdownWidget",
    "SpinBoxWidget",
    "TreeViewWidget",
    "TreeNode",
    "ProgressWidget",
    "FileBrowserWidget",
]
