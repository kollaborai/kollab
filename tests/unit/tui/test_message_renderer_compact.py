"""Tests for compact modern message rendering."""

import re

from kollabor_tui.color_contrast import readable_agent_color
from kollabor_tui.design_system import (
    COLOR_TRUECOLOR,
    T,
    get_color_mode,
    set_color_mode,
    set_theme,
    solid_fg,
)
from kollabor_tui.message_renderer import ModernMessageRenderer

ANSI_RE = re.compile(r"\033\[[0-9;]*m")
FG_RE = re.compile(r"\033\[38;2;(\d+);(\d+);(\d+)m")


def visible(text: str) -> str:
    """Strip ANSI codes for shape assertions."""
    return ANSI_RE.sub("", text)


def contrast_ratio(fg: tuple[int, int, int], bg: tuple[int, int, int]) -> float:
    def channel(value: int) -> float:
        scaled = value / 255
        return (
            scaled / 12.92 if scaled <= 0.04045 else ((scaled + 0.055) / 1.055) ** 2.4
        )

    def luminance(color: tuple[int, int, int]) -> float:
        r, g, b = (channel(value) for value in color)
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    light = max(luminance(fg), luminance(bg))
    dark = min(luminance(fg), luminance(bg))
    return (light + 0.05) / (dark + 0.05)


def foreground_for(rendered: str, text: str) -> tuple[int, int, int]:
    prefix = rendered[: rendered.index(text)]
    matches = FG_RE.findall(prefix)
    assert matches
    return tuple(int(value) for value in matches[-1])


def test_tool_call_renders_as_compact_row():
    """Tool calls render as dense activity rows, not boxed TagBox blocks."""
    rendered = ModernMessageRenderer().tool_call(
        "Read",
        "app/api/orders/route.ts",
        status="success",
        nested_width=80,
        result_summary="28 lines · pulled handler + Prisma calls",
    )

    plain = visible(rendered)
    lines = plain.splitlines()

    assert len(lines) == 1
    assert lines[0].startswith("      ⌕ ")
    assert "app/api/orders/route.ts" in lines[0]
    assert "➲" in lines[0]
    assert "28 lines" in lines[0]
    assert not {"▄", "▀", "█"} & set(plain)


def test_success_tool_summary_uses_success_green():
    """Plain success summaries are green instead of muted gray."""
    rendered = ModernMessageRenderer().tool_call(
        "terminal",
        "git add packages/kollabor-tui/...",
        status="success",
        nested_width=80,
        result_summary="Success",
    )

    plain = visible(rendered)

    assert ">_ git add" in plain
    assert "terminal" not in plain
    assert "➲ Success" in plain
    assert solid_fg("Success", T().success[0]) in rendered


def test_terminal_error_renders_badge_and_inline_exit_summary():
    """Terminal failures use the same compact badge with inline red result."""
    rendered = ModernMessageRenderer().tool_call(
        "terminal",
        'rg "ENGINE-DEBUG" packages/kollabor_engine/',
        status="error",
        nested_width=90,
        result_summary="Error: Command exited with code 2",
    )

    plain = visible(rendered)

    assert plain.startswith('      >_ rg "ENGINE-DEBUG"')
    assert "terminal" not in plain
    assert "➲ ✖ Exit code 2" in plain
    assert solid_fg("✖ Exit code 2", T().error[0]) in rendered


def test_file_write_and_hub_tools_get_operation_glyphs():
    write_rendered = ModernMessageRenderer().tool_call(
        "file_write",
        'path="notes/status.md"',
        status="success",
        nested_width=80,
        result_summary="Success",
    )
    hub_rendered = ModernMessageRenderer().tool_call(
        "hub_msg",
        "lapis hello",
        status="success",
        nested_width=80,
        result_summary="sent",
    )

    assert visible(write_rendered).startswith("      ✎ notes/status.md")
    assert solid_fg("✎", T().user_tag) in write_rendered
    assert visible(hub_rendered).startswith("      ✉ lapis hello")
    assert solid_fg("✉", T().ai_tag) in hub_rendered


def test_common_internal_operations_have_text_mode_glyphs():
    renderer = ModernMessageRenderer()
    cases = [
        ("file_create", 'path="notes/new.md"', "✚ notes/new.md"),
        ("file_edit", 'path="notes/new.md"', "✐ notes/new.md"),
        ("file_delete", 'path="notes/new.md"', "✕ notes/new.md"),
        ("file_move", 'path="notes/new.md"', "↦ notes/new.md"),
        ("file_list", "notes", "☷ notes"),
        ("hub_agents", "online", "◎ online"),
        ("mcp_tool", "server tool", "⌘ server tool"),
        ("state_update", "ready", "↻ ready"),
        ("task_update", "plan", "☑ plan"),
    ]

    for name, args, expected in cases:
        rendered = renderer.tool_call(name, args, "success", nested_width=80)
        plain = visible(rendered)

        assert plain.startswith(f"      {expected}")
        assert "[" not in plain
        assert "]" not in plain


def test_tool_result_body_is_dimmed_and_indented():
    """Raw tool output is visually secondary to the final answer."""
    rendered = ModernMessageRenderer().tool_result(["raw output"])

    assert visible(rendered) == "      raw output"
    assert solid_fg("raw output", T().text_dim) in rendered


def test_user_message_renders_as_compact_row():
    """User turns render as a single dense row, not a framed box."""
    rendered = ModernMessageRenderer().user_message(
        "investigate why /api/orders p99 jumped",
        width=80,
    )

    plain = visible(rendered)
    lines = plain.splitlines()

    assert lines == ["", " ▌ investigate why /api/orders p99 jumped"]
    assert "\033[0;48;" not in rendered
    assert not {"▄", "▀", "█"} & set(plain)


def test_response_block_renders_as_compact_plain_lines():
    """Assistant responses render as plain compact lines."""
    rendered = ModernMessageRenderer().response_block(
        ["Plan updated", "", "Read app/api/orders/route.ts"],
        width=80,
    )

    plain = visible(rendered)
    lines = plain.splitlines()

    assert lines == ["", " ֎ Plan updated", "", "   Read app/api/orders/route.ts"]
    assert "\033[0;48;" not in rendered
    assert solid_fg(" ֎ ", T().text) in rendered
    assert solid_fg("Plan updated", T().assistant_text) in rendered
    assert not {"▄", "▀", "█"} & set(plain)


def test_info_block_renders_as_compact_status_row():
    """System info renders as a compact status row."""
    rendered = ModernMessageRenderer().info_block("Ready", width=80)

    plain = visible(rendered)

    assert plain == "ℹ Ready"
    assert "\033[48;" not in rendered
    assert not {"▄", "▀", "█"} & set(plain)


def test_native_read_file_tool_is_normalized_to_file_read():
    rendered = ModernMessageRenderer().tool_call(
        "read_file",
        'path="packages/kollabor-engine/src/kollabor_engine/auth.py"',
        status="success",
        nested_width=100,
        result_summary="Read 52 lines",
    )

    plain = visible(rendered)

    assert plain == (
        "      ⌕ packages/kollabor-engine/src/kollabor_engine/auth.py"
        " ➲ Read 52 lines"
    )
    assert "\033[48;" not in rendered


def test_turn_timing_info_row_has_no_background_if_rendered_directly():
    rendered = ModernMessageRenderer().info_block("turn took 8.9s", width=80)
    plain = visible(rendered)

    assert plain == "ℹ turn took 8.9s"
    assert "\033[48;" not in rendered
    assert not {"▄", "▀", "█"} & set(plain)


def test_success_block_renders_as_compact_status_row():
    """System success renders as a compact status row."""
    rendered = ModernMessageRenderer().success_block(
        "attached to koordinator", width=80
    )

    plain = visible(rendered)

    assert plain == "✔ attached to koordinator"
    assert "\033[48;" not in rendered
    assert not {"▄", "▀", "█"} & set(plain)


def test_error_block_renders_as_compact_status_row():
    """System errors render as compact icon-colored rows."""
    rendered = ModernMessageRenderer().error_block("Error", "attach failed", 80)

    plain = visible(rendered)

    assert plain == "✖ Error\n  attach failed"
    assert "\033[48;" not in rendered
    assert not {"▄", "▀", "█"} & set(plain)


def test_agent_message_uses_agent_colored_text_and_diamond_marker():
    agent_color = (80, 140, 240)
    original_color_mode = get_color_mode()
    set_color_mode(COLOR_TRUECOLOR)
    try:
        rendered = ModernMessageRenderer().agent_message(
            "lapis -> sapphire\nlapis here.",
            agent_color=agent_color,
            tag_char=" > ",
            width=80,
        )
    finally:
        set_color_mode(original_color_mode)

    plain = visible(rendered)
    rendered_color = readable_agent_color(
        agent_color,
        background=T().dark[0],
        target=T().text,
        muted_target=T().text_dim,
    )

    assert plain.splitlines()[0] == " ◆ lapis -> sapphire"
    assert plain.splitlines()[1] == "   lapis here."
    assert "\033[0;48;" not in rendered
    r, g, b = rendered_color
    assert f"\033[38;2;{r};{g};{b}mlapis -> sapphire" in rendered
    assert f"\033[38;2;{r};{g};{b}mlapis here." in rendered


def test_observed_agent_message_uses_hollow_diamond_marker():
    rendered = ModernMessageRenderer().agent_message(
        "sapphire -> lapis\nstanding by.",
        agent_color=(90, 150, 240),
        tag_char=" ~ ",
        observing=True,
        width=80,
    )

    plain = visible(rendered)

    assert plain.splitlines()[0] == " ◇ sapphire -> lapis"
    assert "\033[0;48;" not in rendered


def test_dark_agent_color_is_lifted_for_readability():
    original = T().name
    original_color_mode = get_color_mode()
    set_theme("dark")
    set_color_mode(COLOR_TRUECOLOR)
    try:
        rendered = ModernMessageRenderer().agent_message(
            "sapphire -> lapis\nstanding by.",
            agent_color=(15, 82, 186),
            width=100,
        )

        fg = foreground_for(rendered, "sapphire -> lapis")

        assert fg != (15, 82, 186)
        assert contrast_ratio(fg, (0, 0, 0)) >= 6.5
    finally:
        set_theme(original)
        set_color_mode(original_color_mode)
