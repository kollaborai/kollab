"""Unit tests for DisplayController component.

Tests the extracted DisplayController independently from InputHandler.
"""

import unittest
from unittest.mock import AsyncMock, Mock

from kollabor_tui.input.display_controller import DisplayController


class TestDisplayController(unittest.TestCase):
    """Test cases for DisplayController component."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_renderer = Mock()
        self.mock_renderer.input_buffer = ""
        self.mock_renderer.cursor_position = 0
        self.mock_renderer.render_active_area = AsyncMock()

        self.mock_buffer_manager = Mock()
        self.mock_buffer_manager.get_display_info.return_value = ("test content", 5)

        self.mock_error_handler = AsyncMock()

        self.display_controller = DisplayController(
            self.mock_renderer, self.mock_buffer_manager, self.mock_error_handler
        )

    def test_initialization(self):
        """Test DisplayController initializes correctly."""
        self.assertIsNotNone(self.display_controller)
        self.assertEqual(self.display_controller.renderer, self.mock_renderer)
        self.assertEqual(
            self.display_controller.buffer_manager, self.mock_buffer_manager
        )
        self.assertFalse(self.display_controller.rendering_paused)
        self.assertEqual(self.display_controller._last_cursor_pos, 0)

    def test_pause_rendering(self):
        """Test pausing rendering."""
        self.assertFalse(self.display_controller.rendering_paused)

        self.display_controller.pause_rendering()

        self.assertTrue(self.display_controller.rendering_paused)

    def test_resume_rendering(self):
        """Test resuming rendering."""
        self.display_controller.rendering_paused = True

        self.display_controller.resume_rendering()

        self.assertFalse(self.display_controller.rendering_paused)

    def test_last_cursor_pos_property(self):
        """Test last_cursor_pos property."""
        self.display_controller.last_cursor_pos = 42

        self.assertEqual(self.display_controller.last_cursor_pos, 42)

    def test_initialization_without_error_handler(self):
        """Test DisplayController works without error handler."""
        controller = DisplayController(
            self.mock_renderer, self.mock_buffer_manager, error_handler=None
        )

        self.assertIsNotNone(controller)
        self.assertIsNone(controller.error_handler)


class TestDisplayControllerAsync(unittest.IsolatedAsyncioTestCase):
    """Async test cases for DisplayController."""

    async def asyncSetUp(self):
        """Set up test fixtures."""
        self.mock_renderer = Mock()
        self.mock_renderer.input_buffer = ""
        self.mock_renderer.cursor_position = 0
        self.mock_renderer.render_active_area = AsyncMock()

        self.mock_buffer_manager = Mock()
        self.mock_buffer_manager.get_display_info.return_value = ("test content", 5)

        self.mock_error_handler = AsyncMock()

        self.display_controller = DisplayController(
            self.mock_renderer, self.mock_buffer_manager, self.mock_error_handler
        )

    async def test_update_display_basic(self):
        """Test basic display update."""
        await self.display_controller.update_display()

        self.mock_buffer_manager.get_display_info.assert_called_once()
        self.assertEqual(self.mock_renderer.input_buffer, "test content")
        self.assertEqual(self.mock_renderer.cursor_position, 5)

    async def test_update_display_skipped_when_paused(self):
        """Test display update skipped when rendering is paused."""
        self.display_controller.rendering_paused = True
        self.mock_buffer_manager.get_display_info.reset_mock()

        await self.display_controller.update_display()

        # Should not update buffer when paused
        self.mock_buffer_manager.get_display_info.assert_not_called()

    async def test_update_display_forced_when_paused(self):
        """Test display update works when forced, even if paused."""
        self.display_controller.rendering_paused = True

        await self.display_controller.update_display(force_render=True)

        self.mock_buffer_manager.get_display_info.assert_called_once()
        self.mock_renderer.render_active_area.assert_called_once()

    async def test_update_display_force_render(self):
        """Test force render triggers immediate rendering."""
        await self.display_controller.update_display(force_render=True)

        self.mock_renderer.render_active_area.assert_called_once()

    async def test_update_display_updates_cursor_position(self):
        """Test cursor position tracking."""
        self.mock_buffer_manager.get_display_info.return_value = ("content", 10)

        await self.display_controller.update_display()

        self.assertEqual(self.display_controller._last_cursor_pos, 10)

    async def test_update_display_cursor_not_updated_if_same(self):
        """Test cursor position not updated if unchanged."""
        self.display_controller._last_cursor_pos = 5
        self.mock_buffer_manager.get_display_info.return_value = ("content", 5)

        await self.display_controller.update_display()

        # Position should remain 5
        self.assertEqual(self.display_controller._last_cursor_pos, 5)

    async def test_update_display_error_handling(self):
        """Test error handling during display update."""
        self.mock_buffer_manager.get_display_info.side_effect = Exception("Test error")

        # Should not raise, should handle error gracefully
        await self.display_controller.update_display()

        self.mock_error_handler.handle_error.assert_called_once()

    async def test_update_display_error_without_handler(self):
        """Test error handling when no error handler is set."""
        controller = DisplayController(
            self.mock_renderer, self.mock_buffer_manager, error_handler=None
        )
        self.mock_buffer_manager.get_display_info.side_effect = Exception("Test error")

        # Should not raise even without error handler
        await controller.update_display()

    async def test_force_render_with_sync_method(self):
        """Test force render falls back to sync method if async not available."""
        # Set up sync render method
        self.mock_renderer.render_active_area = Mock()  # Sync mock

        await self.display_controller.update_display(force_render=True)

        self.mock_renderer.render_active_area.assert_called_once()

    async def test_force_render_with_render_input_fallback(self):
        """Test force render falls back to render_input if render_active_area not available."""
        # Remove render_active_area
        del self.mock_renderer.render_active_area
        self.mock_renderer.render_input = AsyncMock()

        await self.display_controller.update_display(force_render=True)

        self.mock_renderer.render_input.assert_called_once()


if __name__ == "__main__":
    unittest.main()
