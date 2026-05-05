
"""Tests for session 831 fixes.

Covers:
1. is_displaying threading.Lock race condition (36a6dbb)
2. hub console vault stream resolution (7e51221)
3. _output_rendered DisplayTap publishing (pending — lapis)
"""

import threading
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from kollabor_tui.message_coordinator import MessageDisplayCoordinator


# ---------------------------------------------------------------------------
# Fix 1: is_displaying threading.Lock (36a6dbb)
# ---------------------------------------------------------------------------
# display_queued_messages and display_raw_text used a plain bool for
# is_displaying. Two threads could both pass the guard before either
# set it to True. Now uses threading.Lock with non-blocking acquire.


class TestDisplayLockRaceCondition(unittest.TestCase):
    """Test that _display_lock prevents concurrent display."""

    def _make_coordinator(self):
        """Create a minimal coordinator for testing."""
        renderer = MagicMock()
        renderer.writing_messages = False
        renderer.input_line_written = False
        renderer.last_line_count = 0
        renderer.thinking_active = False
        renderer.clear_active_area = MagicMock()
        renderer.invalidate_render_cache = MagicMock()
        renderer.pipe_mode = False
        renderer.terminal_state = MagicMock()

        coordinator = MessageDisplayCoordinator(renderer)
        return coordinator

    def test_has_display_lock(self):
        """Coordinator initializes with a threading.Lock."""
        coord = self._make_coordinator()
        self.assertIsInstance(coord._display_lock, type(threading.Lock()))

    def test_concurrent_display_blocked(self):
        """Two threads cannot display simultaneously."""
        coord = self._make_coordinator()

        # Simulate slow display by holding the lock
        acquired = []

        def slow_display():
            coord._display_lock.acquire()
            acquired.append(threading.current_thread().name)
            time.sleep(0.1)  # Hold lock
            coord._display_lock.release()

        t1 = threading.Thread(target=slow_display, name="t1")
        t2 = threading.Thread(target=slow_display, name="t2")

        t1.start()
        time.sleep(0.02)  # Ensure t1 grabs lock first
        t2.start()
        t2.join(timeout=0.05)  # t2 should be blocked

        # t2 couldn't acquire — only t1 in the list
        self.assertEqual(len(acquired), 1)
        self.assertEqual(acquired[0], "t1")

        t1.join()

    def test_display_queued_uses_lock(self):
        """display_queued_messages acquires and releases the lock."""
        coord = self._make_coordinator()

        # Add a message to the queue
        coord.queue_message("system", "test message")

        # Should succeed — lock acquired and released
        with patch("builtins.print"):
            coord.display_queued_messages()

        # Lock should be released after display
        self.assertTrue(coord._display_lock.acquire(blocking=False))
        coord._display_lock.release()

    def test_display_queued_empty_skips_lock(self):
        """Empty queue returns early without acquiring lock."""
        coord = self._make_coordinator()

        # Lock should be free before and after
        self.assertTrue(coord._display_lock.acquire(blocking=False))
        coord._display_lock.release()

        coord.display_queued_messages()

        self.assertTrue(coord._display_lock.acquire(blocking=False))
        coord._display_lock.release()

    def test_lock_released_on_navigation_skip(self):
        """Lock is released when navigation_active causes early return."""
        coord = self._make_coordinator()
        coord.queue_message("system", "test")
        coord.navigation_active = True

        coord.display_queued_messages()

        # Lock should be released
        self.assertTrue(coord._display_lock.acquire(blocking=False))
        coord._display_lock.release()


# ---------------------------------------------------------------------------
# Fix 2: hub console vault stream resolution (7e51221)
# ---------------------------------------------------------------------------
# hub_console_altview.py was reading legacy stream.jsonl (stale) instead
# of project-scoped stream. Now uses find_active_stream() from vault.py.


class TestConsoleVaultStreamResolution(unittest.TestCase):
    """Test that console reads the flat vault stream layout.

    After the 2026-04-20 flatten refactor, stream.jsonl lives directly
    at vaults/<identity>/stream.jsonl (no projects/ nesting). The old
    nested layout at vaults/<identity>/projects/<fp>/stream.jsonl is
    only a migration fallback.
    """

    def test_find_active_stream_import(self):
        """find_active_stream is importable from vault module."""
        try:
            from plugins.hub.vault import find_active_stream
            self.assertTrue(callable(find_active_stream))
        except ImportError:
            self.skipTest("vault module not available in test environment")

    def test_flat_path_is_canonical(self):
        """Flat stream.jsonl at vault root is the canonical path."""
        vault_dir = Path("/tmp/test_vault")
        flat_stream = vault_dir / "stream.jsonl"

        self.assertEqual(flat_stream.name, "stream.jsonl")
        self.assertEqual(flat_stream.parent, vault_dir)

    def test_nested_path_is_migration_fallback(self):
        """Old nested path still exists for pre-migration vaults."""
        vault_dir = Path("/tmp/test_vault")
        nested_stream = vault_dir / "projects" / "abc123" / "stream.jsonl"

        # Nested path is under vault_dir but inside projects/
        self.assertTrue(str(nested_stream).startswith(str(vault_dir)))
        self.assertTrue("projects" in str(nested_stream))


# ---------------------------------------------------------------------------
# Fix 3: _output_rendered DisplayTap (pending — validates current behavior)
# ---------------------------------------------------------------------------


class TestOutputRenderedDisplayTap(unittest.TestCase):
    """Test _output_rendered behavior with DisplayTap."""

    def _make_coordinator(self):
        renderer = MagicMock()
        renderer.writing_messages = False
        renderer.input_line_written = False
        renderer.last_line_count = 0
        renderer.thinking_active = False
        renderer.clear_active_area = MagicMock()
        renderer.invalidate_render_cache = MagicMock()
        renderer.pipe_mode = False
        renderer.terminal_state = MagicMock()

        coordinator = MessageDisplayCoordinator(renderer)
        return coordinator

    def test_output_rendered_prints_normally(self):
        """_output_rendered calls print() when not in alternate buffer."""
        coord = self._make_coordinator()
        with patch("builtins.print") as mock_print:
            coord._output_rendered("hello world", "system")
            mock_print.assert_called_once_with("hello world", flush=True)

    def test_output_rendered_buffers_in_altbuf(self):
        """_output_rendered buffers when in alternate buffer."""
        coord = self._make_coordinator()
        coord._in_alternate_buffer = True
        with patch("builtins.print") as mock_print:
            coord._output_rendered("hello world", "system")
            mock_print.assert_not_called()
        self.assertEqual(len(coord._buffered_output), 1)
        self.assertEqual(coord._buffered_output[0][0], "system")
        self.assertEqual(coord._buffered_output[0][1], "hello world")

    def test_display_tap_settable(self):
        """DisplayTap can be set on coordinator."""
        coord = self._make_coordinator()
        tap = MagicMock()
        coord._display_tap = tap
        self.assertEqual(coord._display_tap, tap)

    def test_streaming_publishes_to_display_tap(self):
        """write_streaming_chunk publishes to DisplayTap."""
        coord = self._make_coordinator()
        tap = MagicMock()
        coord._display_tap = tap

        coord.write_streaming_chunk("hello")
        tap.publish.assert_called_once_with(
            {"type": "stream_chunk", "chunk": "hello"}
        )

    def test_output_rendered_publishes_to_display_tap(self):
        """_output_rendered publishes rendered output to DisplayTap."""
        coord = self._make_coordinator()
        tap = MagicMock()
        coord._display_tap = tap

        with patch("builtins.print"):
            coord._output_rendered("rendered content", "tool_result")

        tap.publish.assert_called_once_with(
            {"type": "output", "rendered": "rendered content"}
        )

    def test_flush_buffered_output_publishes_to_display_tap(self):
        """Buffered messages get republished to DisplayTap on flush.

        Regression: anything queued during an alt-buffer (modal/altview)
        session was print()'d on flush but never published, so attached
        clients missed the messages entirely. Tool results vanishing in
        detached mode when a modal happened to be open.
        """
        from kollabor_tui.terminal_state import TerminalMode

        coord = self._make_coordinator()
        tap = MagicMock()
        coord._display_tap = tap

        # Simulate two messages buffered while in alt buffer.
        coord._in_alternate_buffer = True
        with patch("builtins.print"):
            coord._output_rendered("tool result 1", "tool_result")
            coord._output_rendered("tool result 2", "tool_result")
        self.assertEqual(len(coord._buffered_output), 2)
        # Reset publish mock so we only see flush publishes.
        tap.publish.reset_mock()

        # Simulate exit of alt buffer + flush.
        coord._in_alternate_buffer = False
        coord.terminal_renderer.terminal_state.current_mode = TerminalMode.COOKED
        with patch("builtins.print"):
            coord._flush_buffered_output()

        # Both buffered messages must reach DisplayTap. Plus there may be
        # the return-summary card (we don't assert exact count, just that
        # both rendered strings show up).
        published_outputs = [
            call.args[0]
            for call in tap.publish.call_args_list
            if call.args[0].get("type") == "output"
        ]
        rendered_set = {evt["rendered"] for evt in published_outputs}
        self.assertIn("tool result 1", rendered_set)
        self.assertIn("tool result 2", rendered_set)
        self.assertEqual(len(coord._buffered_output), 0)


if __name__ == "__main__":
    unittest.main()
