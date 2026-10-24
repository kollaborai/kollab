"""Tests for hub feed roster readability and current agent activity."""

from types import SimpleNamespace

from kollabor_tui.color_contrast import contrast_ratio
from kollabor_tui.design_system import T, set_theme
from plugins.hub.feed import HubFeed, _color


def test_feed_lifts_dark_gem_colors_for_black_terminals():
    original = T().name
    set_theme("dark")
    try:
        color = _color("sapphire")

        assert color != (15, 82, 186)
        assert contrast_ratio(color, (0, 0, 0)) >= 6.5
    finally:
        set_theme(original)


def test_hub_feed_roster_shows_all_agents_and_current_task(monkeypatch):
    feed = HubFeed()
    agents = [
        SimpleNamespace(
            identity="koordinator",
            is_coordinator=True,
            state="working",
            current_task="reviewing capture output",
        ),
        SimpleNamespace(
            identity="lapis",
            is_coordinator=False,
            state="idle",
            current_task="",
        ),
    ]
    monkeypatch.setattr(feed, "get_live_agents", lambda: agents)
    monkeypatch.setattr(feed, "get_recent_messages", lambda limit=30: [])

    rendered = "\n".join(feed.generate_feed_lines(width=100, height=20))

    assert "koordinator" in rendered
    assert "working" in rendered
    assert "reviewing capture output" in rendered
    assert "lapis" in rendered
    assert "idle" in rendered
