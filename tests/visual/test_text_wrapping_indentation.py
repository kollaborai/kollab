#!/usr/bin/env python3
"""Visual test for text wrapping indentation bug.

This test demonstrates the issue where continuation lines in wrapped text
align with the tag bullet (3 spaces) instead of the text content (4 spaces).

Issue: known_issues/active/2026-01-17-text-wrapping-indentation-bug.md
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from kollabor_tui.design_system import T, TagBox, wrap_text


def test_current_behavior():
    """Demonstrate CURRENT (incorrect) behavior."""
    print("=" * 80)
    print("CURRENT BEHAVIOR (INCORRECT)")
    print("=" * 80)
    print()

    # Long text that will wrap
    long_text = (
        'i see you\'re asking about "this problem" but i need more context to '
        "identify what specific issue you're referring to. let me investigate "
        "the current state of the project to find any active problems or issues."
    )

    width = 76  # Terminal width
    content_width = width - 3  # Minus tag_width

    # Current rendering (what ModernMessageRenderer.assistant_message does)
    lines = [f" {long_text}"]  # Add 1-space prefix
    tag_chars = [" ◆ "]

    output = TagBox.render(
        lines=lines,
        tag_bg=T().ai_tag,
        tag_fg=T().text_dark,
        tag_width=3,
        content_colors=T().response_bg,
        content_fg=T().text,
        content_width=content_width,
        tag_chars=tag_chars,
    )

    print(output)
    print()

    # Show the raw wrapped lines to see indentation
    print("Raw wrapped lines (showing spaces with dots):")
    wrapped = wrap_text(f" {long_text}", content_width)
    for i, line in enumerate(wrapped):
        # Replace leading spaces with dots to visualize
        spaces = len(line) - len(line.lstrip())
        visual = "." * spaces + line.lstrip()
        print(f"Line {i}: '{visual}'")

    print()
    print("PROBLEM: Continuation lines have only 1 space (from content prefix)")
    print("         They should have 4 spaces to align with text after ' ◆  '")
    print()


def test_expected_behavior():
    """Demonstrate EXPECTED (correct) behavior."""
    print("=" * 80)
    print("EXPECTED BEHAVIOR (CORRECT)")
    print("=" * 80)
    print()

    # Long text that will wrap
    long_text = (
        'i see you\'re asking about "this problem" but i need more context to '
        "identify what specific issue you're referring to. let me investigate "
        "the current state of the project to find any active problems or issues."
    )

    width = 76
    content_width = width - 3

    # Expected: manually add continuation indent
    lines_with_correct_indent = []
    wrapped = wrap_text(f" {long_text}", content_width)
    for i, line in enumerate(wrapped):
        if i == 0:
            # First line: normal prefix
            lines_with_correct_indent.append(line)
        else:
            # Continuation lines: add 3 more spaces (total 4)
            # Tag takes 3 chars, we want to align with text after " ◆  "
            # " ◆  text" means text starts at position 4
            # Line already has " " (1 space), need 3 more
            lines_with_correct_indent.append("   " + line)

    # Render with manually fixed indentation
    [" ◆ "] + ["   "] * (len(lines_with_correct_indent) - 1)

    # Need to use manual rendering since we've already wrapped
    print("Manual rendering with correct indentation:")
    for line in lines_with_correct_indent:
        print(f"    {line}")  # Simulate tag + content

    print()
    print("SOLUTION: Continuation lines have 4 spaces to align with text content")
    print("          Tag: ' ◆ ' (3 chars) + content prefix ' ' (1 char) = 4 spaces")
    print()


def test_visual_comparison():
    """Side-by-side visual comparison."""
    print("=" * 80)
    print("VISUAL COMPARISON")
    print("=" * 80)
    print()

    print("CURRENT (wrong - 3-space indent):")
    print(" ◆  first line text that wraps to next line because it is very long")
    print("   continuation aligns with bullet (3 spaces)")
    print()

    print("EXPECTED (correct - 4-space indent):")
    print(" ◆  first line text that wraps to next line because it is very long")
    print("    continuation aligns with text (4 spaces)")
    print()

    print("Visual alignment markers:")
    print("0123456789...")
    print(" ◆  text starts here at position 4")
    print("   ^-- 3 spaces (current)")
    print("    ^-- 4 spaces (expected)")
    print()


def test_wrap_text_behavior():
    """Test wrap_text function directly."""
    print("=" * 80)
    print("WRAP_TEXT FUNCTION BEHAVIOR")
    print("=" * 80)
    print()

    text = " This is a very long line that needs to wrap and we want to see how it handles indentation"
    width = 50

    print(f"Input text: '{text}'")
    print(f"Width: {width}")
    print()

    wrapped = wrap_text(text, width)
    print(f"Output ({len(wrapped)} lines):")
    for i, line in enumerate(wrapped):
        spaces = len(line) - len(line.lstrip())
        print(f"  Line {i}: {spaces} leading spaces | '{line}'")

    print()
    print("OBSERVATION: wrap_text does NOT add continuation indentation")
    print("             It just wraps at word boundaries without indent")
    print()


def main():
    """Run all visual tests."""
    print()
    print("TEXT WRAPPING INDENTATION BUG - Visual Test")
    print("Issue: known_issues/active/2026-01-17-text-wrapping-indentation-bug.md")
    print()

    # Run tests
    test_current_behavior()
    test_expected_behavior()
    test_visual_comparison()
    test_wrap_text_behavior()

    print("=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)
    print()
    print("ROOT CAUSE:")
    print("  - wrap_text() does not add indentation to continuation lines")
    print("  - TagBox.render() wraps text but doesn't adjust continuation indent")
    print("  - ModernMessageRenderer adds 1-space prefix but needs 4 for continuation")
    print()
    print("IMPACT:")
    print("  - All wrapped text in the application is misaligned")
    print("  - Affects assistant messages, user messages, tool outputs, errors")
    print()
    print("FIX:")
    print("  - Add continuation_indent parameter to wrap_text()")
    print("  - Calculate indent as: tag_width + content_prefix_length")
    print("  - For TagBox with ' ◆  text': indent = 3 + 1 = 4 spaces")
    print()


if __name__ == "__main__":
    main()
