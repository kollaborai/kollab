"""The daemon's profile snapshot carries the effort its next request will send."""

from kollabor.state.local import LocalStateService
from kollabor_ai import LLMProfile


def _snapshot(**fields):
    profile = LLMProfile(name="p", provider="anthropic", model="claude-opus-4-5", **fields)
    # _profile_to_snapshot never reads self
    return LocalStateService._profile_to_snapshot(None, profile, is_active=True)


def test_snapshot_reports_the_validated_effort():
    assert _snapshot(effort="HIGH").effort == "high"
    assert _snapshot(effort="HIGH").to_dict()["effort"] == "high"


def test_snapshot_drops_an_unknown_effort_and_defaults_to_none():
    assert _snapshot(effort="warp").effort == ""
    assert _snapshot().effort == ""
