"""Reasoning effort: profile field -> provider payload, in each native shape.

Effort is opt-in. Unset means the request carries no effort field at all, so
models that don't accept one keep working; an unrecognized level is dropped
before it can 400 the request.

Also covers top_p, which used to be accepted on a profile and silently
discarded in create_config_from_profile.
"""

import asyncio
import unittest
from types import SimpleNamespace

from kollabor_ai.profile_manager import EFFORT_LEVELS, LLMProfile
from kollabor_ai.providers.anthropic_provider import AnthropicProvider
from kollabor_ai.providers.custom_provider import CustomProvider
from kollabor_ai.providers.openai_provider import OpenAIProvider
from kollabor_ai.providers.registry import create_config_from_profile

MESSAGES = [{"role": "user", "content": "hi"}]


class EffortProfileTests(unittest.TestCase):
    def test_env_override_and_validation(self):
        import os

        profile = LLMProfile(name="work", provider="anthropic", model="claude-opus-5")
        self.assertEqual(profile.get_effort(), "")  # unset -> model default

        profile.effort = "XHigh "  # normalized
        self.assertEqual(profile.get_effort(), "xhigh")

        profile.effort = "turbo"  # not a real level -> dropped, never sent
        self.assertEqual(profile.get_effort(), "")

        os.environ["KOLLAB_WORK_EFFORT"] = "max"
        try:
            self.assertEqual(profile.get_effort(), "max")
        finally:
            del os.environ["KOLLAB_WORK_EFFORT"]

    def test_round_trips_through_dict(self):
        profile = LLMProfile(
            name="p", provider="anthropic", model="claude-opus-5", effort="high"
        )
        data = profile.to_dict()
        self.assertEqual(data["effort"], "high")
        self.assertEqual(LLMProfile.from_dict("p", data).effort, "high")

        # unset effort stays out of the dict entirely
        bare = LLMProfile(name="p", provider="anthropic", model="claude-opus-5")
        self.assertNotIn("effort", bare.to_dict())

    def test_levels_are_ordered_cheapest_first(self):
        self.assertEqual(EFFORT_LEVELS[0], "low")
        self.assertIn("xhigh", EFFORT_LEVELS)
        self.assertEqual(EFFORT_LEVELS[-1], "ultra")


class EffortPayloadTests(unittest.TestCase):
    def _config(self, provider, model, **kw):
        profile = LLMProfile(
            name="p", provider=provider, model=model, api_key="sk-testkey", **kw
        )
        return create_config_from_profile(profile.to_dict())

    def test_profile_fields_reach_the_config(self):
        # regression: top_p was dropped here, so the documented knob did nothing
        config = self._config("anthropic", "claude-opus-4-6", effort="high", top_p=0.9)
        self.assertEqual(config.effort, "high")
        self.assertEqual(config.top_p, 0.9)

    def test_anthropic_uses_output_config(self):
        request = AnthropicProvider(
            self._config("anthropic", "claude-opus-5", effort="xhigh")
        )._prepare_request(list(MESSAGES))
        self.assertEqual(request["output_config"], {"effort": "xhigh"})

    def test_openai_uses_reasoning_effort(self):
        params = OpenAIProvider(
            self._config("openai", "gpt-5.6", effort="high")
        )._prepare_request_params(list(MESSAGES), None, False)
        self.assertEqual(params["reasoning_effort"], "high")

    def test_omitted_when_unset(self):
        request = AnthropicProvider(
            self._config("anthropic", "claude-opus-5")
        )._prepare_request(list(MESSAGES))
        self.assertNotIn("output_config", request)

        params = OpenAIProvider(
            self._config("openai", "gpt-5.6")
        )._prepare_request_params(list(MESSAGES), None, False)
        self.assertNotIn("reasoning_effort", params)

    def test_custom_openai_compatible_payload(self):
        """OpenAI-compatible endpoints (xAI, Z.AI, local) get reasoning_effort."""
        from unittest.mock import patch

        provider = CustomProvider(self._config("custom", "grok-4.5", effort="high"))

        class _NoNetwork(Exception):
            pass

        def _boom(*a, **k):
            # the payload is recorded before the request is sent
            raise _NoNetwork()

        with patch("aiohttp.ClientSession", _boom):
            with self.assertRaises(_NoNetwork):
                asyncio.run(provider.call(list(MESSAGES)))

        self.assertEqual(provider.last_request_payload["reasoning_effort"], "high")


class _FakeProfile:
    name = "p"

    def __init__(self, effort="", provider="anthropic"):
        self.effort = effort
        self._provider = provider

    def get_provider(self):
        return self._provider

    def get_model(self):
        return "claude-opus-5"

    def get_effort(self):
        return self.effort


class _FakePM:
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


class ModelEffortCommandTests(unittest.TestCase):
    """/model effort [level] -- the surface that makes this discoverable."""

    def _handler(self, profile):
        from kollabor.commands.system_commands.handlers.model import (
            ModelCommandHandler,
        )

        return ModelCommandHandler(
            command_registry=None,
            event_bus=SimpleNamespace(get_service=lambda name: None),
            profile_manager=_FakePM(profile),
        )

    def test_shows_current_and_available(self):
        handler = self._handler(_FakeProfile())
        result = asyncio.run(handler._handle_effort(""))
        self.assertTrue(result.success)
        self.assertIn("(model default)", result.message)
        self.assertIn("xhigh", result.message)

    def test_sets_and_persists(self):
        handler = self._handler(_FakeProfile())
        result = asyncio.run(handler._handle_effort("XHigh"))
        self.assertTrue(result.success)
        name, kwargs = handler.profile_manager.updates[0]
        self.assertEqual(kwargs["effort"], "xhigh")
        self.assertTrue(kwargs["save_to_config"])

    def test_clears_back_to_model_default(self):
        handler = self._handler(_FakeProfile(effort="max"))
        asyncio.run(handler._handle_effort("default"))
        _, kwargs = handler.profile_manager.updates[0]
        self.assertEqual(kwargs["effort"], "")

    def test_rejects_unknown_level_without_touching_the_profile(self):
        handler = self._handler(_FakeProfile())
        result = asyncio.run(handler._handle_effort("turbo"))
        self.assertFalse(result.success)
        self.assertIn("Unknown effort level", result.message)
        self.assertEqual(handler.profile_manager.updates, [])

    def test_refuses_on_a_provider_that_drops_effort(self):
        # Gemini has no reasoning-effort parameter, so reporting success would
        # be a lie -- the field never reaches the request.
        handler = self._handler(_FakeProfile(provider="gemini"))
        result = asyncio.run(handler._handle_effort("high"))
        self.assertFalse(result.success)
        self.assertIn("no reasoning-effort parameter", result.message)
        self.assertEqual(handler.profile_manager.updates, [])

        # ...and the read-only form does not pretend either
        status = asyncio.run(handler._handle_effort(""))
        self.assertFalse(status.success)

    def test_effort_supported_set_matches_the_providers_that_send_it(self):
        """The advertised set must equal the payload builders that use it."""
        import pathlib

        import kollabor_ai.providers as providers_pkg
        from kollabor_ai.providers.tuning import EFFORT_SUPPORTED_PROVIDERS

        root = pathlib.Path(providers_pkg.__file__).parent
        sends_effort = {
            "anthropic": "anthropic_provider.py",
            "openai": "openai_provider.py",
            "azure_openai": "azure_provider.py",
            "openai_responses": "openai_responses_provider.py",
            "openrouter": "openrouter_provider.py",
            "custom": "custom_provider.py",
        }
        for provider, source in sends_effort.items():
            self.assertIn(provider, EFFORT_SUPPORTED_PROVIDERS)
            self.assertIn("effort_params", (root / source).read_text(), source)

        self.assertEqual(set(sends_effort), set(EFFORT_SUPPORTED_PROVIDERS))
        # gemini must NOT be advertised -- it never builds an effort field
        self.assertNotIn("effort_params", (root / "gemini_provider.py").read_text())
        self.assertNotIn("gemini", EFFORT_SUPPORTED_PROVIDERS)

    def test_docs_wire_format_table_lists_every_supported_provider(self):
        """The doc table drifted the moment openrouter joined the set."""
        import pathlib

        from kollabor_ai.providers.tuning import EFFORT_SUPPORTED_PROVIDERS

        doc = (
            pathlib.Path(__file__).resolve().parents[2]
            / "docs"
            / "features"
            / "reasoning-effort.md"
        )
        text = doc.read_text()
        table = text.split("## Wire format per provider", 1)[1].split("##", 1)[0]
        for provider in EFFORT_SUPPORTED_PROVIDERS:
            self.assertIn(f"`{provider}`", table, f"{provider} missing from the table")


if __name__ == "__main__":
    unittest.main()
