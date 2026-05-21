"""Tests for startup banner rendering."""

import re

from kollabor_tui import terminal_state
from kollabor_tui.visual_effects import BannerRenderer

ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def visible(text: str) -> str:
    """Strip ANSI codes for shape assertions."""
    return ANSI_RE.sub("", text)


def test_startup_banner_uses_compact_header(monkeypatch):
    """Startup banner is compact, width-safe, and not the old block logo."""
    monkeypatch.setattr(terminal_state, "get_global_width", lambda: 72)

    banner = BannerRenderer.create_kollabor_banner(
        "v1.2.3",
        context={
            "agent": "koordinator",
            "model": "gpt-5.5",
            "profile": "work",
            "skills": 4,
            "directory": "/Users/malmazan/dev/kollab",
        },
    )
    plain_lines = [visible(line) for line in banner.strip("\n").splitlines()]

    assert len(plain_lines) == 5
    assert plain_lines[0] == "▄" * 72
    assert plain_lines[-1] == "▀" * 72
    assert plain_lines[1].startswith("  kollab")
    assert "kollab console" not in plain_lines[1]
    assert "v1.2.3" in plain_lines[1]
    assert "koordinator" in plain_lines[2]
    assert "gpt-5.5" in plain_lines[2]
    assert "work" in plain_lines[2]
    assert "4 skills" in plain_lines[2]
    assert "  ~/dev/kollab" in plain_lines[3]
    assert all(len(line) <= 72 for line in plain_lines)
    assert not {"█", "─", "▌"} & set("\n".join(plain_lines))


def test_startup_banner_has_contrast_panel_background(monkeypatch):
    """Startup banner uses background color instead of foreground-only lines."""
    monkeypatch.setattr(terminal_state, "get_global_width", lambda: 72)

    banner = BannerRenderer.create_kollabor_banner(
        "v1.2.3",
        context={
            "agent": "koordinator",
            "model": "gpt-5.5",
            "profile": "work",
            "skills": 4,
            "directory": "/Users/malmazan/dev/kollab",
        },
    )

    assert "\033[0;48;" in banner


def test_startup_banner_border_is_visible_against_panel(monkeypatch):
    """Startup banner border uses a different color than the panel fill."""
    monkeypatch.setattr(terminal_state, "get_global_width", lambda: 72)

    banner = BannerRenderer.create_kollabor_banner("v1.2.3", context=None)
    lines = banner.strip("\n").splitlines()

    assert lines[0] != lines[1]


def test_startup_banner_uses_full_global_width(monkeypatch):
    """Startup banner aligns with status widget width instead of capping width."""
    monkeypatch.setattr(terminal_state, "get_global_width", lambda: 100)

    banner = BannerRenderer.create_kollabor_banner("v1.2.3", context=None)
    plain_lines = [visible(line) for line in banner.strip("\n").splitlines()]

    assert plain_lines[0] == "▄" * 100
    assert plain_lines[-1] == "▀" * 100
    assert all(len(line) == 100 for line in plain_lines)
