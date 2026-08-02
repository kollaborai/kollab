"""Profile timeouts are seconds, end to end.

The field docstring used to say milliseconds while every consumer read
seconds, and `_try_create_profile_from_env` hardcoded 30000 -- so any
env-var profile got an 8.3 hour HTTP timeout and a hung connection never
died. These tests pin the unit at each boundary the value crosses.
"""

import os
from unittest.mock import patch

from kollabor_ai.profile_manager import LLMProfile, ProfileManager
from kollabor_ai.providers.registry import create_config_from_profile


def _profile(**kw):
    return LLMProfile(
        name="test", provider="openai", model="gpt-4", api_key="sk-test", **kw
    )


def test_unset_timeout_inherits_provider_default():
    """0 must reach the provider as 'unset', not as a literal 0."""
    cfg = create_config_from_profile(_profile().to_dict())
    assert cfg.timeout == 120.0  # ProviderConfig default, in seconds


def test_explicit_timeout_passes_through_unconverted():
    cfg = create_config_from_profile(_profile(timeout=45).to_dict())
    assert cfg.timeout == 45


def test_env_profile_does_not_get_an_eight_hour_timeout():
    """The regression: env-created profiles hardcoded timeout=30000."""
    mgr = ProfileManager.__new__(ProfileManager)  # skip config I/O
    mgr._profiles = {}

    env = {
        "KOLLAB_ENVTEST_MODEL": "gpt-4",
        "KOLLAB_ENVTEST_PROVIDER": "openai",
        "KOLLAB_ENVTEST_API_KEY": "sk-test",
    }
    with patch.dict(os.environ, env, clear=False):
        assert mgr._try_create_profile_from_env("envtest") is True

    created = mgr._profiles["envtest"]
    assert created.get_timeout() == 0, "env profiles must inherit the provider default"

    cfg = create_config_from_profile(created.to_dict())
    assert cfg.timeout == 120.0, f"got {cfg.timeout}s ({cfg.timeout / 3600:.1f}h)"


def test_explicit_env_timeout_is_read_as_seconds():
    mgr = ProfileManager.__new__(ProfileManager)
    mgr._profiles = {}
    env = {
        "KOLLAB_ENVTEST2_MODEL": "gpt-4",
        "KOLLAB_ENVTEST2_PROVIDER": "openai",
        "KOLLAB_ENVTEST2_API_KEY": "sk-test",
        "KOLLAB_ENVTEST2_TIMEOUT": "90",
    }
    with patch.dict(os.environ, env, clear=False):
        assert mgr._try_create_profile_from_env("envtest2") is True
        cfg = create_config_from_profile(mgr._profiles["envtest2"].to_dict())
        assert cfg.timeout == 90


def test_suspiciously_large_timeout_warns(caplog):
    """A ms-looking value still works, but must not fail silently."""
    profile = _profile(timeout=30000)
    with caplog.at_level("WARNING"):
        assert profile.get_timeout() == 30000
    assert any("SECONDS" in r.message for r in caplog.records)


def test_normal_timeout_does_not_warn(caplog):
    with caplog.at_level("WARNING"):
        _profile(timeout=120).get_timeout()
    assert not [r for r in caplog.records if "SECONDS" in r.message]
