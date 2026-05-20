"""Tests for WidgetState typed container and merge logic."""

import time

from kollabor.application import merge_widget_state_snapshot
from kollabor.state.widget_state import WidgetState


class TestFromFlatDict:
    """WidgetState.from_flat_dict constructs from legacy flat dicts."""

    def test_populates_all_known_fields(self):
        d = {
            "messages": 10,
            "input_tokens": 500,
            "output_tokens": 200,
            "total_input_tokens": 5000,
            "total_output_tokens": 2000,
            "cache_read_tokens": 100,
            "cost_usd": 0.05,
            "total_cost_usd": 1.23,
            "session": "sess-abc",
            "is_processing": True,
            "bg_tasks": 2,
            "pending_tools": 1,
            "tmux_sessions": 0,
            "cwd": "/tmp/project",
            "git_branch": "main",
            "daemon_pid": 12345,
            "daemon_uptime": 3600.0,
            "runtime_mode": "attach",
            "hub_identity": "sapphire",
            "hub_is_coordinator": False,
            "hub_peers": 3,
            "mcp": {"connected": 1, "tools": 4},
            "profile_name": "openai-oauth",
            "model": "gpt-5.4",
            "provider": "openai",
            "endpoint": "https://api.openai.com",
            "approval_mode": "DEFAULT",
            "agent": "coder",
            "skills": "tdd, debugging",
        }
        state = WidgetState.from_flat_dict(d, source="refresher")

        assert state.messages == 10
        assert state.input_tokens == 500
        assert state.total_input_tokens == 5000
        assert state.total_output_tokens == 2000
        assert state.profile_name == "openai-oauth"
        assert state.model == "gpt-5.4"
        assert state.agent == "coder"
        assert state.skills == "tdd, debugging"
        assert state.runtime_mode == "attach"
        assert state.hub_identity == "sapphire"
        assert state.mcp == {"connected": 1, "tools": 4}
        assert state._source == "refresher"
        assert state._updated_at > 0

    def test_unknown_keys_are_dropped(self):
        d = {
            "messages": 5,
            "type": "state_snapshot",  # DisplayTap artifact
            "unknown_future_key": "whatever",
        }
        state = WidgetState.from_flat_dict(d, source="legacy_hub")

        assert state.messages == 5
        assert state._source == "legacy_hub"
        # No crash from unknown keys

    def test_partial_dict_uses_defaults(self):
        d = {"messages": 3, "input_tokens": 100}
        state = WidgetState.from_flat_dict(d)

        assert len(WidgetState.state_fields()) == 29
        assert state.messages == 3
        assert state.input_tokens == 100
        assert state.output_tokens == 0  # default
        assert state.profile_name == ""  # default
        assert state.is_processing is False  # default


class TestToDict:
    """WidgetState.to_dict produces backward-compatible flat dict."""

    def test_round_trip(self):
        original = WidgetState(
            messages=7,
            input_tokens=300,
            profile_name="claude",
            model="claude-sonnet-4-6",
            runtime_mode="daemon",
            hub_identity="lapis",
            mcp={"connected": 2, "tools": 8},
            _source="refresher",
        )
        d = original.to_dict()

        assert d["messages"] == 7
        assert d["input_tokens"] == 300
        assert d["profile_name"] == "claude"
        assert d["model"] == "claude-sonnet-4-6"
        assert d["runtime_mode"] == "daemon"
        assert d["hub_identity"] == "lapis"
        assert d["mcp"] == {"connected": 2, "tools": 8}
        assert d["_source"] == "refresher"
        assert "_updated_at" in d

    def test_dict_is_widget_compatible(self):
        """to_dict output can be read with .get() like widgets do."""
        state = WidgetState(profile_name="openai", model="gpt-5.4")
        d = state.to_dict()

        # Widget pattern: ctx.remote_state.get("key", default)
        assert d.get("profile_name", "") == "openai"
        assert d.get("model", "") == "gpt-5.4"
        assert d.get("missing_key", "fallback") == "fallback"


class TestUpdateFrom:
    """WidgetState.update_from merges without erasing existing keys."""

    def test_merge_preserves_keys_other_doesnt_set(self):
        base = WidgetState(
            profile_name="openai",
            model="gpt-5.4",
            cache_read_tokens=46800,
            total_cost_usd=1.23,
            _source="refresher",
        )
        # Legacy hub only provides messages + is_processing + hub_identity
        legacy = WidgetState(
            messages=16,
            is_processing=True,
            hub_identity="sapphire",
            _source="legacy_hub",
        )
        merged = base.update_from(legacy, source="legacy_hub")

        # Legacy keys win
        assert merged.messages == 16
        assert merged.is_processing is True
        assert merged.hub_identity == "sapphire"
        # Refresher keys preserved
        assert merged.profile_name == "openai"
        assert merged.model == "gpt-5.4"
        assert merged.cache_read_tokens == 46800
        assert merged.total_cost_usd == 1.23
        # Source tracks who wrote last
        assert merged._source == "legacy_hub"

    def test_merge_does_not_mutate_self(self):
        base = WidgetState(profile_name="openai", _source="refresher")
        other = WidgetState(profile_name="claude", _source="legacy_hub")

        merged = base.update_from(other, source="legacy_hub")

        assert base.profile_name == "openai"  # unchanged
        assert merged.profile_name == "claude"

    def test_merge_updates_timestamp(self):
        base = WidgetState(_updated_at=100.0)
        other = WidgetState()

        before = time.monotonic()
        merged = base.update_from(other, source="test")
        after = time.monotonic()

        assert before <= merged._updated_at <= after


class TestMergeFlatDict:
    """WidgetState.merge_flat_dict convenience wrapper."""

    def test_strips_type_key_and_merges(self):
        base = WidgetState(
            profile_name="openai",
            cache_read_tokens=46800,
            _source="refresher",
        )
        # Simulates DisplayTap payload from legacy hub
        event = {
            "type": "state_snapshot",
            "messages": 20,
            "is_processing": False,
            "hub_identity": "sapphire",
        }

        merged = base.merge_flat_dict(event, source="legacy_hub")

        # "type" key should NOT appear in any field
        d = merged.to_dict()
        assert "type" not in d
        # Legacy keys merged
        assert merged.messages == 20
        assert merged.hub_identity == "sapphire"
        # Refresher keys preserved
        assert merged.profile_name == "openai"
        assert merged.cache_read_tokens == 46800

    def test_from_legacy_excludes_type_key(self):
        """from_flat_dict should silently drop 'type' key."""
        d = {"type": "state_snapshot", "messages": 5}
        state = WidgetState.from_flat_dict(d)

        assert state.messages == 5
        d_out = state.to_dict()
        assert "type" not in d_out


class TestFreshnessDefaults:
    """Freshness metadata has sensible defaults."""

    def test_default_state_is_not_stale_or_degraded(self):
        state = WidgetState()
        assert state._stale is False
        assert state._degraded is False

    def test_source_tracks_producer(self):
        refresher = WidgetState.from_flat_dict({"messages": 1}, source="refresher")
        legacy = WidgetState.from_flat_dict({"messages": 2}, source="legacy_hub")

        assert refresher._source == "refresher"
        assert legacy._source == "legacy_hub"


class TestApplicationStateSnapshotMerge:
    """Attach DisplayTap snapshots merge instead of replacing remote_state."""

    def test_state_snapshot_merge_preserves_refresher_owned_keys(self):
        current = {
            "profile_name": "openai-oauth",
            "model": "glm-5.1",
            "cache_read_tokens": 46800,
            "total_cost_usd": 0.12,
            "_source": "state_service",
        }
        event = {
            "type": "state_snapshot",
            "messages": 16,
            "is_processing": False,
            "hub_identity": "koordinator",
        }

        merged = merge_widget_state_snapshot(current, event)

        assert "type" not in merged
        assert merged["messages"] == 16
        assert merged["is_processing"] is False
        assert merged["hub_identity"] == "koordinator"
        assert merged["profile_name"] == "openai-oauth"
        assert merged["model"] == "glm-5.1"
        assert merged["cache_read_tokens"] == 46800
        assert merged["total_cost_usd"] == 0.12
        assert merged["_source"] == "display_tap"

    def test_state_snapshot_merge_allows_explicit_zero_updates(self):
        current = {
            "messages": 16,
            "cache_read_tokens": 46800,
            "_source": "state_service",
        }
        event = {
            "type": "state_snapshot",
            "messages": 0,
            "cache_read_tokens": 0,
        }

        merged = merge_widget_state_snapshot(current, event)

        assert merged["messages"] == 0
        assert merged["cache_read_tokens"] == 0
