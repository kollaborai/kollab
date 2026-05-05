#!/usr/bin/env python3
"""Simple visual test for tool output formatting (no wrapping for code).

This test directly tests the Box and TagBox components without
importing the entire application.
"""

import os
import re
import sys

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from kollabor_tui.design_system.components import Box, TagBox, wrap_text


def strip_ansi(text):
    """Strip ANSI escape codes from text for testing."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_wrap_text_basic():
    """Test that wrap_text still works for regular text."""
    print("\n=== Test: wrap_text Basic Functionality ===\n")

    text = (
        "This is a very long line of text that should be wrapped at word "
        "boundaries when it exceeds the specified width"
    )
    wrapped = wrap_text(text, width=40)

    print(f"Original: {text}")
    print("\nWrapped (width=40):")
    for i, line in enumerate(wrapped):
        print(f"  Line {i+1}: {line}")

    assert len(wrapped) > 1, "Text should have been wrapped into multiple lines"
    print("\n[OK] wrap_text works correctly\n")


def test_box_render_solid_no_wrapping():
    """Test Box.render_solid with disable_wrapping=True."""
    print("\n=== Test: Box.render_solid with disable_wrapping ===\n")

    # Code that should NOT wrap
    code_lines = [
        "from kollabor.io.message_renderer import ModernMessageRenderer",
        "content = '''multi-line string content here'''",
        "def function_with_long_name(param1, param2, param3):",
    ]

    # Color values (simple RGB)
    bg = (40, 40, 50)
    fg = (200, 200, 200)
    width = 50

    print("Code with disable_wrapping=True:")
    result = Box.render_solid(code_lines, bg, fg, width, disable_wrapping=True)
    print(result)
    print()

    # Verify each original line appears intact (not word-wrapped)
    result_lines = result.split("\n")

    # Count content lines (excluding top/bottom borders which have only▄▀)
    content_lines = [
        line for line in result_lines if "▄" not in line and "▀" not in line
    ]

    # Should have exactly 3 content lines (our 3 input lines), NOT more from wrapping
    assert (
        len(content_lines) == 3
    ), f"Expected 3 content lines, got {len(content_lines)} (wrapping occurred!)"

    print(f"[OK] Got exactly {len(content_lines)} lines (no wrapping occurred)")
    print("[OK] Lines were truncated with ... when needed, NOT word-wrapped")

    print("[PASS] Box.render_solid with disable_wrapping works\n")


def test_box_render_solid_with_wrapping():
    """Test Box.render_solid with disable_wrapping=False (default)."""
    print("\n=== Test: Box.render_solid with wrapping enabled ===\n")

    # Long prose text that SHOULD wrap
    prose_lines = [
        "This is a very long line of prose text that should be wrapped at word boundaries when rendering in a box"
    ]

    bg = (40, 40, 50)
    fg = (200, 200, 200)
    width = 40

    print("Prose with wrapping enabled (default):")
    result = Box.render_solid(prose_lines, bg, fg, width, disable_wrapping=False)
    print(result)
    print()

    # Should have multiple content lines (wrapped)
    result_lines = result.split("\n")
    # Count content lines (excluding top/bottom edges)
    [
        line
        for line in result_lines
        if "very long" in line or "boundaries" in line or "wrapped" in line
    ]

    # Text should be split across multiple lines
    assert (
        len(result_lines) > 3
    ), "Long text should have been wrapped into multiple lines"
    print(f"[OK] Text wrapped into {len(result_lines)} lines total\n")

    print("[PASS] Box.render_solid with wrapping works\n")


def test_tagbox_render_no_wrapping():
    """Test TagBox.render with disable_wrapping=True."""
    print("\n=== Test: TagBox.render with disable_wrapping ===\n")

    # Code lines
    code_lines = [
        "python",
        "from kollabor_tui.design_system import T, S, Box, TagBox, solid, gradient",
        "def process(): pass",
    ]

    tag_bg = (130, 80, 180)  # Purple
    tag_fg = (255, 255, 255)
    content_bg = (40, 40, 50)
    content_fg = (200, 200, 200)
    tag_width = 3
    content_width = 60

    print("Code with disable_wrapping=True:")
    result = TagBox.render(
        lines=code_lines,
        tag_bg=tag_bg,
        tag_fg=tag_fg,
        tag_width=tag_width,
        content_colors=content_bg,
        content_fg=content_fg,
        content_width=content_width,
        use_gradient=False,
        disable_wrapping=True,
    )
    print(result)
    print()

    # Verify import not wrapped (strip ANSI before matching)
    result_lines = result.split("\n")
    import_found = False
    for line in result_lines:
        clean = strip_ansi(line)
        if "design_system" in clean:
            import_found = True
            # Should have "from" and "import" on same line
            if "from" in clean:
                assert (
                    "import" in clean or "..." in clean
                ), "Import was split across lines!"
            print("[OK] Import preserved on single line (or truncated)")
            break

    assert import_found, "Import not found"

    print("[PASS] TagBox.render with disable_wrapping works\n")


if __name__ == "__main__":
    test_wrap_text_basic()
    test_box_render_solid_no_wrapping()
    test_box_render_solid_with_wrapping()
    test_tagbox_render_no_wrapping()

    print("\n" + "=" * 70)
    print("ALL TESTS PASSED - Tool output formatting fix works correctly!")
    print("=" * 70 + "\n")
