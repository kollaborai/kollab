"""Unit tests for hub role -> agent bundle reconciliation.

Covers ``desired_bundle_for_identity`` — the pure function the hub uses to
decide which agent bundle a given mesh identity should run. The koordinator
orchestrator bundle must belong ONLY to the elected coordinator; personas
run their declared ``agent`` (``coder`` for the gems); unknown names fall
back to the default bundle.
"""

import unittest

from plugins.hub.models import COORDINATOR_IDENTITY, desired_bundle_for_identity
from plugins.hub.rosters import find_persona, roster_personas

POOL_BY_NAME = {p.name: p for p in roster_personas()}


class TestDesiredBundleForIdentity(unittest.TestCase):
    def test_coordinator_identity_gets_koordinator_bundle(self):
        self.assertEqual(
            desired_bundle_for_identity(COORDINATOR_IDENTITY), "koordinator"
        )
        self.assertEqual(desired_bundle_for_identity("koordinator"), "koordinator")

    def test_gem_identity_gets_its_roster_agent(self):
        # The gems roster ships every gem with agent "coder".
        for gem_name in ("lapis", "sapphire", "ruby", "peridot"):
            self.assertIn(gem_name, POOL_BY_NAME)
            expected = POOL_BY_NAME[gem_name].agent or "default"
            self.assertEqual(desired_bundle_for_identity(gem_name), expected)

    def test_a_numbered_gem_runs_its_gems_bundle(self):
        # Once every gem is live the pool hands out lapis-2, lapis-3, ...
        self.assertEqual(
            desired_bundle_for_identity("lapis-2"), desired_bundle_for_identity("lapis")
        )
        self.assertEqual(desired_bundle_for_identity("lapis-dev"), "default")
        self.assertEqual(desired_bundle_for_identity("agent-4242"), "default")

    def test_every_gem_resolves_and_is_never_koordinator(self):
        # No gem in the pool should ever be promoted to the orchestrator
        # bundle — that is the whole point of the reconcile.
        for gem_name in POOL_BY_NAME:
            bundle = desired_bundle_for_identity(gem_name)
            self.assertTrue(bundle)
            self.assertNotEqual(
                bundle,
                "koordinator",
                f"gem {gem_name!r} must not resolve to the koordinator bundle",
            )

    def test_unknown_identity_falls_back_to_default(self):
        self.assertEqual(desired_bundle_for_identity("not-a-real-gem"), "default")
        self.assertEqual(desired_bundle_for_identity(""), "default")

    def test_unknown_identity_honours_custom_default(self):
        self.assertEqual(
            desired_bundle_for_identity("not-a-real-gem", default_bundle="coder"),
            "coder",
        )

    def test_persona_from_another_roster_runs_its_agent(self):
        self.assertEqual(desired_bundle_for_identity("maya"), "research")
        self.assertEqual(desired_bundle_for_identity("marketing-crew/theo"), "creative-writer")

    def test_gem_without_agent_uses_default(self):
        # Defensive: a persona whose agent is empty should fall back rather
        # than return an empty bundle name.
        gem = find_persona(next(iter(POOL_BY_NAME)))
        original = gem.agent
        try:
            gem.agent = ""
            self.assertEqual(
                desired_bundle_for_identity(gem.name, default_bundle="default"),
                "default",
            )
        finally:
            gem.agent = original


if __name__ == "__main__":
    unittest.main()
