"""Vulture whitelist for dynamic code patterns.

This file contains code patterns that vulture should ignore because they are
called dynamically at runtime (via getattr, decorators, plugin system, etc.).
"""

# ==============================================================================
# PLUGIN SYSTEM
# ==============================================================================


# Base plugin methods called dynamically
class BasePlugin:
    def __init__(self):
        pass

    def initialize(self):
        """Called during plugin initialization."""
        pass

    def register_hooks(self):
        """Called to register event hooks."""
        pass

    def shutdown(self):
        """Called during plugin shutdown."""
        pass

    def get_status_line(self):
        """Called to get status line information."""
        pass

    def get_default_config(self):
        """Called to get default configuration."""
        pass


# ==============================================================================
# EVENT HANDLERS (called via event bus)
# ==============================================================================


# Hook handlers - called by event bus
def on_pre_user_input():
    pass


def on_post_user_input():
    pass


def on_pre_api_request():
    pass


def on_post_api_response():
    pass


def on_tool_execution():
    pass


def on_error():
    pass


# Generic event handler pattern
def on_event():
    pass


def handle_event():
    pass


def _handle_event():
    pass


# ==============================================================================
# COMMAND HANDLERS (called via command system)
# ==============================================================================


# Command handlers - called via getattr in command system
def cmd_help():
    pass


def cmd_save():
    pass


def cmd_config():
    pass


def cmd_clear():
    pass


def do_action():
    pass


# ==============================================================================
# ABSTRACT METHODS (intentional placeholders)
# ==============================================================================


# Abstract methods that raise NotImplementedError
def abstract_method():
    raise NotImplementedError


# ==============================================================================
# TEST FIXTURES (called by pytest)
# ==============================================================================


# Test fixtures and methods
def test_something():
    pass


def setup():
    pass


def teardown():
    pass


def setup_module():
    pass


def teardown_module():
    pass


# ==============================================================================
# WIDGET SYSTEM (called via reflection)
# ==============================================================================


class Widget:
    def render(self):
        """Called to render widget."""
        pass

    def render_modern(self):
        """Called to render modern style widget."""
        pass

    def handle_input(self):
        """Called to handle user input."""
        pass

    def update(self):
        """Called to update widget state."""
        pass


# ==============================================================================
# FULLSCREEN PLUGINS (called via plugin system)
# ==============================================================================


class FullscreenPlugin:
    def render_frame(self):
        """Called each frame to render."""
        pass

    def handle_key(self):
        """Called to handle keyboard input."""
        pass

    def on_activate(self):
        """Called when plugin is activated."""
        pass

    def on_deactivate(self):
        """Called when plugin is deactivated."""
        pass


# ==============================================================================
# STATUS VIEWS (called via registry)
# ==============================================================================


class StatusView:
    def render_status(self):
        """Called to render status information."""
        pass

    def get_priority(self):
        """Called to get display priority."""
        pass


# ==============================================================================
# MCP TOOLS (called via MCP protocol)
# ==============================================================================


# MCP tool handlers
def tool_handler():
    pass


def mcp_tool():
    pass


# ==============================================================================
# DECORATORS (applied to functions/methods)
# ==============================================================================


# Decorator functions
def hook():
    pass


def command():
    pass


def tool():
    pass


def validator():
    pass


# ==============================================================================
# DYNAMIC IMPORTS (imported at runtime)
# ==============================================================================

# Variables used in dynamic imports
PLUGIN_CLASSES = []
COMMAND_REGISTRY = {}
HOOK_REGISTRY = {}
