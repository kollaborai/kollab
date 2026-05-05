"""Tests for dictionary utility functions."""

import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent))

from kollabor_events.dict_utils import (
    deep_merge,
    flatten_dict,
    merge_multiple,
    safe_get,
    safe_set,
    unflatten_dict,
)


class TestDictUtils(unittest.TestCase):
    """Test cases for dictionary utilities."""

    def test_deep_merge_basic(self):
        """Test basic deep merge functionality."""
        target = {"a": 1, "b": {"c": 2}}
        source = {"b": {"d": 3}, "e": 4}
        result = deep_merge(target, source)

        expected = {"a": 1, "b": {"c": 2, "d": 3}, "e": 4}
        self.assertEqual(result, expected)

    def test_deep_merge_overwrite(self):
        """Test that source values overwrite target values."""
        target = {"a": 1, "b": {"c": 2}}
        source = {"a": 10, "b": {"c": 20}}
        result = deep_merge(target, source)

        expected = {"a": 10, "b": {"c": 20}}
        self.assertEqual(result, expected)

    def test_deep_merge_nested_complex(self):
        """Test deep merge with complex nested structures."""
        target = {
            "config": {
                "database": {"host": "localhost", "port": 5432},
                "cache": {"enabled": True},
            },
            "plugins": ["auth", "logging"],
        }
        source = {
            "config": {
                "database": {"port": 3306, "name": "prod_db"},
                "api": {"timeout": 30},
            },
            "plugins": ["monitoring"],
        }

        result = deep_merge(target, source)

        expected = {
            "config": {
                "database": {"host": "localhost", "port": 3306, "name": "prod_db"},
                "cache": {"enabled": True},
                "api": {"timeout": 30},
            },
            "plugins": ["monitoring"],  # Lists are replaced, not merged
        }
        self.assertEqual(result, expected)

    def test_deep_merge_empty_dicts(self):
        """Test merge with empty dictionaries."""
        self.assertEqual(deep_merge({}, {"a": 1}), {"a": 1})
        self.assertEqual(deep_merge({"a": 1}, {}), {"a": 1})
        self.assertEqual(deep_merge({}, {}), {})

    def test_deep_merge_non_dict_inputs(self):
        """Test merge with non-dictionary inputs."""
        # Non-dict target
        result = deep_merge("not_a_dict", {"a": 1})
        self.assertEqual(result, {"a": 1})

        # Non-dict source
        result = deep_merge({"a": 1}, "not_a_dict")
        self.assertEqual(result, {"a": 1})

        # Both non-dict
        result = deep_merge("not_a_dict", "also_not_a_dict")
        self.assertEqual(result, {})

    def test_safe_get_basic(self):
        """Test basic safe_get functionality."""
        data = {"terminal": {"render_fps": 20}}

        self.assertEqual(safe_get(data, "terminal.render_fps"), 20)
        self.assertEqual(safe_get(data, "terminal"), {"render_fps": 20})
        self.assertEqual(safe_get(data, "missing.key", "default"), "default")

    def test_safe_get_edge_cases(self):
        """Test safe_get with edge cases."""
        data = {"a": {"b": None, "c": 0, "d": ""}}

        # Test None value
        self.assertIsNone(safe_get(data, "a.b"))

        # Test falsy values
        self.assertEqual(safe_get(data, "a.c"), 0)
        self.assertEqual(safe_get(data, "a.d"), "")

        # Test non-dict input
        self.assertEqual(safe_get("not_dict", "key", "default"), "default")

        # Test empty key path
        self.assertEqual(safe_get(data, "", "default"), "default")

    def test_safe_set_basic(self):
        """Test basic safe_set functionality."""
        data = {}

        # Set nested key
        self.assertTrue(safe_set(data, "terminal.render_fps", 30))
        self.assertEqual(data, {"terminal": {"render_fps": 30}})

        # Update existing key
        self.assertTrue(safe_set(data, "terminal.render_fps", 60))
        self.assertEqual(data["terminal"]["render_fps"], 60)

        # Add to existing structure
        self.assertTrue(safe_set(data, "terminal.shimmer_speed", 3))
        self.assertEqual(data["terminal"]["shimmer_speed"], 3)

    def test_safe_set_overwrite_non_dict(self):
        """Test safe_set when overwriting non-dict values."""
        data = {"terminal": "not_a_dict"}

        # Should overwrite non-dict value and log warning
        self.assertTrue(safe_set(data, "terminal.render_fps", 30))
        self.assertEqual(data, {"terminal": {"render_fps": 30}})

    def test_safe_set_edge_cases(self):
        """Test safe_set with edge cases."""
        # Non-dict input
        self.assertFalse(safe_set("not_dict", "key", "value"))

        # Empty key path
        self.assertFalse(safe_set({}, "", "value"))

    def test_merge_multiple(self):
        """Test merging multiple dictionaries."""
        configs = [{"a": 1, "b": {"c": 2}}, {"b": {"d": 3}, "e": 4}, {"a": 10, "f": 5}]

        result = merge_multiple(configs)
        expected = {"a": 10, "b": {"c": 2, "d": 3}, "e": 4, "f": 5}
        self.assertEqual(result, expected)

    def test_merge_multiple_edge_cases(self):
        """Test merge_multiple with edge cases."""
        # Empty list
        self.assertEqual(merge_multiple([]), {})

        # List with non-dict items (should skip them)
        configs = [{"a": 1}, "not_dict", {"b": 2}]
        result = merge_multiple(configs)
        self.assertEqual(result, {"a": 1, "b": 2})

    def test_flatten_dict(self):
        """Test dictionary flattening."""
        data = {
            "terminal": {"render_fps": 20, "shimmer_speed": 3},
            "plugins": {"llm": {"api_url": "localhost"}},
        }

        result = flatten_dict(data)
        expected = {
            "terminal.render_fps": 20,
            "terminal.shimmer_speed": 3,
            "plugins.llm.api_url": "localhost",
        }
        self.assertEqual(result, expected)

    def test_flatten_dict_custom_separator(self):
        """Test dictionary flattening with custom separator."""
        data = {"a": {"b": {"c": 1}}}
        result = flatten_dict(data, separator="-")
        expected = {"a-b-c": 1}
        self.assertEqual(result, expected)

    def test_unflatten_dict(self):
        """Test dictionary unflattening."""
        data = {
            "terminal.render_fps": 20,
            "terminal.shimmer_speed": 3,
            "plugins.llm.api_url": "localhost",
        }

        result = unflatten_dict(data)
        expected = {
            "terminal": {"render_fps": 20, "shimmer_speed": 3},
            "plugins": {"llm": {"api_url": "localhost"}},
        }
        self.assertEqual(result, expected)

    def test_round_trip_flatten_unflatten(self):
        """Test that flatten -> unflatten is identity for simple dicts."""
        original = {"terminal": {"render_fps": 20}, "plugins": {"llm": {"timeout": 30}}}

        flattened = flatten_dict(original)
        restored = unflatten_dict(flattened)
        self.assertEqual(original, restored)


if __name__ == "__main__":
    unittest.main()
