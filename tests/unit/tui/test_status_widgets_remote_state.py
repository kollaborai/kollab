"""Tests that profile/model/endpoint status widgets prefer ctx.remote_state.

Regression guard for bug 3 from phase 4.5 of the daemon transparency refactor:
in attach mode the widgets were reading `ctx.profile_manager.get_active_profile()`
directly, which is the client's shadow profile manager stuck on the default.
The daemon's actual active profile lives in `ctx.remote_state`, populated by
WidgetStateRefresher every ~2s. Widgets must read remote_state first and fall
back to the local profile_manager only when remote_state has nothing.

These tests do not touch the real render pipeline -- they construct minimal
widget contexts with stubbed profile managers and assert that the
rendered string contains the expected profile/model/endpoint text.
"""

import re
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

# Add packages/kollabor-tui to path for imports
sys.path.insert(
    0,
    str(
        Path(__file__).parent.parent.parent.parent / "packages" / "kollabor-tui" / "src"
    ),
)

from kollabor_tui.status.core_widgets import (  # noqa: E402
    render_endpoint,
    render_model,
    render_profile,
    render_stats,
)

# ANSI escape regex to strip color/style codes from widget output for assertions
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[mGKHfJ]")


def _strip_ansi(text: str) -> str:
    """Strip ANSI escape codes so tests can match on plain text."""
    return ANSI_ESCAPE.sub("", text)


def _make_ctx(
    *,
    remote_state: dict | None = None,
    local_profile_name: str = "",
    local_model: str = "",
    local_endpoint: str = "",
    local_provider: str = "",
) -> MagicMock:
    """Build a minimal WidgetContext-shaped mock.

    Args:
        remote_state: dict to assign to ctx.remote_state (None -> {})
        local_profile_name: name returned by local profile_manager.get_active_profile().name
        local_model: model returned by local profile.get_model()
        local_endpoint: endpoint returned by local profile.get_endpoint()
        local_provider: provider attribute on the local profile
    """
    profile_mock = MagicMock()
    profile_mock.name = local_profile_name
    profile_mock.get_model.return_value = local_model
    profile_mock.get_endpoint.return_value = local_endpoint
    profile_mock.provider = local_provider

    profile_manager = MagicMock()
    if local_profile_name:
        profile_manager.get_active_profile.return_value = profile_mock
    else:
        profile_manager.get_active_profile.return_value = None

    ctx = MagicMock()
    ctx.remote_state = remote_state if remote_state is not None else {}
    ctx.profile_manager = profile_manager
    return ctx


class TestRenderProfileRemoteStatePreference(unittest.TestCase):
    """render_profile should prefer ctx.remote_state['profile_name']."""

    def test_remote_state_wins_over_local(self) -> None:
        ctx = _make_ctx(
            remote_state={"profile_name": "openai-oauth"},
            local_profile_name="default",
        )
        out = _strip_ansi(render_profile(30, ctx))
        self.assertIn("openai-oauth", out)
        self.assertNotIn("default", out)

    def test_falls_back_to_local_when_remote_missing(self) -> None:
        ctx = _make_ctx(remote_state={}, local_profile_name="claude")
        out = _strip_ansi(render_profile(30, ctx))
        self.assertIn("claude", out)

    def test_falls_back_to_default_when_both_empty(self) -> None:
        ctx = _make_ctx(remote_state={}, local_profile_name="")
        out = _strip_ansi(render_profile(30, ctx))
        self.assertIn("default", out)


class TestRenderModelRemoteStatePreference(unittest.TestCase):
    """render_model should prefer ctx.remote_state['model']."""

    def test_remote_state_wins_over_local(self) -> None:
        ctx = _make_ctx(
            remote_state={"model": "gpt-5.4"},
            local_profile_name="default",
            local_model="",  # local has empty model (bug 3 scenario)
        )
        out = _strip_ansi(render_model(30, ctx))
        self.assertIn("gpt-5.4", out)
        self.assertNotIn("unknown", out)

    def test_falls_back_to_local_when_remote_missing(self) -> None:
        ctx = _make_ctx(
            remote_state={},
            local_profile_name="claude",
            local_model="claude-sonnet-4-6",
        )
        out = _strip_ansi(render_model(30, ctx))
        self.assertIn("claude-sonnet-4-6", out)

    def test_unknown_when_both_empty(self) -> None:
        """bug 3 original scenario: both remote_state and local profile are empty."""
        ctx = _make_ctx(
            remote_state={},
            local_profile_name="default",
            local_model="",
        )
        out = _strip_ansi(render_model(30, ctx))
        self.assertIn("unknown", out)

    def test_empty_string_in_remote_state_falls_through(self) -> None:
        """If remote_state has a key but the value is empty, we should
        still fall back to the local profile_manager rather than showing
        an empty widget. This catches the bug where remote refresh
        silently returned an empty string instead of None."""
        ctx = _make_ctx(
            remote_state={"model": ""},
            local_profile_name="claude",
            local_model="claude-sonnet-4-6",
        )
        out = _strip_ansi(render_model(30, ctx))
        self.assertIn("claude-sonnet-4-6", out)


class TestRenderEndpointRemoteStatePreference(unittest.TestCase):
    """render_endpoint should prefer ctx.remote_state['endpoint']."""

    def test_remote_state_endpoint_wins(self) -> None:
        ctx = _make_ctx(
            remote_state={
                "endpoint": "https://chatgpt.com/backend-api/codex",
                "provider": "openai_responses",
            },
            local_profile_name="default",
        )
        out = _strip_ansi(render_endpoint(40, ctx))
        self.assertIn("chatgpt.com", out)

    def test_remote_state_provider_used_when_endpoint_empty(self) -> None:
        """When remote endpoint is empty but provider is known, widget
        should map provider to its default host rather than falling
        back to local state."""
        ctx = _make_ctx(
            remote_state={"endpoint": "", "provider": "anthropic"},
            local_profile_name="claude",
            local_endpoint="https://api.something-else.com",
        )
        out = _strip_ansi(render_endpoint(40, ctx))
        # Remote provider hint wins over local endpoint when remote has a provider.
        self.assertIn("api.anthropic.com", out)

    def test_falls_back_to_local_when_remote_missing(self) -> None:
        ctx = _make_ctx(
            remote_state={},
            local_profile_name="claude",
            local_endpoint="https://api.anthropic.com",
            local_provider="anthropic",
        )
        out = _strip_ansi(render_endpoint(40, ctx))
        self.assertIn("api.anthropic.com", out)

    def test_shows_local_fallback_when_nothing_set(self) -> None:
        ctx = _make_ctx(remote_state={}, local_profile_name="")
        out = _strip_ansi(render_endpoint(40, ctx))
        # Default fallback is "local"
        self.assertIn("local", out)


class TestRenderStatsRemoteStatePreference(unittest.TestCase):
    """render_stats should prefer daemon stats over attach shadow stats."""

    def test_remote_state_cache_read_wins_over_partial_local_stats(self) -> None:
        ctx = MagicMock()
        ctx.remote_state = {
            "messages": 16,
            "input_tokens": 47000,
            "output_tokens": 200,
            "cache_read_tokens": 46800,
            "total_cost_usd": 0.0,
        }
        ctx.llm_service.session_stats = {
            "messages": 16,
            "input_tokens": 47000,
            "output_tokens": 200,
            "cache_read_tokens": 0,
            "total_cost_usd": 0.0,
        }

        out = _strip_ansi(render_stats(80, ctx))

        self.assertIn("⟳ 46.8K", out)


if __name__ == "__main__":
    unittest.main()
