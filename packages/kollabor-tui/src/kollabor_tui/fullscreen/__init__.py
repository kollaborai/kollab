"""Full-screen plugin framework for terminal takeover.

This module provides a reusable framework for plugins to take complete
terminal control using the proven modal system architecture.
"""

from .manager import FullScreenManager
from .plugin import FullScreenPlugin
from .renderer import FullScreenRenderer
from .session import FullScreenSession

__all__ = [
    "FullScreenManager",
    "FullScreenRenderer",
    "FullScreenPlugin",
    "FullScreenSession",
]
