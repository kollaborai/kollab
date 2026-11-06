"""Review round 3 on #559.

The shipped ``default`` profile has ``provider: "auto"``, which resolves to
Anthropic when its key starts with ``sk-ant-``. The unsent-level rule (refuse
``none``/``minimal`` at write time, read them back as unset, leave them out of the
pickers) matched only the literal string ``"anthropic"``, so an ``auto`` profile
that resolves to Anthropic stored and reported a level its requests never carry.
"""

import unittest

from kollabor.panels.llm import effort_options, resolve_provider_type
from kollabor_ai import LLMProfile, ProfileManager
from kollabor_ai.loadout_manager import Loadout, loadout_sendable_effort
from kollabor_ai.providers.registry import ProviderType, create_config_from_profile
from kollabor_ai.providers.tuning import effort_params

NEW_LEVELS = ("none", "minimal")


class _Manager(ProfileManager):
    def __init__(self):
        self._profiles = {
            "auto-an": LLMProfile(
                name="auto-an",
                provider="auto",
                model="claude-sonnet-5",
                api_key="sk-ant-test-key",
            ),
            "auto-oa": LLMProfile(
                name="auto-oa",
                provider="auto",
                model="gpt-5.6",
                api_key="sk-test-key",
            ),
        }
        self._invalid_profile_errors = {}
        self._active_profile_name = "auto-an"

    def save_profile_values_to_config(self, profile, *args, **kwargs):
        return True


class AutoProviderResolvingToAnthropicTests(unittest.TestCase):
    def test_stored_level_reads_back_as_unset_and_request_agrees(self):
        for level in NEW_LEVELS:
            with self.subTest(level=level):
                profile = LLMProfile(
                    name="auto-an",
                    provider="auto",
                    model="claude-sonnet-5",
                    api_key="sk-ant-test-key",
                    effort=level,
                )
                self.assertEqual(profile.get_effort(), "")
                self.assertNotIn("effort", profile.to_dict())
                cfg = create_config_from_profile(profile.to_dict())
                self.assertEqual(cfg.provider, ProviderType.ANTHROPIC)
                self.assertEqual(effort_params(cfg, ProviderType.ANTHROPIC), {})

    def test_update_profile_refuses_and_leaves_profile_unchanged(self):
        pm = _Manager()
        for level in NEW_LEVELS:
            with self.subTest(level=level):
                with self.assertRaises(ValueError):
                    pm.update_profile("auto-an", effort=level, save_to_config=False)
                self.assertEqual(pm.get_profile("auto-an").effort, "")

    def test_update_refuses_when_the_key_written_makes_it_anthropic(self):
        pm = _Manager()
        for level in NEW_LEVELS:
            with self.subTest(level=level):
                with self.assertRaises(ValueError):
                    pm.update_profile(
                        "auto-oa",
                        effort=level,
                        api_key="sk-ant-other-key",
                        save_to_config=False,
                    )
                self.assertEqual(pm.get_profile("auto-oa").effort, "")

    def test_duplicate_refuses(self):
        pm = _Manager()
        with self.assertRaises(ValueError):
            pm.duplicate_profile("auto-an", "copy", overrides={"effort": "minimal"})

    def test_auto_profile_resolving_to_openai_keeps_the_level(self):
        pm = _Manager()
        for level in NEW_LEVELS:
            with self.subTest(level=level):
                self.assertTrue(
                    pm.update_profile("auto-oa", effort=level, save_to_config=False)
                )
                self.assertEqual(pm.get_profile("auto-oa").get_effort(), level)

    def test_pickers_leave_the_levels_out(self):
        pm = _Manager()
        provider = resolve_provider_type(pm, "auto-an")
        self.assertEqual(provider, "anthropic")
        for level in NEW_LEVELS:
            self.assertNotIn(level, effort_options(provider))
        self.assertIn("minimal", effort_options(resolve_provider_type(pm, "auto-oa")))

    def test_loadout_level_is_dropped_but_the_preset_applies(self):
        pm = _Manager()
        for level in NEW_LEVELS:
            with self.subTest(level=level):
                loadout = Loadout(
                    name="fast",
                    provider_profile="auto-an",
                    model="claude-sonnet-5",
                    effort=level,
                )
                self.assertEqual(loadout_sendable_effort(pm, loadout), "")
        keep = Loadout(
            name="fast", provider_profile="auto-oa", model="gpt-5.6", effort="minimal"
        )
        self.assertEqual(loadout_sendable_effort(pm, keep), "minimal")
