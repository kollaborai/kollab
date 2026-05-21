"""Tests for compact modern message rendering."""

import re

from kollabor_tui.message_renderer import ModernMessageRenderer

ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def visible(text: str) -> str:
    """Strip ANSI codes for shape assertions."""
    return ANSI_RE.sub("", text)


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

    assert len(lines) == 2
    assert lines[0].startswith("read ")
    assert "app/api/orders/route.ts" in lines[0]
    assert lines[1].startswith("  ↳ ")
    assert "28 lines" in lines[1]
    assert not {"▄", "▀", "█"} & set(plain)


def test_user_message_renders_as_compact_row():
    """User turns render as a single dense row, not a framed box."""
    rendered = ModernMessageRenderer().user_message(
        "investigate why /api/orders p99 jumped",
        width=80,
    )

    plain = visible(rendered)
    lines = plain.splitlines()

    assert lines == ["▌ investigate why /api/orders p99 jumped"]
    assert not {"▄", "▀", "█"} & set(plain)


def test_response_block_renders_as_compact_plain_lines():
    """Assistant responses render as plain compact lines."""
    rendered = ModernMessageRenderer().response_block(
        ["Plan updated", "", "Read app/api/orders/route.ts"],
        width=80,
    )

    plain = visible(rendered)
    lines = plain.splitlines()

    assert lines == ["Plan updated", "", "Read app/api/orders/route.ts"]
    assert not {"▄", "▀", "█"} & set(plain)


def test_info_block_renders_as_compact_status_row():
    """System info renders as a compact status row."""
    rendered = ModernMessageRenderer().info_block("Ready", width=80)

    plain = visible(rendered)

    assert plain == "ℹ Ready"
    assert "\033[48;" not in rendered
    assert not {"▄", "▀", "█"} & set(plain)


def test_success_block_renders_as_compact_status_row():
    """System success renders as a compact status row."""
    rendered = ModernMessageRenderer().success_block("attached to koordinator", width=80)

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
