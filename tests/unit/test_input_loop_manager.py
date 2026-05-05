"""Unit tests for InputLoopManager component."""

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch


class TestInputLoopManager(unittest.TestCase):
    """Test cases for InputLoopManager."""

    def setUp(self):
        """Set up test fixtures."""
        # Mock dependencies
        self.mock_renderer = MagicMock()
        self.mock_renderer.terminal_state.current_mode = MagicMock(value="raw")

        self.mock_key_parser = MagicMock()
        self.mock_key_parser.check_for_standalone_escape.return_value = None
        self.mock_key_parser._reset_escape_state = MagicMock()

        self.mock_error_handler = MagicMock()
        self.mock_error_handler.clear_old_errors.return_value = 0

        self.mock_paste_processor = MagicMock()
        self.mock_paste_processor.should_merge_paste.return_value = False
        self.mock_paste_processor.start_new_paste.return_value = "PASTE_1"
        self.mock_paste_processor.create_paste_placeholder = AsyncMock()
        self.mock_paste_processor.update_paste_placeholder = AsyncMock()
        self.mock_paste_processor.append_to_current_paste = MagicMock()

        self.mock_config = MagicMock()
        self.mock_config.get.side_effect = lambda key, default: default

        # Import here to avoid import errors during collection
        from kollabor_tui.input.input_loop_manager import InputLoopManager

        self.manager = InputLoopManager(
            renderer=self.mock_renderer,
            key_parser=self.mock_key_parser,
            error_handler=self.mock_error_handler,
            paste_processor=self.mock_paste_processor,
            config=self.mock_config,
        )

    def test_initialization(self):
        """Test InputLoopManager initializes correctly."""
        self.assertFalse(self.manager.running)
        self.assertEqual(self.manager.polling_delay, 0.01)
        self.assertEqual(self.manager.error_delay, 0.1)
        self.assertIsNone(self.manager._process_character_callback)

    def test_set_callbacks(self):
        """Test callback setting."""
        mock_process = MagicMock()
        mock_keypress = MagicMock()
        mock_command = MagicMock()
        mock_hooks = MagicMock()
        mock_mode = MagicMock()

        self.manager.set_callbacks(
            process_character=mock_process,
            handle_key_press=mock_keypress,
            handle_command_mode_keypress=mock_command,
            register_hooks=mock_hooks,
            get_command_mode=mock_mode,
        )

        self.assertEqual(self.manager._process_character_callback, mock_process)
        self.assertEqual(self.manager._handle_key_press_callback, mock_keypress)
        self.assertEqual(
            self.manager._handle_command_mode_keypress_callback, mock_command
        )
        self.assertEqual(self.manager._register_hooks_callback, mock_hooks)
        self.assertEqual(self.manager._get_command_mode_callback, mock_mode)

    def test_set_buffer_manager(self):
        """Test buffer manager reference setting."""
        mock_buffer = MagicMock()
        self.manager.set_buffer_manager(mock_buffer)
        self.assertEqual(self.manager._buffer_manager, mock_buffer)

    def test_is_escape_sequence_with_escape(self):
        """Test escape sequence detection with ESC character."""
        self.assertTrue(self.manager._is_escape_sequence("\x1b[A"))
        self.assertTrue(self.manager._is_escape_sequence("\x1b[B"))
        self.assertTrue(self.manager._is_escape_sequence("\x1bOP"))

    def test_is_escape_sequence_without_escape(self):
        """Test escape sequence detection with normal text."""
        self.assertFalse(self.manager._is_escape_sequence("hello"))
        self.assertFalse(self.manager._is_escape_sequence("a"))
        self.assertFalse(self.manager._is_escape_sequence(""))

    def test_is_escape_sequence_empty(self):
        """Test escape sequence detection with empty string."""
        self.assertFalse(self.manager._is_escape_sequence(""))
        self.assertFalse(self.manager._is_escape_sequence(None))

    def test_win_key_map_exists(self):
        """Test Windows key map contains expected mappings."""
        from kollabor_tui.input.input_loop_manager import InputLoopManager

        # Arrow keys
        self.assertEqual(InputLoopManager.WIN_KEY_MAP[72], b"\x1b[A")  # Up
        self.assertEqual(InputLoopManager.WIN_KEY_MAP[80], b"\x1b[B")  # Down
        self.assertEqual(InputLoopManager.WIN_KEY_MAP[75], b"\x1b[D")  # Left
        self.assertEqual(InputLoopManager.WIN_KEY_MAP[77], b"\x1b[C")  # Right

        # Navigation keys
        self.assertEqual(InputLoopManager.WIN_KEY_MAP[71], b"\x1b[H")  # Home
        self.assertEqual(InputLoopManager.WIN_KEY_MAP[79], b"\x1b[F")  # End

        # Function keys
        self.assertEqual(InputLoopManager.WIN_KEY_MAP[59], b"\x1bOP")  # F1
        self.assertEqual(InputLoopManager.WIN_KEY_MAP[68], b"\x1b[21~")  # F10


class TestInputLoopManagerAsync(unittest.TestCase):
    """Async test cases for InputLoopManager."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_renderer = MagicMock()
        self.mock_renderer.terminal_state.current_mode = MagicMock(value="raw")
        self.mock_renderer.enter_raw_mode = MagicMock()
        self.mock_renderer.exit_raw_mode = MagicMock()

        self.mock_key_parser = MagicMock()
        self.mock_key_parser.check_for_standalone_escape.return_value = None
        self.mock_key_parser._reset_escape_state = MagicMock()

        self.mock_error_handler = MagicMock()
        self.mock_error_handler.clear_old_errors.return_value = 0
        self.mock_error_handler.handle_error = AsyncMock()

        self.mock_paste_processor = MagicMock()
        self.mock_paste_processor.should_merge_paste.return_value = False
        self.mock_paste_processor.start_new_paste.return_value = "PASTE_1"
        self.mock_paste_processor.create_paste_placeholder = AsyncMock()
        self.mock_paste_processor.update_paste_placeholder = AsyncMock()
        self.mock_paste_processor.append_to_current_paste = MagicMock()

        self.mock_config = MagicMock()
        self.mock_config.get.side_effect = lambda key, default: default

        from kollabor_tui.input.input_loop_manager import InputLoopManager

        self.manager = InputLoopManager(
            renderer=self.mock_renderer,
            key_parser=self.mock_key_parser,
            error_handler=self.mock_error_handler,
            paste_processor=self.mock_paste_processor,
            config=self.mock_config,
        )

    def test_start_enters_raw_mode(self):
        """Test start() enters raw mode and registers hooks."""

        async def run_test():
            mock_register = AsyncMock()
            self.manager.set_callbacks(
                process_character=AsyncMock(),
                handle_key_press=AsyncMock(),
                handle_command_mode_keypress=AsyncMock(),
                register_hooks=mock_register,
                get_command_mode=MagicMock(),
            )

            # Make _input_loop stop immediately
            async def stop_loop():
                self.manager.running = False

            with patch.object(self.manager, "_input_loop", stop_loop):
                await self.manager.start()

            self.mock_renderer.enter_raw_mode.assert_called_once()
            mock_register.assert_called_once()

        asyncio.run(run_test())

    def test_stop_exits_raw_mode(self):
        """Test stop() exits raw mode and performs cleanup."""

        async def run_test():
            self.manager.running = True
            await self.manager.stop()

            self.assertFalse(self.manager.running)
            self.mock_renderer.exit_raw_mode.assert_called_once()

        asyncio.run(run_test())

    def test_cleanup_clears_errors(self):
        """Test cleanup() clears old errors."""

        async def run_test():
            self.mock_error_handler.clear_old_errors.return_value = 5
            await self.manager.cleanup()

            self.mock_error_handler.clear_old_errors.assert_called_once()
            self.mock_key_parser._reset_escape_state.assert_called_once()

        asyncio.run(run_test())

    def test_handle_paste_chunk_new_paste(self):
        """Test _handle_paste_chunk creates new paste."""

        async def run_test():
            self.mock_paste_processor.should_merge_paste.return_value = False

            await self.manager._handle_paste_chunk("large paste content here")

            self.mock_paste_processor.start_new_paste.assert_called_once()
            self.mock_paste_processor.create_paste_placeholder.assert_called_once_with(
                "PASTE_1"
            )

        asyncio.run(run_test())

    def test_handle_paste_chunk_merge_paste(self):
        """Test _handle_paste_chunk merges with existing paste."""

        async def run_test():
            self.mock_paste_processor.should_merge_paste.return_value = True

            await self.manager._handle_paste_chunk("more paste content")

            self.mock_paste_processor.append_to_current_paste.assert_called_once()
            self.mock_paste_processor.update_paste_placeholder.assert_called_once()

        asyncio.run(run_test())

    def test_route_escape_key_normal_mode(self):
        """Test _route_escape_key routes to handle_key_press in normal mode."""

        async def run_test():
            from kollabor_events.models import CommandMode

            mock_keypress = AsyncMock()
            self.manager.set_callbacks(
                process_character=AsyncMock(),
                handle_key_press=mock_keypress,
                handle_command_mode_keypress=AsyncMock(),
                register_hooks=AsyncMock(),
                get_command_mode=lambda: CommandMode.NORMAL,
            )

            mock_esc_key = MagicMock()
            await self.manager._route_escape_key(mock_esc_key)

            mock_keypress.assert_called_once_with(mock_esc_key)

        asyncio.run(run_test())

    def test_route_escape_key_modal_mode(self):
        """Test _route_escape_key routes to command mode handler in modal mode."""

        async def run_test():
            from kollabor_events.models import CommandMode

            mock_command = AsyncMock()
            self.manager.set_callbacks(
                process_character=AsyncMock(),
                handle_key_press=AsyncMock(),
                handle_command_mode_keypress=mock_command,
                register_hooks=AsyncMock(),
                get_command_mode=lambda: CommandMode.MODAL,
            )

            mock_esc_key = MagicMock()
            await self.manager._route_escape_key(mock_esc_key)

            mock_command.assert_called_once_with(mock_esc_key)

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
