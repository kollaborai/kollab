#!/usr/bin/env python3
"""Unit tests for status modal functionality."""

import asyncio
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from kollabor_events.models import CommandMode, UIConfig
from kollabor_tui.input_handler import InputHandler
from kollabor_tui.key_parser import KeyPress


class TestStatusModal(unittest.TestCase):
    """Test cases for status modal functionality."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_event_bus = Mock()
        self.mock_renderer = Mock()
        # Add terminal_state mock for status modal line generation
        self.mock_renderer.terminal_state = Mock()
        self.mock_renderer.terminal_state.width = 80
        self.mock_config = Mock()

        # Mock config methods with proper types
        def mock_get(key, default=None):
            config_values = {
                "input.polling_delay": 0.01,
                "input.error_delay": 0.1,
                "input.input_buffer_limit": 100000,
                "input.history_limit": 100,
                "input.error_threshold": 10,
                "input.error_window_minutes": 5,
                "input.max_errors": 100,
            }
            return config_values.get(key, default)

        self.mock_config.get.side_effect = mock_get

        # Create input handler with mocks
        self.input_handler = InputHandler(
            self.mock_event_bus, self.mock_renderer, self.mock_config
        )

        # Set up initial state
        self.input_handler.command_mode = CommandMode.NORMAL
        self.input_handler.current_status_modal_config = None

    def test_status_modal_esc_key_handling(self):
        """Test that Esc key properly closes status modal."""

        async def run_test():
            # Set up status modal mode
            ui_config = UIConfig(
                type="status_modal",
                title="Test Modal",
                modal_config={"footer": "Press Esc to close"},
            )

            self.input_handler.command_mode = CommandMode.STATUS_MODAL
            self.input_handler.current_status_modal_config = ui_config

            # Create Escape key press
            esc_key = KeyPress(name="Escape", char="", code=27, type="key")

            # Handle the key press
            result = await self.input_handler._handle_status_modal_keypress(esc_key)

            # Verify modal was closed
            self.assertTrue(result)
            self.assertEqual(self.input_handler.command_mode, CommandMode.NORMAL)
            self.assertIsNone(self.input_handler.current_status_modal_config)

        asyncio.run(run_test())

    def test_status_modal_ctrl_c_handling(self):
        """Test that Ctrl+C properly closes status modal."""

        async def run_test():
            # Set up status modal mode
            ui_config = UIConfig(type="status_modal", title="Test Modal")
            self.input_handler.command_mode = CommandMode.STATUS_MODAL
            self.input_handler.current_status_modal_config = ui_config

            # Create Ctrl+C key press
            ctrl_c_key = KeyPress(name="", char=chr(3), code=3, type="key")

            # Handle the key press
            result = await self.input_handler._handle_status_modal_keypress(ctrl_c_key)

            # Verify modal was closed
            self.assertTrue(result)
            self.assertEqual(self.input_handler.command_mode, CommandMode.NORMAL)
            self.assertIsNone(self.input_handler.current_status_modal_config)

        asyncio.run(run_test())

    def test_status_modal_enter_key_handling(self):
        """Test that Enter key properly closes status modal."""

        async def run_test():
            # Set up status modal mode
            ui_config = UIConfig(type="status_modal", title="Test Modal")
            self.input_handler.command_mode = CommandMode.STATUS_MODAL
            self.input_handler.current_status_modal_config = ui_config

            # Create Enter key press
            enter_key = KeyPress(name="Enter", char="\r", code=13, type="key")

            # Handle the key press
            result = await self.input_handler._handle_status_modal_keypress(enter_key)

            # Verify modal was closed
            self.assertTrue(result)
            self.assertEqual(self.input_handler.command_mode, CommandMode.NORMAL)
            self.assertIsNone(self.input_handler.current_status_modal_config)

        asyncio.run(run_test())

    def test_status_modal_trigger_handling(self):
        """Test status modal trigger event handling."""
        ui_config = UIConfig(
            type="status_modal",
            title="Available Commands",
            modal_config={"sections": []},
        )

        event_data = {"ui_config": ui_config}

        # Mock the enter status modal method
        self.input_handler._enter_status_modal_mode = AsyncMock()

        # Handle the trigger
        result = self.input_handler._handle_status_modal_trigger(event_data)

        # Verify trigger was handled
        self.assertIsNotNone(result)

    def test_status_modal_line_generation(self):
        """Test status modal line generation."""
        ui_config = UIConfig(
            type="status_modal",
            title="Available Commands",
            modal_config={
                "sections": [
                    {
                        "title": "System Commands",
                        "commands": [
                            {"name": "/help", "description": "Show help"},
                            {"name": "/config", "description": "Open config"},
                        ],
                    }
                ],
                "footer": "Press Esc to close",
            },
        )

        lines = self.input_handler._generate_status_modal_lines(ui_config)

        # Verify lines were generated
        self.assertIsInstance(lines, list)
        self.assertGreater(len(lines), 0)

        # Strip ANSI escape codes before checking content
        import re

        content = "\n".join(lines)
        clean_content = re.sub(r"\x1b\[[0-9;]*m", "", content)
        # Check for expected content in stripped text
        self.assertIn("/help", clean_content)
        self.assertIn("/config", clean_content)
        self.assertIn("Press Esc to close", clean_content)


if __name__ == "__main__":
    # Run tests
    unittest.main()
