"""Tests for kollabor_tui.terminal_state module.

Tests the public API of the terminal state management system
including singleton behavior, size queries, and width management.
"""

import sys
import unittest
from pathlib import Path

# Add packages/kollabor-tui to path for imports
sys.path.insert(
    0,
    str(
        Path(__file__).parent.parent.parent.parent / "packages" / "kollabor-tui" / "src"
    ),
)

from kollabor_tui.terminal_state import (
    TerminalState,
    get_global_terminal_state,
    get_global_width,
    get_terminal_height,
    get_terminal_size,
    get_terminal_width,
    set_global_terminal_state,
)


class TestTerminalState(unittest.TestCase):
    """Test cases for TerminalState singleton and utilities."""

    def test_get_global_terminal_state_returns_singleton(self):
        """Test that get_global_terminal_state returns same instance."""
        # Reset global state for this test
        import kollabor_tui.terminal_state as ts_module

        ts_module._global_terminal_state = None

        ts1 = get_global_terminal_state()
        ts2 = get_global_terminal_state()

        self.assertIs(ts1, ts2)

    def test_get_global_terminal_state_creates_instance(self):
        """Test that get_global_terminal_state creates TerminalState instance."""
        import kollabor_tui.terminal_state as ts_module

        ts_module._global_terminal_state = None

        ts = get_global_terminal_state()

        self.assertIsInstance(ts, TerminalState)

    def test_set_global_terminal_state(self):
        """Test setting a custom global terminal state."""
        import kollabor_tui.terminal_state as ts_module

        custom_state = TerminalState()
        ts_module._global_terminal_state = None

        set_global_terminal_state(custom_state)
        result = get_global_terminal_state()

        self.assertIs(result, custom_state)

    def test_get_terminal_width_returns_int(self):
        """Test that get_terminal_width returns an integer."""
        width = get_terminal_width()
        self.assertIsInstance(width, int)
        self.assertGreater(width, 0)

    def test_get_terminal_height_returns_int(self):
        """Test that get_terminal_height returns an integer."""
        height = get_terminal_height()
        self.assertIsInstance(height, int)
        self.assertGreater(height, 0)

    def test_get_terminal_size_returns_tuple(self):
        """Test that get_terminal_size returns (width, height) tuple."""
        size = get_terminal_size()
        self.assertIsInstance(size, tuple)
        self.assertEqual(len(size), 2)

        width, height = size
        self.assertIsInstance(width, int)
        self.assertIsInstance(height, int)
        self.assertGreater(width, 0)
        self.assertGreater(height, 0)

    def test_get_global_width_returns_int(self):
        """Test that get_global_width returns an integer."""
        width = get_global_width()
        self.assertIsInstance(width, int)
        self.assertGreater(width, 0)

    def test_get_global_width_uses_terminal_width(self):
        """Test that get_global_width is based on terminal width."""
        import kollabor_tui.terminal_state as ts_module

        ts_module._global_terminal_state = None

        ts = get_global_terminal_state()
        terminal_width = ts.capabilities.width
        global_width = get_global_width()

        # Default is 80%, so global_width should be less than or equal to terminal width
        self.assertLessEqual(global_width, terminal_width)

    def test_terminal_state_has_capabilities(self):
        """Test that TerminalState has capabilities attribute."""
        ts = TerminalState()
        self.assertTrue(hasattr(ts, "capabilities"))

    def test_terminal_state_capabilities_has_width(self):
        """Test that capabilities.width is set."""
        ts = TerminalState()
        self.assertTrue(hasattr(ts.capabilities, "width"))
        self.assertIsInstance(ts.capabilities.width, int)
        self.assertGreater(ts.capabilities.width, 0)

    def test_terminal_state_capabilities_has_height(self):
        """Test that capabilities.height is set."""
        ts = TerminalState()
        self.assertTrue(hasattr(ts.capabilities, "height"))
        self.assertIsInstance(ts.capabilities.height, int)
        self.assertGreater(ts.capabilities.height, 0)

    def test_terminal_state_get_size(self):
        """Test TerminalState.get_size() method."""
        ts = TerminalState()
        size = ts.get_size()
        self.assertIsInstance(size, tuple)
        self.assertEqual(len(size), 2)

        width, height = size
        self.assertIsInstance(width, int)
        self.assertIsInstance(height, int)

    def test_terminal_state_get_global_width(self):
        """Test TerminalState.get_global_width() method."""
        ts = TerminalState()
        width = ts.get_global_width()
        self.assertIsInstance(width, int)
        self.assertGreater(width, 0)

    def test_default_global_width_caps_wide_terminals(self):
        """Default UI width stays compact on wide terminals."""
        ts = TerminalState()
        ts.capabilities.width = 180
        ts._cached_global_width = None

        self.assertEqual(ts.get_global_width(), 104)


if __name__ == "__main__":
    unittest.main()
