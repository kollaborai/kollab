"""Regression for #559: effort levels ``none`` and ``minimal`` could not be picked or sent.

``EFFORT_LEVELS`` left them out, so ``LLMProfile.get_effort()`` dropped them before
a request was built, the profile route refused them, ``/model effort`` refused
them, and ``/model effort none`` cleared the level instead of setting it. Anthropic
documents only ``low`` to ``max``, so these levels must stay out of its request
and its picker.
"""

import asyncio
import unittest
from types import SimpleNamespace

from kollabor_engine.routes.profiles import _normalize_effort

from kollabor_ai.profile_manager import LLMProfile
from kollabor_ai.providers.anthropic_provider import AnthropicProvider
from kollabor_ai.providers.openai_provider import OpenAIProvider
from kollabor_ai.providers.registry import create_config_from_profile
from kollabor_ai.providers.tuning import EffortStyle, effort_params

MESSAGES = [{"role": "user", "content": "hi"}]
NEW_LEVELS = ("none", "minimal")


class _FakePM:
    """Records update_profile calls and applies them the way ProfileManager does."""

    def __init__(self, profile):
        self.profile = profile
        self.updates = []

    def get_active_profile(self):
        return self.profile

    def update_profile(self, name, **kw):
        self.updates.append((name, kw))
        if "effort" in kw:
            self.profile.effort = kw["effort"]
        return True


def _handler(profile):
    from kollabor.commands.system_commands.handlers.model import ModelCommandHandler

    return ModelCommandHandler(
        command_registry=None,
        event_bus=SimpleNamespace(get_service=lambda name: None),
        profile_manager=_FakePM(profile),
    )


class NoneAndMinimalStoredTests(unittest.TestCase):
    def test_profile_keeps_each_level(self):
        for level in NEW_LEVELS:
            with self.subTest(level=level):
                profile = LLMProfile(
                    name="p", provider="openai", model="gpt-5.6", effort=level
                )
                self.assertEqual(profile.get_effort(), level)
                self.assertEqual(profile.to_dict()["effort"], level)

    def test_profile_route_accepts_each_level(self):
        for level in NEW_LEVELS:
            with self.subTest(level=level):
                self.assertEqual(_normalize_effort(f" {level.upper()} "), level)

    def test_unknown_level_is_still_dropped_and_refused(self):
        # widening the list must not switch the gate off
        with self.assertRaises(ValueError):
            _normalize_effort("turbo")
        profile = LLMProfile(
            name="p", provider="openai", model="gpt-5.6", effort="turbo"
        )
        self.assertEqual(profile.get_effort(), "")


class NoneAndMinimalRequestTests(unittest.TestCase):
    def _config(self, provider, model, effort):
        profile = LLMProfile(
            name="p",
            provider=provider,
            model=model,
            api_key="sk-testkey",
            effort=effort,
        )
        return create_config_from_profile(profile.to_dict())

    def test_openai_request_carries_each_level(self):
        for level in NEW_LEVELS:
            with self.subTest(level=level):
                params = OpenAIProvider(
                    self._config("openai", "gpt-5.6", level)
                )._prepare_request_params(list(MESSAGES), None, False)
                self.assertEqual(params["reasoning_effort"], level)

    def test_responses_and_openai_compatible_shapes_pass_them_through(self):
        for level in NEW_LEVELS:
            with self.subTest(level=level):
                config = SimpleNamespace(effort=level)
                self.assertEqual(
                    effort_params(config, EffortStyle.RESPONSES),
                    {"reasoning": {"effort": level}},
                )
                self.assertEqual(
                    effort_params(config, EffortStyle.OPENAI),
                    {"reasoning_effort": level},
                )

    def test_anthropic_request_never_carries_them(self):
        for level in NEW_LEVELS:
            with self.subTest(level=level):
                request = AnthropicProvider(
                    self._config("anthropic", "claude-opus-5", level)
                )._prepare_request(list(MESSAGES))
                self.assertNotIn("output_config", request)

        # the rest of the scale still reaches Anthropic
        request = AnthropicProvider(
            self._config("anthropic", "claude-opus-5", "low")
        )._prepare_request(list(MESSAGES))
        self.assertEqual(request["output_config"], {"effort": "low"})


class NoneAndMinimalModelEffortTests(unittest.TestCase):
    def test_openai_picker_offers_each_level(self):
        handler = _handler(LLMProfile(name="p", provider="openai", model="gpt-5.6"))
        levels = asyncio.run(
            handler._model_effort_levels(handler.profile_manager.get_active_profile())
        )
        for level in NEW_LEVELS:
            self.assertIn(level, levels)

    def test_anthropic_picker_does_not_offer_them(self):
        handler = _handler(
            LLMProfile(name="p", provider="anthropic", model="claude-opus-5")
        )
        levels = asyncio.run(
            handler._model_effort_levels(handler.profile_manager.get_active_profile())
        )
        for level in NEW_LEVELS:
            self.assertNotIn(level, levels)
        self.assertIn("low", levels)
        self.assertIn("max", levels)

    def test_none_is_set_not_cleared(self):
        profile = LLMProfile(name="p", provider="openai", model="gpt-5.6", effort="high")
        handler = _handler(profile)
        result = asyncio.run(handler._handle_effort("none"))
        self.assertTrue(result.success, result.message)
        _, kwargs = handler.profile_manager.updates[0]
        self.assertEqual(kwargs["effort"], "none")

    def test_minimal_is_accepted_on_openai(self):
        handler = _handler(LLMProfile(name="p", provider="openai", model="gpt-5.6"))
        result = asyncio.run(handler._handle_effort("minimal"))
        self.assertTrue(result.success, result.message)
        _, kwargs = handler.profile_manager.updates[0]
        self.assertEqual(kwargs["effort"], "minimal")

    def test_anthropic_refuses_minimal_and_none_still_clears(self):
        profile = LLMProfile(
            name="p", provider="anthropic", model="claude-opus-5", effort="max"
        )
        handler = _handler(profile)

        refused = asyncio.run(handler._handle_effort("minimal"))
        self.assertFalse(refused.success)
        self.assertEqual(handler.profile_manager.updates, [])

        # where none is not a level for the provider, its old meaning stays
        cleared = asyncio.run(handler._handle_effort("none"))
        self.assertTrue(cleared.success, cleared.message)
        _, kwargs = handler.profile_manager.updates[0]
        self.assertEqual(kwargs["effort"], "")


if __name__ == "__main__":
    unittest.main()
