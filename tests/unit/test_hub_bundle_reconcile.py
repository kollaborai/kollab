"""Unit tests for hub role -> agent bundle reconciliation.

Covers ``desired_bundle_for_identity`` — the pure function the hub uses to
decide which agent bundle a given mesh identity should run. The koordinator
orchestrator bundle must belong ONLY to the elected coordinator; pool gems
run their declared ``agent_type`` (``coder`` by default); unknown names fall
back to the default bundle.
"""

import unittest

from plugins.hub.models import (
    COORDINATOR_IDENTITY,
    POOL_BY_NAME,
    desired_bundle_for_identity,
)


class TestDesiredBundleForIdentity(unittest.TestCase):
    def test_coordinator_identity_gets_koordinator_bundle(self):
        self.assertEqual(
            desired_bundle_for_identity(COORDINATOR_IDENTITY), "koordinator"
        )
        self.assertEqual(desired_bundle_for_identity("koordinator"), "koordinator")

    def test_gem_identity_gets_its_pool_agent_type(self):
        # pool.json ships every gem with agent_type "coder".
        for gem_name in ("lapis", "sapphire", "ruby", "peridot"):
            self.assertIn(gem_name, POOL_BY_NAME)
            expected = POOL_BY_NAME[gem_name].agent_type or "default"
            self.assertEqual(desired_bundle_for_identity(gem_name), expected)

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

    def test_gem_without_agent_type_uses_default(self):
        # Defensive: a gem whose agent_type is empty should fall back rather
        # than return an empty bundle name.
        gem = next(iter(POOL_BY_NAME.values()))
        original = gem.agent_type
        try:
            gem.agent_type = ""
            self.assertEqual(
                desired_bundle_for_identity(gem.name, default_bundle="default"),
                "default",
            )
        finally:
            gem.agent_type = original


if __name__ == "__main__":
    unittest.main()
