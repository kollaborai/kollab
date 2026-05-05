"""System commands plugin package.

Provides core system commands like /help, /config, /status, /profile,
/agent, /skill, /model, /cd, /permissions, /version, /restart.
"""

from .plugin import SystemCommandsPlugin

__all__ = ["SystemCommandsPlugin"]
