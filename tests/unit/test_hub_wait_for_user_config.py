"""plugins.hub.wait_for_user_enabled toggle."""

import unittest
from unittest.mock import MagicMock

from plugins.hub.nudge_engine import NudgeEngine
from plugins.hub.plugin import HubPlugin


class TestHubWaitForUserConfig(unittest.TestCase):
    def test_wait_feature_disabled_when_config_false(self):
        p = HubPlugin()
        p.config = MagicMock()

        def _get(key, default=None):
            if key == "plugins.hub.wait_for_user_enabled":
                return False
            return default

        p.config.get = MagicMock(side_effect=_get)
        self.assertFalse(p._wait_for_user_feature_enabled())

    def test_wait_feature_defaults_true_without_config(self):
        p = HubPlugin()
        p.config = None
        self.assertTrue(p._wait_for_user_feature_enabled())

    def test_wait_feature_true_when_config_true(self):
        p = HubPlugin()
        p.config = MagicMock()
        p.config.get = MagicMock(return_value=True)
        self.assertTrue(p._wait_for_user_feature_enabled())


class TestNudgeEngineWaitForUserDisabled(unittest.TestCase):
    def test_loop_nudge_omits_wait_tag_when_disabled(self):
        eng = NudgeEngine(loop_threshold=1)
        eng.wait_for_user_enabled = False
        tr = eng._get_tracker("lapis")
        tr.turns_hub_only = 5
        msg = eng.evaluate("lapis", 0)
        self.assertIsNotNone(msg)
        self.assertNotIn("<wait_for_user/>", msg)


if __name__ == "__main__":
    unittest.main()
