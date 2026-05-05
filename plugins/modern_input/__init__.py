"""Modern Input Plugin for Kollab.

Provides customizable input rendering using the design system.
Supports multi-line input with scroll indicators.
"""

from plugins.modern_input.config import ModernInputConfig
from plugins.modern_input.cursor_manager import CursorManager
from plugins.modern_input.renderer import ModernInputRenderer

__all__ = ["ModernInputConfig", "CursorManager", "ModernInputRenderer"]
