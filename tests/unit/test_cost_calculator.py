"""Tests for pricing_registry and cost_calculator."""

import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent))

from kollabor_ai.cost_calculator import calculate_cost
from kollabor_ai.pricing_registry import ModelPricing, PricingRegistry


class TestPricingRegistryMatching(unittest.TestCase):
    def setUp(self):
        PricingRegistry.reset()
        self.reg = PricingRegistry()
        self.reg.register_provider_pricing(
            "openai",
            "gpt-4o",
            ModelPricing(0.0000025, 0.00001, 0.5),
        )
        self.reg.register_provider_pricing(
            "openai",
            "gpt-4-turbo",
            ModelPricing(0.00001, 0.00003, 0.5),
        )
        self.reg.register_provider_pricing(
            "custom",
            "glm-5",
            ModelPricing(0.000001, 0.000002, 0.5),
        )

    def test_exact_match(self):
        p = self.reg.get_pricing("openai", "gpt-4o")
        self.assertIsNotNone(p)
        self.assertEqual(p.prompt_per_token, 0.0000025)

    def test_segment_prefix_match_gpt4o_dated(self):
        # gpt-4o should match gpt-4o-2024-05-13 (dated revision)
        p = self.reg.get_pricing("openai", "gpt-4o-2024-05-13")
        self.assertIsNotNone(p)
        self.assertEqual(p.prompt_per_token, 0.0000025)

    def test_segment_match_does_not_confuse_gpt4_vs_gpt4o(self):
        # gpt-4-turbo is registered; querying gpt-4o must NOT match gpt-4-turbo
        # (different second segment: "4o" vs "4")
        p = self.reg.get_pricing("openai", "gpt-4o")
        self.assertIsNotNone(p)
        self.assertEqual(p.prompt_per_token, 0.0000025)  # gpt-4o not turbo

    def test_dot_in_segment_matches(self):
        # glm-5 should match glm-5.1 (dot stays within segment)
        p = self.reg.get_pricing("custom", "glm-5.1")
        self.assertIsNotNone(p)
        self.assertEqual(p.prompt_per_token, 0.000001)

    def test_openrouter_namespace_strip(self):
        self.reg.register_provider_pricing(
            "openrouter",
            "gpt-4o",
            ModelPricing(0.0000025, 0.00001, 0.5),
        )
        p = self.reg.get_pricing("openrouter", "openai/gpt-4o")
        self.assertIsNotNone(p)

    def test_no_match_returns_none(self):
        self.assertIsNone(self.reg.get_pricing("openai", "nonexistent-model-999"))

    def test_unknown_provider_returns_none(self):
        self.assertIsNone(self.reg.get_pricing("nobody", "gpt-4o"))


class TestPricingRegistryLoad(unittest.TestCase):
    def setUp(self):
        PricingRegistry.reset()

    def test_load_defaults_bundled(self):
        reg = PricingRegistry()
        reg.load_defaults()
        p = reg.get_pricing("openai", "gpt-4o")
        self.assertIsNotNone(p)
        p2 = reg.get_pricing("anthropic", "claude-sonnet-4-6")
        self.assertIsNotNone(p2)


class TestCostCalculator(unittest.TestCase):
    def setUp(self):
        PricingRegistry.reset()

    def test_openai_formula_subtracts_cache_from_prompt(self):
        # spec: (1000-500)*0.0000025 + 100*0.00001 + 500*0.0000025*0.5
        #     = 0.00125 + 0.001 + 0.000625 = 0.002875
        cost = calculate_cost("openai", "gpt-4o", 1000, 100, 500)
        self.assertAlmostEqual(cost, 0.002875, places=7)

    def test_anthropic_formula_does_not_subtract_cache(self):
        # spec: 1000*0.000003 + 100*0.000015 + 500*0.000003*0.1
        #     = 0.003 + 0.0015 + 0.00015 = 0.00465
        cost = calculate_cost("anthropic", "claude-sonnet-4-6", 1000, 100, 500)
        self.assertAlmostEqual(cost, 0.00465, places=7)

    def test_unknown_provider_returns_zero(self):
        cost = calculate_cost("nobody", "xyz", 1000, 100, 0)
        self.assertEqual(cost, 0.0)

    def test_unknown_model_returns_zero(self):
        cost = calculate_cost("openai", "totally-fake-model-xyz", 1000, 100, 0)
        self.assertEqual(cost, 0.0)

    def test_gemini_provider_returns_zero(self):
        # gemini has no pricing formula, should be zero even if registered
        cost = calculate_cost("gemini", "gemini-pro", 1000, 100, 0)
        self.assertEqual(cost, 0.0)

    def test_zero_tokens_returns_zero(self):
        cost = calculate_cost("openai", "gpt-4o", 0, 0, 0)
        self.assertEqual(cost, 0.0)

    def test_no_cache_read_matches_simple_formula(self):
        # prompt only * price + completion * price
        cost = calculate_cost("openai", "gpt-4o", 1000, 100, 0)
        expected = 1000 * 0.0000025 + 100 * 0.00001
        self.assertAlmostEqual(cost, expected, places=7)

    def test_cache_read_equals_prompt_still_nonnegative(self):
        # defensive: if cache_read == prompt, unique_prompt = 0
        cost = calculate_cost("openai", "gpt-4o", 500, 100, 500)
        expected = 0 + 100 * 0.00001 + 500 * 0.0000025 * 0.5
        self.assertAlmostEqual(cost, expected, places=7)


class TestSessionStatsBackwardCompat(unittest.TestCase):
    def test_from_dict_filters_unknown_keys(self):
        from kollabor.state.snapshots import SessionStats

        # old snapshot without cost fields + with a stray key
        raw = {
            "messages": 5,
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_tokens": 10,
            "legacy_unknown_field": "should_be_dropped",
        }
        s = SessionStats.from_dict(raw)
        self.assertEqual(s.messages, 5)
        self.assertEqual(s.cost_usd, 0.0)
        self.assertEqual(s.total_cost_usd, 0.0)

    def test_from_dict_picks_up_new_cost_fields(self):
        from kollabor.state.snapshots import SessionStats

        raw = {"messages": 1, "cost_usd": 0.05, "total_cost_usd": 1.25}
        s = SessionStats.from_dict(raw)
        self.assertEqual(s.cost_usd, 0.05)
        self.assertEqual(s.total_cost_usd, 1.25)


if __name__ == "__main__":
    unittest.main()
