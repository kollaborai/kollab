"""System commands plugin package.

Provides core system commands like /help, /config, /status, /setup,
/agent, /skill, /model, /llm, /cd, /permissions, /version, /restart.
"""

from .plugin import SystemCommandsPlugin

__all__ = ["SystemCommandsPlugin"]
