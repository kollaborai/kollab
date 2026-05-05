#!/usr/bin/env python3
"""Visual test for input box text wrapping.

This script tests that long input text wraps correctly within the input box
instead of being truncated.
"""

import os
import sys

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from kollabor_tui.terminal_renderer import TerminalRenderer


def test_input_wrapping():
    """Test input box wrapping with various text lengths."""

    # Initialize renderer without config (it's optional)
    renderer = TerminalRenderer(config=None)

    # Test cases with increasing text length
    test_cases = [
        ("Short text", "Hello world"),
        ("Medium text", "This is a medium length text that should fit on one line"),
        (
            "Long text (should wrap)",
            "This is a much longer text that exceeds the terminal width and "
            "should wrap to a second line within the input box instead of "
            "being truncated",
        ),
        (
            "Very long text (multiple wraps)",
            "This is an extremely long text input that will definitely exceed the "
            "terminal width by a significant margin and should wrap to multiple "
            "lines within the input box, demonstrating proper continuation line "
            "indentation and cursor handling across wrapped lines",
        ),
    ]

    print("Input Box Wrapping Test")
    print("=" * 60)
    print()

    for test_name, test_text in test_cases:
        print(f"{test_name}:")
        print(f"  Input length: {len(test_text)} characters")
        print()

        # Set input buffer
        renderer.input_buffer = test_text
        renderer.cursor_position = len(test_text)  # Cursor at end

        # Render input area
        lines = []
        renderer._render_input_modern(lines, position="only")

        # Display rendered output
        for line in lines:
            print(f"  {line}")

        print()
        print("-" * 60)
        print()

    # Test with cursor in middle of wrapped text
    print("Cursor positioning test (cursor in middle):")
    long_text = (
        "This is a long text with the cursor positioned in the middle of "
        "the wrapped content to verify cursor placement"
    )
    cursor_pos = 40  # Middle of text

    renderer.input_buffer = long_text
    renderer.cursor_position = cursor_pos

    lines = []
    renderer._render_input_modern(lines, position="only")

    for line in lines:
        print(f"  {line}")

    print()
    print("=" * 60)
    print("[OK] Input wrapping test completed")
    print()
    print("Visual inspection checklist:")
    print("  [?] Long text wraps to multiple lines")
    print("  [?] Continuation lines are properly indented")
    print("  [?] No text is truncated or hidden")
    print("  [?] Cursor appears on correct wrapped line")
    print("  [?] Input box borders display correctly")


if __name__ == "__main__":
    test_input_wrapping()
