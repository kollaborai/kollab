"""Tests for System Monitor color threshold functionality."""

import sys
import unittest
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent))

from kollabor_tui.design_system import T, set_theme
from kollabor_tui.status.system_monitor import (
    color_threshold,
    format_system_metric,
    get_status_label,
    get_sysmon_config,
)


class TestColorThreshold(unittest.TestCase):
    """Test cases for color_threshold function."""

    def setUp(self):
        """Set up test fixtures."""
        # Use lime theme for consistent testing.
        set_theme("lime")
        self.success = T().success[0]
        self.warning = T().warning[0]
        self.error = T().error[0]
        self.dimmed = T().text_dim

    def test_green_threshold_healthy_range(self):
        """Test that values <70% return green (success) color."""
        # Boundary cases
        self.assertEqual(color_threshold(0), self.success)  # 0%
        self.assertEqual(color_threshold(1), self.success)  # 1%
        self.assertEqual(color_threshold(50), self.success)  # 50%
        self.assertEqual(color_threshold(69), self.success)  # 69%

        # Float values
        self.assertEqual(color_threshold(45.5), self.success)
        self.assertEqual(color_threshold(69.99), self.success)

    def test_yellow_threshold_elevated_range(self):
        """Test that values 70-89% return yellow (warning) color."""
        # Boundary cases
        self.assertEqual(color_threshold(70), self.warning)  # 70%
        self.assertEqual(color_threshold(75), self.warning)  # 75%
        self.assertEqual(color_threshold(80), self.warning)  # 80%
        self.assertEqual(color_threshold(89), self.warning)  # 89%

        # Float values
        self.assertEqual(color_threshold(70.0), self.warning)
        self.assertEqual(color_threshold(89.99), self.warning)

    def test_red_threshold_critical_range(self):
        """Test that values >=90% return red (error) color."""
        # Boundary cases
        self.assertEqual(color_threshold(90), self.error)  # 90%
        self.assertEqual(color_threshold(95), self.error)  # 95%
        self.assertEqual(color_threshold(99), self.error)  # 99%
        self.assertEqual(color_threshold(100), self.error)  # 100%

        # Float values
        self.assertEqual(color_threshold(90.0), self.error)
        self.assertEqual(color_threshold(99.9), self.error)

    def test_none_returns_dimmed(self):
        """Test that None value returns dimmed text color."""
        result = color_threshold(None)
        self.assertEqual(result, self.dimmed)

    def test_negative_values_return_dimmed(self):
        """Test that negative values return dimmed text color."""
        self.assertEqual(color_threshold(-1), self.dimmed)
        self.assertEqual(color_threshold(-100), self.dimmed)
        self.assertEqual(color_threshold(-0.5), self.dimmed)

    def test_values_over_100_return_dimmed(self):
        """Test that values >100 return dimmed text color."""
        self.assertEqual(color_threshold(101), self.dimmed)
        self.assertEqual(color_threshold(150), self.dimmed)
        self.assertEqual(color_threshold(100.5), self.dimmed)

    def test_string_numeric_values_handled(self):
        """Test that numeric strings are converted correctly."""
        self.assertEqual(color_threshold("50"), self.success)  # String number
        self.assertEqual(color_threshold("75.5"), self.warning)  # String float
        self.assertEqual(color_threshold("95"), self.error)  # String int

    def test_non_numeric_strings_return_dimmed(self):
        """Test that non-numeric strings return dimmed text color."""
        self.assertEqual(color_threshold("invalid"), self.dimmed)
        self.assertEqual(color_threshold("N/A"), self.dimmed)
        self.assertEqual(color_threshold(""), self.dimmed)

    def test_nan_returns_dimmed(self):
        """Test that NaN values return dimmed text color."""
        import math

        result = color_threshold(float("nan"))
        self.assertEqual(result, self.dimmed)
        self.assertTrue(math.isnan(float("nan")))

    def test_inf_returns_dimmed(self):
        """Test that infinity values return dimmed text color."""
        self.assertEqual(color_threshold(float("inf")), self.dimmed)
        self.assertEqual(color_threshold(float("-inf")), self.dimmed)


class TestGetStatusLabel(unittest.TestCase):
    """Test cases for get_status_label function."""

    def setUp(self):
        """Set up test fixtures."""
        set_theme("lime")

    def test_label_ok_for_healthy(self):
        """Test that healthy values return 'ok' label."""
        self.assertEqual(get_status_label(0), "ok")
        self.assertEqual(get_status_label(50), "ok")
        self.assertEqual(get_status_label(69), "ok")

    def test_label_warning_for_elevated(self):
        """Test that elevated values return 'warning' label."""
        self.assertEqual(get_status_label(70), "warning")
        self.assertEqual(get_status_label(80), "warning")
        self.assertEqual(get_status_label(89), "warning")

    def test_label_critical_for_critical(self):
        """Test that critical values return 'critical' label."""
        self.assertEqual(get_status_label(90), "critical")
        self.assertEqual(get_status_label(95), "critical")
        self.assertEqual(get_status_label(100), "critical")

    def test_label_unknown_for_invalid(self):
        """Test that invalid values return 'unknown' label."""
        self.assertEqual(get_status_label(None), "unknown")
        self.assertEqual(get_status_label(-1), "unknown")
        self.assertEqual(get_status_label(101), "unknown")
        self.assertEqual(get_status_label("invalid"), "unknown")

    def test_label_float_values(self):
        """Test that float values return correct labels."""
        self.assertEqual(get_status_label(45.5), "ok")
        self.assertEqual(get_status_label(75.5), "warning")
        self.assertEqual(get_status_label(95.5), "critical")


class TestFormatSystemMetric(unittest.TestCase):
    """Test cases for format_system_metric function."""

    def setUp(self):
        """Set up test fixtures."""
        set_theme("lime")

    def test_format_cpu_metric(self):
        """Test formatting CPU metrics."""
        result = format_system_metric("CPU", 45)
        self.assertEqual(result, "CPU: 45% (ok)")

        result = format_system_metric("CPU", 85)
        self.assertEqual(result, "CPU: 85% (warning)")

        result = format_system_metric("CPU", 95)
        self.assertEqual(result, "CPU: 95% (critical)")

    def test_format_memory_metric(self):
        """Test formatting memory metrics."""
        result = format_system_metric("Memory", 30)
        self.assertEqual(result, "Memory: 30% (ok)")

        result = format_system_metric("Memory", 75)
        self.assertEqual(result, "Memory: 75% (warning)")

        result = format_system_metric("Memory", 92)
        self.assertEqual(result, "Memory: 92% (critical)")

    def test_format_disk_metric(self):
        """Test formatting disk metrics."""
        result = format_system_metric("Disk", 50)
        self.assertEqual(result, "Disk: 50% (ok)")

        result = format_system_metric("Disk", 80)
        self.assertEqual(result, "Disk: 80% (warning)")

        result = format_system_metric("Disk", 98)
        self.assertEqual(result, "Disk: 98% (critical)")

    def test_format_none_metric(self):
        """Test formatting None value."""
        result = format_system_metric("Network", None)
        self.assertEqual(result, "Network: N/A (unknown)")

    def test_format_float_values(self):
        """Test formatting float values."""
        result = format_system_metric("CPU", 45.5)
        self.assertEqual(result, "CPU: 45.5% (ok)")

        result = format_system_metric("Memory", 75.8)
        self.assertEqual(result, "Memory: 75.8% (warning)")


class TestGetSysmonConfig(unittest.TestCase):
    """Test cases for get_sysmon_config function."""

    def test_defaults_for_none(self):
        """Test that None config returns all defaults."""

        cfg = get_sysmon_config(None)
        self.assertEqual(cfg["refresh_interval"], 10)
        self.assertTrue(cfg["show_cpu"])
        self.assertTrue(cfg["show_memory"])
        self.assertTrue(cfg["show_disk"])
        self.assertEqual(cfg["thresholds"]["low"], 70)
        self.assertEqual(cfg["thresholds"]["medium"], 90)
        self.assertEqual(cfg["thresholds"]["high"], 100)

    def test_defaults_for_empty_dict(self):
        """Test that empty dict config returns all defaults."""

        cfg = get_sysmon_config({})
        self.assertEqual(cfg["refresh_interval"], 10)
        self.assertTrue(cfg["show_cpu"])

    def test_partial_config_with_defaults(self):
        """Test that partial config uses defaults for missing keys."""

        cfg = get_sysmon_config({"refresh_interval": 5, "show_cpu": False})
        self.assertEqual(cfg["refresh_interval"], 5)
        self.assertFalse(cfg["show_cpu"])
        self.assertTrue(cfg["show_memory"])  # default
        self.assertTrue(cfg["show_disk"])  # default

    def test_full_config(self):
        """Test that full config is respected."""

        cfg = get_sysmon_config(
            {
                "refresh_interval": 3,
                "show_cpu": False,
                "show_memory": True,
                "show_disk": False,
                "thresholds": {"low": 60, "medium": 85, "high": 95},
            }
        )
        self.assertEqual(cfg["refresh_interval"], 3)
        self.assertFalse(cfg["show_cpu"])
        self.assertTrue(cfg["show_memory"])
        self.assertFalse(cfg["show_disk"])
        self.assertEqual(cfg["thresholds"]["low"], 60)
        self.assertEqual(cfg["thresholds"]["medium"], 85)
        self.assertEqual(cfg["thresholds"]["high"], 95)

    def test_partial_thresholds(self):
        """Test that partial thresholds use defaults for missing keys."""

        cfg = get_sysmon_config({"thresholds": {"low": 50, "medium": 75}})
        self.assertEqual(cfg["thresholds"]["low"], 50)
        self.assertEqual(cfg["thresholds"]["medium"], 75)
        self.assertEqual(cfg["thresholds"]["high"], 100)  # default

    def test_config_is_copy(self):
        """Test that returned config is a copy, not a reference."""

        cfg1 = get_sysmon_config(None)
        cfg1["refresh_interval"] = 999
        cfg2 = get_sysmon_config(None)
        self.assertEqual(cfg2["refresh_interval"], 10)  # Not affected by cfg1


class TestThemeIntegration(unittest.TestCase):
    """Test that threshold function works across different themes."""

    def test_works_with_ocean_theme(self):
        """Test that threshold function works with ocean theme."""
        set_theme("ocean")

        # Green should use ocean's success color
        result = color_threshold(50)
        expected = (40, 160, 140)  # Ocean success[0]
        self.assertEqual(result, expected)

        # Yellow should use ocean's warning color
        result = color_threshold(80)
        expected = (180, 160, 80)  # Ocean warning[0]
        self.assertEqual(result, expected)

        # Red should use ocean's error color
        result = color_threshold(95)
        expected = (160, 80, 100)  # Ocean error[0]
        self.assertEqual(result, expected)

    def test_works_with_sunset_theme(self):
        """Test that threshold function works with sunset theme."""
        set_theme("sunset")

        result = color_threshold(50)
        expected = (120, 180, 100)  # Sunset success[0]
        self.assertEqual(result, expected)

        result = color_threshold(80)
        expected = (240, 180, 80)  # Sunset warning[0]
        self.assertEqual(result, expected)

        result = color_threshold(95)
        expected = (200, 80, 80)  # Sunset error[0]
        self.assertEqual(result, expected)

    def test_works_with_mono_theme(self):
        """Test that threshold function works with mono theme."""
        set_theme("mono")

        result = color_threshold(50)
        expected = (140, 180, 140)  # Mono success[0]
        self.assertEqual(result, expected)

        result = color_threshold(80)
        expected = (180, 180, 140)  # Mono warning[0]
        self.assertEqual(result, expected)

        result = color_threshold(95)
        expected = (180, 140, 140)  # Mono error[0]
        self.assertEqual(result, expected)


if __name__ == "__main__":
    unittest.main()
