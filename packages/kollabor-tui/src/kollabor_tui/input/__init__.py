"""Input handling components for Kollab.

This package contains modular components extracted from the monolithic InputHandler.
Each component has a single responsibility and can be tested independently.
"""

from .command_mode_handler import CommandModeHandler
from .display_controller import DisplayController
from .hook_registrar import HookRegistrar
from .input_loop_manager import InputLoopManager
from .key_press_handler import KeyPressHandler
from .modal_controller import ModalController
from .paste_processor import PasteProcessor
from .status_modal_renderer import StatusModalRenderer

__all__ = [
    "StatusModalRenderer",
    "PasteProcessor",
    "DisplayController",
    "CommandModeHandler",
    "KeyPressHandler",
    "ModalController",
    "HookRegistrar",
    "InputLoopManager",
]
