"""Tests for kollabor_tui.design_system module.

Tests the public API of the design system including:
- Theme (T) with color attributes
- Style constants (S)
- Box and TagBox rendering
- gradient/solid/solid_fg functions
- wrap_text function
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

from kollabor_tui.design_system import (
    THEMES,
    Box,
    C,
    S,
    T,
    TagBox,
    Theme,
    get_theme,
    gradient,
    gradient_fg,
    set_theme,
    smooth_gradient,
    smooth_gradient_subtle,
    solid,
    solid_fg,
    wrap_text,
)


class TestTheme(unittest.TestCase):
    """Test cases for Theme system."""

    def test_t_returns_theme_instance(self):
        """Test that T() returns a Theme instance."""
        theme = T()
        self.assertIsInstance(theme, Theme)

    def test_theme_has_primary(self):
        """Test that theme has primary attribute."""
        theme = T()
        self.assertTrue(hasattr(theme, "primary"))
        self.assertIsInstance(theme.primary, list)

    def test_theme_has_dark(self):
        """Test that theme has dark attribute."""
        theme = T()
        self.assertTrue(hasattr(theme, "dark"))
        self.assertIsInstance(theme.dark, list)

    def test_theme_has_text(self):
        """Test that theme has text attribute."""
        theme = T()
        self.assertTrue(hasattr(theme, "text"))
        self.assertIsInstance(theme.text, tuple)

    def test_theme_has_text_dim(self):
        """Test that theme has text_dim attribute."""
        theme = T()
        self.assertTrue(hasattr(theme, "text_dim"))
        self.assertIsInstance(theme.text_dim, tuple)

    def test_theme_has_text_dark(self):
        """Test that theme has text_dark attribute."""
        theme = T()
        self.assertTrue(hasattr(theme, "text_dark"))
        self.assertIsInstance(theme.text_dark, tuple)

    def test_theme_has_success(self):
        """Test that theme has success attribute."""
        theme = T()
        self.assertTrue(hasattr(theme, "success"))
        self.assertIsInstance(theme.success, list)

    def test_theme_has_error(self):
        """Test that theme has error attribute."""
        theme = T()
        self.assertTrue(hasattr(theme, "error"))
        self.assertIsInstance(theme.error, list)

    def test_theme_has_warning(self):
        """Test that theme has warning attribute."""
        theme = T()
        self.assertTrue(hasattr(theme, "warning"))
        self.assertIsInstance(theme.warning, list)

    def test_theme_has_secondary(self):
        """Test that theme has secondary attribute."""
        theme = T()
        self.assertTrue(hasattr(theme, "secondary"))
        self.assertIsInstance(theme.secondary, list)

    def test_theme_has_response_bg(self):
        """Test that theme has response_bg attribute."""
        theme = T()
        self.assertTrue(hasattr(theme, "response_bg"))
        self.assertIsInstance(theme.response_bg, list)

    def test_theme_has_input_bg(self):
        """Test that theme has input_bg attribute."""
        theme = T()
        self.assertTrue(hasattr(theme, "input_bg"))
        self.assertIsInstance(theme.input_bg, list)

    def test_theme_has_user_tag(self):
        """Test that theme has user_tag attribute."""
        theme = T()
        self.assertTrue(hasattr(theme, "user_tag"))
        self.assertIsInstance(theme.user_tag, tuple)

    def test_theme_has_ai_tag(self):
        """Test that theme has ai_tag attribute."""
        theme = T()
        self.assertTrue(hasattr(theme, "ai_tag"))
        self.assertIsInstance(theme.ai_tag, tuple)

    def test_theme_has_assistant_text(self):
        """Assistant text has a readable foreground separate from success green."""
        theme = T()
        self.assertTrue(hasattr(theme, "assistant_text"))
        self.assertIsInstance(theme.assistant_text, tuple)

    def test_theme_has_tool_tag(self):
        """Test that theme has tool_tag attribute."""
        theme = T()
        self.assertTrue(hasattr(theme, "tool_tag"))
        self.assertIsInstance(theme.tool_tag, tuple)

    def test_theme_has_thinking_tag(self):
        """Test that theme has thinking_tag attribute."""
        theme = T()
        self.assertTrue(hasattr(theme, "thinking_tag"))
        self.assertIsInstance(theme.thinking_tag, tuple)

    def test_theme_has_code_bg(self):
        """Test that theme has code_bg attribute."""
        theme = T()
        self.assertTrue(hasattr(theme, "code_bg"))
        self.assertIsInstance(theme.code_bg, tuple)

    def test_theme_has_name(self):
        """Test that theme has name attribute."""
        theme = T()
        self.assertTrue(hasattr(theme, "name"))
        self.assertIsInstance(theme.name, str)

    def test_set_theme(self):
        """Test set_theme function."""
        original_name = T().name
        set_theme("ocean")
        theme = get_theme()
        self.assertEqual(theme.name, "ocean")
        # Restore default
        set_theme(original_name)

    def assert_graphite_ember_palette(self, theme):
        self.assertLessEqual(max(theme.dark[0]), 22)
        self.assertGreater(theme.primary[0][0], theme.primary[0][1])
        self.assertGreater(theme.primary[0][0], theme.primary[0][2])
        self.assertGreaterEqual(theme.text_dim[0], 130)

    def test_dark_theme_uses_graphite_ember_palette(self):
        """Default active theme is compact graphite with ember accents."""
        self.assert_graphite_ember_palette(THEMES["dark"])
        self.assertNotEqual(THEMES["dark"].assistant_text, THEMES["dark"].success[0])

    def test_dark_assistant_text_is_dim_white_not_blue(self):
        """Assistant text in dark mode is muted white, not the old blue."""
        theme = THEMES["dark"]

        self.assertGreaterEqual(min(theme.assistant_text), 175)
        self.assertLessEqual(max(theme.assistant_text) - min(theme.assistant_text), 18)

    def test_lime_theme_uses_graphite_ember_palette(self):
        """Lime alias keeps the compact graphite and ember palette."""
        self.assert_graphite_ember_palette(THEMES["lime"])

    def test_light_theme_uses_dark_foreground_for_light_terminals(self):
        """Light mode keeps foreground readable on light terminal backgrounds."""
        theme = THEMES["light"]

        self.assertLess(max(theme.text), 80)
        self.assertLess(max(theme.assistant_text), 150)
        self.assertGreater(min(theme.dark[0]), 230)


class TestStyleConstants(unittest.TestCase):
    """Test cases for S style constants."""

    def test_s_has_bold(self):
        """Test that S has BOLD constant."""
        self.assertTrue(hasattr(S, "BOLD"))
        self.assertIsInstance(S.BOLD, str)

    def test_s_has_dim(self):
        """Test that S has DIM constant."""
        self.assertTrue(hasattr(S, "DIM"))
        self.assertIsInstance(S.DIM, str)

    def test_s_has_reset(self):
        """Test that S has RESET constant."""
        self.assertTrue(hasattr(S, "RESET"))
        self.assertIsInstance(S.RESET, str)

    def test_s_has_reset_bold(self):
        """Test that S has RESET_BOLD constant."""
        self.assertTrue(hasattr(S, "RESET_BOLD"))
        self.assertIsInstance(S.RESET_BOLD, str)

    def test_s_has_reset_dim(self):
        """Test that S has RESET_DIM constant."""
        self.assertTrue(hasattr(S, "RESET_DIM"))
        self.assertIsInstance(S.RESET_DIM, str)

    def test_s_has_italic(self):
        """Test that S has ITALIC constant."""
        self.assertTrue(hasattr(S, "ITALIC"))
        self.assertIsInstance(S.ITALIC, str)

    def test_s_has_reset_italic(self):
        """Test that S has RESET_ITALIC constant."""
        self.assertTrue(hasattr(S, "RESET_ITALIC"))
        self.assertIsInstance(S.RESET_ITALIC, str)


class TestBox(unittest.TestCase):
    """Test cases for Box class."""

    def test_box_render_returns_string(self):
        """Test that Box.render returns a string."""
        result = Box.render(["Hello"], T().dark, T().text, 40)
        self.assertIsInstance(result, str)

    def test_box_render_non_empty(self):
        """Test that Box.render returns non-empty string."""
        result = Box.render(["Hello"], T().dark, T().text, 40)
        self.assertTrue(len(result) > 0)

    def test_box_top_returns_string(self):
        """Test that Box.top returns a string."""
        result = Box.top(T().dark, 40)
        self.assertIsInstance(result, str)

    def test_box_bottom_returns_string(self):
        """Test that Box.bottom returns a string."""
        result = Box.bottom(T().dark, 40)
        self.assertIsInstance(result, str)

    def test_box_content_returns_string(self):
        """Test that Box.content returns a string."""
        result = Box.content("Hello", T().dark, T().text, 40)
        self.assertIsInstance(result, str)

    def test_box_render_solid_returns_string(self):
        """Test that Box.render_solid returns a string."""
        result = Box.render_solid(["Hello"], T().dark[0], T().text, 40)
        self.assertIsInstance(result, str)


class TestTagBox(unittest.TestCase):
    """Test cases for TagBox class."""

    def test_tagbox_render_returns_string(self):
        """Test that TagBox.render returns a string."""
        result = TagBox.render(
            lines=["Hello"],
            tag_bg=T().primary[0],
            tag_fg=T().text_dark,
            tag_width=3,
            content_colors=T().dark,
            content_fg=T().text,
            content_width=37,
        )
        self.assertIsInstance(result, str)

    def test_tagbox_render_non_empty(self):
        """Test that TagBox.render returns non-empty string."""
        result = TagBox.render(
            lines=["Hello"],
            tag_bg=T().primary[0],
            tag_fg=T().text_dark,
            tag_width=3,
            content_colors=T().dark,
            content_fg=T().text,
            content_width=37,
        )
        self.assertTrue(len(result) > 0)

    def test_tagbox_render_with_multiple_lines(self):
        """Test TagBox.render with multiple lines."""
        result = TagBox.render(
            lines=["Line 1", "Line 2", "Line 3"],
            tag_bg=T().primary[0],
            tag_fg=T().text_dark,
            tag_width=3,
            content_colors=T().dark,
            content_fg=T().text,
            content_width=37,
        )
        self.assertIsInstance(result, str)
        # Should have 3 content lines + top + bottom = at least 5 lines
        self.assertGreaterEqual(result.count("\n"), 4)

    def test_tagbox_render_with_tag_chars(self):
        """Test TagBox.render with custom tag chars."""
        result = TagBox.render(
            lines=["Hello"],
            tag_bg=T().primary[0],
            tag_fg=T().text_dark,
            tag_width=3,
            content_colors=T().dark,
            content_fg=T().text,
            content_width=37,
            tag_chars=[" > "],
        )
        self.assertIsInstance(result, str)
        self.assertIn(">", result)


class TestGradient(unittest.TestCase):
    """Test cases for gradient functions."""

    def test_gradient_returns_string(self):
        """Test that gradient returns a string."""
        result = gradient("Hello", [(80, 200, 50), (100, 220, 70)])
        self.assertIsInstance(result, str)

    def test_gradient_non_empty(self):
        """Test that gradient returns non-empty string."""
        result = gradient("Hello", [(80, 200, 50), (100, 220, 70)])
        self.assertTrue(len(result) > 0)

    def test_gradient_with_width(self):
        """Test gradient with width parameter."""
        result = gradient("Hi", [(80, 200, 50), (100, 220, 70)], width=10)
        self.assertIsInstance(result, str)
        # Should be padded to width
        self.assertGreaterEqual(len(result), 10)

    def test_gradient_fg_returns_string(self):
        """Test that gradient_fg returns a string."""
        result = gradient_fg("Hello", [(80, 200, 50), (100, 220, 70)])
        self.assertIsInstance(result, str)

    def test_gradient_fg_non_empty(self):
        """Test that gradient_fg returns non-empty string."""
        result = gradient_fg("Hello", [(80, 200, 50), (100, 220, 70)])
        self.assertTrue(len(result) > 0)

    def test_smooth_gradient_returns_string(self):
        """Test that smooth_gradient returns a string."""
        result = smooth_gradient("Hello", [(80, 200, 50), (100, 220, 70)])
        self.assertIsInstance(result, str)

    def test_smooth_gradient_non_empty(self):
        """Test that smooth_gradient returns non-empty string."""
        result = smooth_gradient("Hello", [(80, 200, 50), (100, 220, 70)])
        self.assertTrue(len(result) > 0)

    def test_smooth_gradient_subtle_returns_string(self):
        """Test that smooth_gradient_subtle returns a string."""
        result = smooth_gradient_subtle("Hello", [(80, 200, 50), (100, 220, 70)])
        self.assertIsInstance(result, str)

    def test_smooth_gradient_subtle_non_empty(self):
        """Test that smooth_gradient_subtle returns non-empty string."""
        result = smooth_gradient_subtle("Hello", [(80, 200, 50), (100, 220, 70)])
        self.assertTrue(len(result) > 0)


class TestSolid(unittest.TestCase):
    """Test cases for solid functions."""

    def test_solid_returns_string(self):
        """Test that solid returns a string."""
        result = solid("Hello", (80, 200, 50), (255, 255, 255))
        self.assertIsInstance(result, str)

    def test_solid_non_empty(self):
        """Test that solid returns non-empty string."""
        result = solid("Hello", (80, 200, 50), (255, 255, 255))
        self.assertTrue(len(result) > 0)

    def test_solid_with_width(self):
        """Test solid with width parameter."""
        result = solid("Hi", (80, 200, 50), (255, 255, 255), width=10)
        self.assertIsInstance(result, str)

    def test_solid_fg_returns_string(self):
        """Test that solid_fg returns a string."""
        result = solid_fg("Hello", (80, 200, 50))
        self.assertIsInstance(result, str)

    def test_solid_fg_non_empty(self):
        """Test that solid_fg returns non-empty string."""
        result = solid_fg("Hello", (80, 200, 50))
        self.assertTrue(len(result) > 0)


class TestWrapText(unittest.TestCase):
    """Test cases for wrap_text function."""

    def test_wrap_text_returns_list(self):
        """Test that wrap_text returns a list."""
        result = wrap_text("Hello world", 20)
        self.assertIsInstance(result, list)

    def test_wrap_text_single_line(self):
        """Test wrap_text with text that fits on one line."""
        result = wrap_text("Hello", 20)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], "Hello")

    def test_wrap_text_multiple_lines(self):
        """Test wrap_text with text that needs multiple lines."""
        result = wrap_text("Hello world this is a long text", 10)
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 1)

    def test_wrap_text_with_continuation_indent(self):
        """Test wrap_text with continuation_indent parameter."""
        result = wrap_text("Hello world this is a long text", 10, continuation_indent=2)
        self.assertIsInstance(result, list)
        # Second line should start with spaces
        if len(result) > 1:
            self.assertTrue(result[1].startswith("  "))

    def test_wrap_text_empty_string(self):
        """Test wrap_text with empty string."""
        result = wrap_text("", 20)
        self.assertEqual(result, [""])

    def test_wrap_text_zero_width(self):
        """Test wrap_text with zero width."""
        result = wrap_text("Hello", 0)
        self.assertEqual(result, ["Hello"])

    def test_wrap_text_word_boundary(self):
        """Test that wrap_text respects word boundaries."""
        result = wrap_text("Hello world test", 10, word_wrap=True)
        # Should split on word boundary, not mid-word
        for line in result:
            if len(line) > 1:
                # If line is max length, it should end with a complete word
                self.assertNotIn(" ", line.strip()[:2])


class TestConstants(unittest.TestCase):
    """Test cases for C constants."""

    def test_c_has_check_on(self):
        """Test that C has check_on constant."""
        self.assertIn("check_on", C)
        self.assertIsInstance(C["check_on"], str)

    def test_c_has_check_off(self):
        """Test that C has check_off constant."""
        self.assertIn("check_off", C)
        self.assertIsInstance(C["check_off"], str)

    def test_c_has_bar_full(self):
        """Test that C has bar_full constant."""
        self.assertIn("bar_full", C)
        self.assertIsInstance(C["bar_full"], str)

    def test_c_has_bar_empty(self):
        """Test that C has bar_empty constant."""
        self.assertIn("bar_empty", C)
        self.assertIsInstance(C["bar_empty"], str)

    def test_c_has_success(self):
        """Test that C has success constant."""
        self.assertIn("success", C)
        self.assertIsInstance(C["success"], str)

    def test_c_has_error(self):
        """Test that C has error constant."""
        self.assertIn("error", C)
        self.assertIsInstance(C["error"], str)

    def test_c_has_warning(self):
        """Test that C has warning constant."""
        self.assertIn("warning", C)
        self.assertIsInstance(C["warning"], str)


if __name__ == "__main__":
    unittest.main()
