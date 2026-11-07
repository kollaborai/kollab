"""Review round 1 on #559: where ``none`` and ``minimal`` are refused or kept.

Finding 1: an Anthropic profile could store ``none``/``minimal`` (route, launch
flag, env var, copy) and report it as active while the request never carried it.
It is refused when written, and what is read back is what is sent.
Finding 2: the loadout editors (Settings panel and terminal ``/llm``) kept their
own level list without the two levels, so a stored value was refused or dropped.
"""

import unittest
from types import SimpleNamespace
from unittest import mock

import plugins.altview.loadout_altview as loadout_altview
from kollabor.panels.llm import _loadout_values
from kollabor_ai import LLMProfile, ProfileManager

NEW_LEVELS = ("none", "minimal")


class _Manager(ProfileManager):
    def __init__(self):
        self._profiles = {
            "oa": LLMProfile(name="oa", provider="openai", model="gpt-5.6"),
            "an": LLMProfile(
                name="an",
                provider="anthropic",
                model="claude-x",
                api_key="test-key",
                auth_type="api_key",
            ),
        }
        self._invalid_profile_errors = {}
        self._active_profile_name = "oa"

    def save_profile_values_to_config(self, profile, *args, **kwargs):
        return True

    def add_profile(self, profile, save_to_config=False):
        if profile.name in self._profiles:
            return False
        self._profiles[profile.name] = profile
        return True


class AnthropicStoredLevelTests(unittest.TestCase):
    def test_read_back_matches_what_is_sent(self):
        # hand-edited config or env var: the level is reported as unset, not as
        # a level the provider never receives
        for level in NEW_LEVELS:
            with self.subTest(level=level):
                profile = LLMProfile(
                    name="p", provider="anthropic", model="claude-x", effort=level
                )
                self.assertEqual(profile.get_effort(), "")
                self.assertNotIn("effort", profile.to_dict())

    def test_openai_keeps_the_level(self):
        profile = LLMProfile(
            name="p", provider="openai", model="gpt-5.6", effort="minimal"
        )
        self.assertEqual(profile.get_effort(), "minimal")

    def test_update_profile_refuses_and_leaves_profile_unchanged(self):
        pm = _Manager()
        for level in NEW_LEVELS:
            with self.subTest(level=level):
                with self.assertRaises(ValueError):
                    pm.update_profile("an", effort=level, save_to_config=False)
                self.assertEqual(pm.get_profile("an").effort, "")

    def test_launch_flag_path_surfaces_the_refusal(self):
        # --effort goes through update_profile; a refusal must not be a quiet False
        pm = _Manager()
        pm._active_profile_name = "an"
        with self.assertRaisesRegex(ValueError, "minimal"):
            pm.update_profile("an", effort="minimal", save_to_config=False)

    def test_duplicate_refuses_for_anthropic(self):
        pm = _Manager()
        with self.assertRaises(ValueError):
            pm.duplicate_profile("an", "an2", overrides={"effort": "none"})


class LoadoutEditorTests(unittest.TestCase):
    def test_panel_accepts_each_level_for_openai_shaped_loadout(self):
        for level in NEW_LEVELS:
            with self.subTest(level=level):
                values, errors = _loadout_values(
                    {"effort": level, "temperature": 0.7}, False, "gpt-5"
                )
                self.assertEqual(errors, {})
                self.assertEqual(values["effort"], level)

    def test_panel_refuses_each_level_for_anthropic_loadout(self):
        for level in NEW_LEVELS:
            with self.subTest(level=level):
                _, errors = _loadout_values(
                    {"effort": level, "temperature": 0.7},
                    False,
                    "claude-x",
                    provider="anthropic",
                )
                self.assertIn("effort", errors)

    def test_panel_form_offers_levels_by_provider(self):
        from kollabor.panels.llm import effort_options

        for level in NEW_LEVELS:
            self.assertIn(level, effort_options("openai"))
            self.assertNotIn(level, effort_options("anthropic"))
        self.assertEqual(effort_options("openai")[0], "default")

    def test_terminal_form_keeps_stored_level_and_offers_it(self):
        class _Profile:
            def get_endpoint(self):
                return "https://api.openai.com/v1"

            def get_provider(self):
                return "openai"

        class _PM:
            def get_profile(self, name):
                return _Profile()

        class _Loadouts:
            def list_loadouts(self):
                return []

        base = SimpleNamespace(
            name="gpt-5",
            provider_profile="oa",
            model="gpt-5",
            temperature=None,
            effort="minimal",
            max_tokens=None,
            description="",
            implicit=True,
        )
        with mock.patch.object(loadout_altview, "_model_registry_info", lambda m: {}):
            view = loadout_altview.LoadoutFormAltView()
            view.set_context(manager=_Loadouts(), profile_manager=_PM(), base=base)
            view._build_widgets()
        self.assertEqual(view._effort_widget.get_pending_value(), "minimal")
        self.assertIn("none", view._effort_widget.options)


if __name__ == "__main__":
    unittest.main()
