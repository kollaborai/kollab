"""AltView framework for terminal alternate-buffer views.

Provides a lifecycle-managed system for plugins that take full terminal
control via the alternate screen buffer. Views can be stacked, suspended,
resumed, and run background tasks while off-screen.
"""

from .base import AltView, AltViewMetadata, AltViewState
from .display_queue import DisplayQueue
from .session import AltViewSession
from .stack_manager import AltViewStackManager, SessionInfo

__all__ = [
    "AltView",
    "AltViewMetadata",
    "AltViewSession",
    "AltViewStackManager",
    "AltViewState",
    "DisplayQueue",
    "SessionInfo",
]
