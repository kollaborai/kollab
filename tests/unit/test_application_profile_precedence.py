"""Profile precedence between persisted settings and agent metadata."""

from kollabor.application import _should_apply_agent_preferred_profile


def test_agent_default_does_not_override_configured_profile():
    assert not _should_apply_agent_preferred_profile("default", "openai-oauth")


def test_agent_default_remains_usable_when_no_profile_is_selected():
    assert _should_apply_agent_preferred_profile("default", "default")


def test_non_default_agent_profile_overrides_persisted_profile():
    assert _should_apply_agent_preferred_profile("fast", "openai-oauth")
