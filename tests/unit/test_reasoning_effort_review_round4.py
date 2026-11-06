"""Review round 4 on #559: an ``auto`` profile gets the effort rules of the provider it resolves to.

``/model effort`` and the model panel read the raw ``auto``, which is not an
effort provider. So an ``auto`` profile whose key resolves to OpenAI was told it
had no reasoning-effort parameter, and one that resolves to Anthropic was offered
``none``/``minimal`` that its requests never send.
"""

import asyncio
import unittest
from types import SimpleNamespace

from kollabor_ai import LLMProfile


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


def _handler(pm):
    from kollabor.commands.system_commands.handlers.model import ModelCommandHandler

    return ModelCommandHandler(
        command_registry=None,
        event_bus=SimpleNamespace(get_service=lambda name: None),
        profile_manager=pm,
    )


def _auto(api_key):
    return LLMProfile(name="auto", provider="auto", model="model-x", api_key=api_key)


def _available(message):
    return next(line for line in message.splitlines() if "Available:" in line)


class AutoResolvingToOpenAITests(unittest.TestCase):
    def test_every_level_is_accepted_and_stored(self):
        for level in ("none", "minimal", "low"):
            with self.subTest(level=level):
                profile = _auto("sk-test-key")
                result = asyncio.run(_handler(_FakePM(profile))._handle_effort(level))
                self.assertTrue(result.success, result.message)
                self.assertEqual(profile.get_effort(), level)

    def test_show_lists_the_levels_openai_takes(self):
        profile = _auto("sk-test-key")
        result = asyncio.run(_handler(_FakePM(profile))._handle_effort(""))
        self.assertTrue(result.success, result.message)
        self.assertIn("minimal", _available(result.message))
        self.assertIn("none", _available(result.message))


class AutoResolvingToAnthropicTests(unittest.TestCase):
    def test_show_lists_no_level_anthropic_never_sends(self):
        profile = _auto("sk-ant-test-key")
        result = asyncio.run(_handler(_FakePM(profile))._handle_effort(""))
        self.assertTrue(result.success, result.message)
        self.assertNotIn("minimal", _available(result.message))
        self.assertNotIn("none", _available(result.message))

    def test_minimal_is_refused_and_nothing_is_stored(self):
        profile = _auto("sk-ant-test-key")
        pm = _FakePM(profile)
        result = asyncio.run(_handler(pm)._handle_effort("minimal"))
        self.assertFalse(result.success)
        self.assertIn("Unknown effort level", result.message)
        self.assertEqual(pm.updates, [])


class AutoModelPanelTests(unittest.TestCase):
    def test_effort_field_is_editable_and_offers_minimal_for_openai(self):
        from kollabor.panels.model import _effort_control

        profile = _auto("sk-test-key")
        field = asyncio.run(_effort_control(_handler(_FakePM(profile)), profile))
        self.assertTrue(field.get("editable", True), field)
        self.assertIn("minimal", field["options"])
