"""Tests for compact clean renderer status rows."""

import re

from kollabor_tui.clean_renderer import CleanRenderer

ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def visible(text: str) -> str:
    """Strip ANSI codes for shape assertions."""
    return ANSI_RE.sub("", text)


def assert_no_background(rendered: str) -> None:
    assert "\033[48;" not in rendered
    assert "\033[0;48;" not in rendered
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

    samples = [
        (renderer.user_message("investigate p99", 80), "▌ investigate p99"),
        (
            renderer.tool_call(
                "Read",
                "app/api/orders/route.ts",
                "success",
                nested_width=80,
                result_summary="28 lines",
            ),
            "read app/api/orders/route.ts\n  ↳ 28 lines",
        ),
    ]

    for rendered, expected in samples:
        assert visible(rendered) == expected
        assert_no_background(rendered)


def test_agent_rows_use_foreground_icon_only():
    rendered = CleanRenderer().agent_message(
        "lapis -> sapphire\nlapis here.",
        tag_char=" > ",
        width=80,
    )

    plain = visible(rendered)

    assert plain.splitlines()[0] == "> lapis -> sapphire"
    assert plain.splitlines()[1] == "  lapis here."
    assert_no_background(rendered)
