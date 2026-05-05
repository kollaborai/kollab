"""Unit tests for PasteProcessor component.

Tests the extracted PasteProcessor independently from InputHandler.
"""

import unittest
from unittest.mock import AsyncMock, Mock

from kollabor_tui.input.paste_processor import PasteProcessor


class TestPasteProcessor(unittest.TestCase):
    """Test cases for PasteProcessor component."""

    def setUp(self):
        """Set up test fixtures."""
        self.mock_buffer_manager = Mock()
        self.mock_display_callback = AsyncMock()

        self.paste_processor = PasteProcessor(
            self.mock_buffer_manager, display_callback=self.mock_display_callback
        )

    def test_initialization(self):
        """Test PasteProcessor initializes correctly."""
        self.assertIsNotNone(self.paste_processor)
        self.assertEqual(self.paste_processor.buffer_manager, self.mock_buffer_manager)
        self.assertEqual(self.paste_processor._paste_bucket, {})
        self.assertEqual(self.paste_processor._paste_counter, 0)
        self.assertIsNone(self.paste_processor._current_paste_id)
        self.assertFalse(self.paste_processor.paste_detection_enabled)

    def test_start_new_paste(self):
        """Test starting a new paste."""
        chunk = "This is pasted content"
        current_time = 1000.0

        paste_id = self.paste_processor.start_new_paste(chunk, current_time)

        self.assertEqual(paste_id, "PASTE_1")
        self.assertEqual(self.paste_processor._paste_counter, 1)
        self.assertEqual(self.paste_processor._current_paste_id, "PASTE_1")
        self.assertEqual(self.paste_processor._paste_bucket["PASTE_1"], chunk)
        self.assertEqual(self.paste_processor._last_paste_time, current_time)

    def test_append_to_current_paste(self):
        """Test appending to current paste."""
        # Start a paste first
        self.paste_processor.start_new_paste("Initial content", 1000.0)

        # Append more content
        self.paste_processor.append_to_current_paste(" More content", 1000.05)

        self.assertEqual(
            self.paste_processor._paste_bucket["PASTE_1"],
            "Initial content More content",
        )

    def test_should_merge_paste_within_threshold(self):
        """Test merge detection within time threshold."""
        self.paste_processor.start_new_paste("Content", 1000.0)

        # Within 0.1s threshold
        self.assertTrue(self.paste_processor.should_merge_paste(1000.05))

    def test_should_merge_paste_outside_threshold(self):
        """Test merge detection outside time threshold."""
        self.paste_processor.start_new_paste("Content", 1000.0)

        # Outside 0.1s threshold
        self.assertFalse(self.paste_processor.should_merge_paste(1000.2))

    def test_should_merge_paste_no_current(self):
        """Test merge returns False when no current paste."""
        self.assertFalse(self.paste_processor.should_merge_paste(1000.0))

    def test_expand_paste_placeholders_single(self):
        """Test expanding a single paste placeholder."""
        # Set up paste bucket
        self.paste_processor._paste_bucket["PASTE_1"] = "Actual pasted content"

        message = "Before [Pasted #1 1 lines, 21 chars] After"
        expanded = self.paste_processor.expand_paste_placeholders(message)

        self.assertEqual(expanded, "Before Actual pasted content After")
        # Bucket should be cleared
        self.assertEqual(len(self.paste_processor._paste_bucket), 0)

    def test_expand_paste_placeholders_multiple(self):
        """Test expanding multiple paste placeholders."""
        self.paste_processor._paste_bucket["PASTE_1"] = "First paste"
        self.paste_processor._paste_bucket["PASTE_2"] = "Second paste"

        message = "[Pasted #1 1 lines, 11 chars] and [Pasted #2 1 lines, 12 chars]"
        expanded = self.paste_processor.expand_paste_placeholders(message)

        self.assertEqual(expanded, "First paste and Second paste")

    def test_expand_paste_placeholders_multiline(self):
        """Test expanding placeholder with multiline content."""
        self.paste_processor._paste_bucket["PASTE_1"] = "Line1\nLine2\nLine3"

        message = "[Pasted #1 3 lines, 17 chars]"
        expanded = self.paste_processor.expand_paste_placeholders(message)

        self.assertEqual(expanded, "Line1\nLine2\nLine3")

    def test_expand_paste_placeholders_no_match(self):
        """Test message without placeholders passes through."""
        message = "Regular message without placeholders"
        expanded = self.paste_processor.expand_paste_placeholders(message)

        self.assertEqual(expanded, message)

    def test_flush_paste_buffer_as_keystrokes_sync(self):
        """Test flushing paste buffer as keystrokes."""
        self.paste_processor._paste_buffer = ["h", "e", "l", "l", "o"]

        self.paste_processor._flush_paste_buffer_as_keystrokes_sync()

        # Should have called insert_char for each character
        self.assertEqual(self.mock_buffer_manager.insert_char.call_count, 5)

    def test_flush_paste_buffer_filters_non_printable(self):
        """Test that non-printable chars are filtered during flush."""
        self.paste_processor._paste_buffer = ["a", "\x00", "b", " ", "c"]

        self.paste_processor._flush_paste_buffer_as_keystrokes_sync()

        # Should skip \x00 but include space
        self.assertEqual(self.mock_buffer_manager.insert_char.call_count, 4)

    def test_process_simple_paste_sync(self):
        """Test processing simple paste with indicator."""
        self.paste_processor._paste_buffer = list("pasted content")

        self.paste_processor._process_simple_paste_sync()

        # Should have created indicator and inserted it
        self.assertTrue(self.mock_buffer_manager.insert_char.called)
        self.assertEqual(self.paste_processor._paste_counter, 1)
        self.assertEqual(self.paste_processor._paste_buffer, [])

    def test_process_simple_paste_cleans_bracketed_markers(self):
        """Test that bracketed paste markers are cleaned."""
        # Simulate content with bracketed paste markers
        self.paste_processor._paste_buffer = list("[200~content[201~")

        self.paste_processor._process_simple_paste_sync()

        # Should process without the markers
        self.assertTrue(self.mock_buffer_manager.insert_char.called)

    def test_paste_bucket_property(self):
        """Test paste_bucket property access."""
        self.paste_processor._paste_bucket["TEST"] = "content"

        self.assertEqual(self.paste_processor.paste_bucket, {"TEST": "content"})

    def test_current_paste_id_property(self):
        """Test current_paste_id property access."""
        self.paste_processor._current_paste_id = "PASTE_5"

        self.assertEqual(self.paste_processor.current_paste_id, "PASTE_5")

    def test_last_paste_time_property(self):
        """Test last_paste_time property access."""
        self.paste_processor._last_paste_time = 12345.67

        self.assertEqual(self.paste_processor.last_paste_time, 12345.67)


class TestPasteProcessorAsync(unittest.IsolatedAsyncioTestCase):
    """Async test cases for PasteProcessor."""

    async def asyncSetUp(self):
        """Set up test fixtures."""
        self.mock_buffer_manager = Mock()
        self.mock_display_callback = AsyncMock()

        self.paste_processor = PasteProcessor(
            self.mock_buffer_manager, display_callback=self.mock_display_callback
        )

    async def test_create_paste_placeholder(self):
        """Test creating paste placeholder."""
        # Set up paste content
        self.paste_processor._paste_bucket["PASTE_1"] = "Multi\nLine\nContent"

        await self.paste_processor.create_paste_placeholder("PASTE_1")

        # Should have inserted placeholder chars
        self.assertTrue(self.mock_buffer_manager.insert_char.called)
        # Should have called display callback
        self.mock_display_callback.assert_called_once_with(force_render=True)

    async def test_update_paste_placeholder(self):
        """Test updating paste placeholder (logs only for now)."""
        self.paste_processor._current_paste_id = "PASTE_1"
        self.paste_processor._paste_bucket["PASTE_1"] = "Updated content"

        # Should not raise
        await self.paste_processor.update_paste_placeholder()

    async def test_process_simple_paste(self):
        """Test async process_simple_paste."""
        self.paste_processor._paste_buffer = list("test content")

        await self.paste_processor.process_simple_paste()

        self.assertTrue(self.mock_buffer_manager.insert_char.called)
        self.mock_display_callback.assert_called_once_with(force_render=True)

    async def test_flush_paste_buffer_as_keystrokes(self):
        """Test async flush_paste_buffer_as_keystrokes."""
        self.paste_processor._paste_buffer = ["a", "b", "c"]

        await self.paste_processor.flush_paste_buffer_as_keystrokes()

        self.assertEqual(self.mock_buffer_manager.insert_char.call_count, 3)

    async def test_simple_paste_detection_disabled_by_default(self):
        """Test that paste detection is disabled by default."""
        self.assertFalse(self.paste_processor.paste_detection_enabled)


if __name__ == "__main__":
    unittest.main()
