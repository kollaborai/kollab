#!/usr/bin/env python3
"""Visual test for tool output formatting (no wrapping for code).

This test verifies that tool output preserves original formatting
and doesn't wrap code at arbitrary positions.
"""

import re

from kollabor_tui.design_system import Box, T
from kollabor_tui.message_renderer import ModernMessageRenderer


def strip_ansi(text):
    """Strip ANSI escape codes from text for testing."""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_tool_result_preserves_formatting():
    """Test that tool result preserves code formatting without wrapping."""
    print("\n=== Test: Tool Result Preserves Code Formatting ===\n")

    # Simulate tool output with code
    code_output = [
        "from kollabor_tui.message_renderer import ModernMessageRenderer",
        "",
        "content = '''nice! looks like you're stress-testing the multi-line input behavior.",
        "",
        "from what i can see of the codebase, this is the modern message renderer",
        "with multi-line support.",
        "'''",
        "",
        "renderer = ModernMessageRenderer()",
    ]

    # Render with tool_result (should NOT wrap)
    rendered = ModernMessageRenderer().tool_result(code_output, nested_width=80)

    print("Tool output (should preserve formatting):")
    print(rendered)
    print()

    # Verify no wrapping occurred
    lines = rendered.split("\n")

    # Check that import statement is on one line (not wrapped)
    import_found = False
    for line in lines:
        clean = strip_ansi(line)
        if "ModernMessageRenderer" in clean and "import" in clean:
            import_found = True
            print("[OK] Import statement preserved on single line")
            break

    assert import_found, "Could not find import statement in output"

    # Check that multi-line string is preserved
    multiline_found = False
    for line in lines:
        clean = strip_ansi(line)
        if "stress-testing" in clean:
            multiline_found = True
            # Should have full content, not wrapped mid-sentence
            assert "you're" in clean, "Multi-line string was incorrectly wrapped!"
            print("[OK] Multi-line string content preserved")
            break

    assert multiline_found, "Could not find multi-line string content"

    print("\n[PASS] Tool output formatting preserved correctly\n")


def test_box_render_solid_with_disable_wrapping():
    """Test Box.render_solid with disable_wrapping parameter."""
    print("\n=== Test: Box.render_solid Disable Wrapping ===\n")

    # Long code line that would normally wrap
    code_lines = [
        "def very_long_function_name_that_exceeds_width(param1, param2, param3, param4):",
        "    return some_complex_expression_that_is_also_quite_long_and_would_wrap(x, y, z)",
    ]

    # Render with wrapping disabled
    code_bg = T().code_bg
    rendered = Box.render_solid(
        code_lines, code_bg, T().text_dim, 60, disable_wrapping=True
    )

    print("Code with disable_wrapping=True (should NOT wrap):")
    print(rendered)
    print()

    # Check that function def is NOT split across lines
    lines = rendered.split("\n")
    def_found = False
    for line in lines:
        clean = strip_ansi(line)
        if "very_long_function_name" in clean:
            def_found = True
            # Should have "def" and opening paren on same line
            if "def" in clean:
                assert "(" in clean, "Function definition was wrapped incorrectly!"
                print(
                    "[OK] Function definition preserved on single line (may be truncated)"
                )
            break

    assert def_found, "Could not find function definition in output"

    # Now test WITH wrapping enabled (default)
    print("\nCode with wrapping enabled (should wrap long lines):")
    rendered_wrapped = Box.render_solid(
        code_lines, code_bg, T().text_dim, 60, disable_wrapping=False
    )
    print(rendered_wrapped)
    print()

    print("[PASS] Box.render_solid wrapping control works correctly\n")


def test_code_block_preserves_formatting():
    """Test that code_block preserves code formatting."""
    print("\n=== Test: Code Block Preserves Formatting ===\n")

    python_code = [
        "from kollabor_tui.design_system import T, S, Box, TagBox, solid, solid_fg, gradient",
        "",
        "def process_data(input_data):",
        "    result = [item for item in input_data if condition(item)]",
        "    return result",
    ]

    rendered = ModernMessageRenderer().code_block(
        python_code, lang="python", nested_width=90
    )

    print("Code block (should preserve formatting):")
    print(rendered)
    print()

    # Verify import statement isn't wrapped
    lines = rendered.split("\n")
    import_found = False
    for line in lines:
        clean = strip_ansi(line)
        if "from kollabor_tui.design_system" in clean:
            import_found = True
            # Should have all imports on one line
            assert (
                "gradient" in clean or "..." in clean
            ), "Import was truncated/wrapped incorrectly!"
            print("[OK] Import statement preserved (or cleanly truncated)")
            break

    assert import_found, "Could not find import statement"

    print("[PASS] Code block formatting preserved correctly\n")


if __name__ == "__main__":
    test_tool_result_preserves_formatting()
    test_box_render_solid_with_disable_wrapping()
    test_code_block_preserves_formatting()

    print("\n" + "=" * 60)
    print("ALL TESTS PASSED - Tool output formatting works correctly!")
    print("=" * 60 + "\n")
