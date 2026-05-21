"""Tests for compact clean renderer status rows."""

import re

from kollabor_tui.clean_renderer import CleanRenderer
from kollabor_tui.design_system import T, solid_fg

ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def visible(text: str) -> str:
    """Strip ANSI codes for shape assertions."""
    return ANSI_RE.sub("", text)


def assert_no_background(rendered: str) -> None:
    assert "\033[48;" not in rendered
    assert "\033[0;48;" not in rendered
    assert_no_block_art(rendered)


def assert_no_block_art(rendered: str) -> None:
    assert not {"▄", "▀", "█"} & set(visible(rendered))


def test_status_rows_use_foreground_icon_only():
    renderer = CleanRenderer()

    samples = [
        (renderer.info_block("attaching to sapphire...", 80), "ℹ attaching"),
        (renderer.success_block("attached to sapphire", 80), "✔ attached"),
        (renderer.error_block("Error", "attach failed", 80), "✖ Error"),
        (renderer.warning_block("waiting", 80), "⚠ waiting"),
    ]

    for rendered, expected in samples:
        assert expected in visible(rendered)
        assert_no_background(rendered)


def test_user_and_tool_rows_use_no_boxes():
    renderer = CleanRenderer()

    user_rendered = renderer.user_message("investigate p99", 80)
    assert visible(user_rendered) == " ▌ investigate p99"
    assert_no_background(user_rendered)

    tool_rendered = renderer.tool_call(
        "Read",
        "app/api/orders/route.ts",
        "success",
        nested_width=80,
        result_summary="28 lines",
    )

    assert visible(tool_rendered) == "      ⌕ app/api/orders/route.ts ➲ 28 lines"
    assert solid_fg("⌕", T().ai_tag) in tool_rendered
    assert_no_background(tool_rendered)


def test_native_read_file_tool_is_normalized_to_file_read():
    rendered = CleanRenderer().tool_call(
        "read_file",
        'path="packages/kollabor-engine/src/kollabor_engine/auth.py"',
        "success",
        nested_width=100,
        result_summary="Read 52 lines",
    )

    assert (
        visible(rendered)
        == "      ⌕ packages/kollabor-engine/src/kollabor_engine/auth.py"
        " ➲ Read 52 lines"
    )
    assert_no_background(rendered)


def test_success_tool_summary_uses_success_green():
    rendered = CleanRenderer().tool_call(
        "terminal",
        "git add packages/kollabor-tui/...",
        "success",
        nested_width=80,
        result_summary="Success",
    )

    plain = visible(rendered)

    assert ">_ git add" in plain
    assert "terminal" not in plain
    assert "➲ Success" in plain
    assert solid_fg(">", T().tool_tag) in rendered
    assert solid_fg("Success", T().success[0]) in rendered


def test_terminal_error_renders_badge_and_inline_exit_summary():
    rendered = CleanRenderer().tool_call(
        "terminal",
        'rg "ENGINE-DEBUG" packages/kollabor_engine/',
        "error",
        nested_width=90,
        result_summary="Error: Command exited with code 2",
    )

    plain = visible(rendered)

    assert plain.startswith('      >_ rg "ENGINE-DEBUG"')
    assert "terminal" not in plain
    assert "➲ ✖ Exit code 2" in plain
    assert solid_fg("✖ Exit code 2", T().error[0]) in rendered


def test_file_write_and_hub_tools_get_operation_glyphs():
    write_rendered = CleanRenderer().tool_call(
        "file_write",
        'path="notes/status.md"',
        "success",
        nested_width=80,
        result_summary="Success",
    )
    hub_rendered = CleanRenderer().tool_call(
        "hub_msg",
        "lapis hello",
        "success",
        nested_width=80,
        result_summary="sent",
    )

    assert visible(write_rendered).startswith("      ✎ notes/status.md")
    assert solid_fg("✎", T().user_tag) in write_rendered
    assert visible(hub_rendered).startswith("      ✉ lapis hello")
    assert solid_fg("✉", T().ai_tag) in hub_rendered


def test_common_internal_operations_have_text_mode_glyphs():
    renderer = CleanRenderer()
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


def test_response_text_uses_assistant_text_color_not_success_green():
    rendered = CleanRenderer().response_block(["on it"], width=80)

    assert solid_fg("on it", T().assistant_text) in rendered
    assert solid_fg("on it", T().success[0]) not in rendered


def test_tool_result_body_is_dimmed_and_indented():
    rendered = CleanRenderer().tool_result(["raw output"])

    assert visible(rendered) == "      raw output"
    assert solid_fg("raw output", T().text_dim) in rendered


def test_agent_rows_use_foreground_icon_only():
    agent_color = (80, 140, 240)
    rendered = CleanRenderer().agent_message(
        "lapis -> sapphire\nlapis here.",
        agent_color=agent_color,
        tag_char=" > ",
        width=80,
    )

    plain = visible(rendered)

    assert plain.splitlines()[0] == " ◆ lapis -> sapphire"
    assert plain.splitlines()[1] == "   lapis here."
    assert "\033[0;48;" not in rendered
    assert solid_fg("lapis -> sapphire", agent_color) in rendered
    assert solid_fg("lapis here.", agent_color) in rendered
    assert_no_block_art(rendered)


def test_observed_agent_rows_use_dimmed_diamond_marker():
    rendered = CleanRenderer().agent_message(
        "sapphire -> lapis\nstanding by.",
        agent_color=(90, 150, 240),
        tag_char=" ~ ",
        observing=True,
        width=80,
    )

    plain = visible(rendered)

    assert plain.splitlines()[0] == " ◇ sapphire -> lapis"
    assert "\033[0;48;" not in rendered
    assert_no_block_art(rendered)


def test_turn_timing_info_row_has_no_background_if_rendered_directly():
    rendered = CleanRenderer().info_block("turn took 8.9s", width=80)
    plain = visible(rendered)

    assert plain == "ℹ turn took 8.9s"
    assert_no_background(rendered)
