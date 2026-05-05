"""System Monitor utilities for status display.

Provides data collection, color threshold mapping, and widget rendering
for system metrics like CPU, memory, and disk usage.
"""

import logging
import time
from typing import Any, Dict, Optional, Tuple, Union

from kollabor_tui.design_system import T

from .utils import fg as _fg

logger = logging.getLogger(__name__)

# Optional psutil import
try:
    import psutil  # type: ignore[import-untyped]

    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    logger.debug("psutil not available, system monitor will return placeholder data")


def color_threshold(percentage: Union[int, float, None]) -> Tuple[int, int, int]:
    """Map a percentage value to a color based on threshold ranges.

    Thresholds:
        - <70%:    Green (success)  - healthy state
        - 70-90%:  Yellow (warning) - elevated usage
        - >90%:    Red (error)      - critical state

    Edge cases:
        - None, NaN, or non-numeric values: Dimmed text color
        - Negative values: Treated as invalid (dimmed)
        - Values >100: Treated as invalid (dimmed)

    Args:
        percentage: The metric percentage value (0-100).

    Returns:
        RGB tuple representing the appropriate color.
    """
    # Handle invalid/missing values
    if percentage is None:
        return tuple(T().text_dim)

    # Check if numeric
    try:
        value = float(percentage)
    except (ValueError, TypeError):
        return tuple(T().text_dim)

    # Validate range (percentages should be 0-100)
    if value < 0 or value > 100:
        return tuple(T().text_dim)

    # Check for NaN
    import math

    if math.isnan(value):
        return tuple(T().text_dim)

    # Apply threshold logic
    if value < 70:
        return tuple(T().success[0])
    elif value < 90:
        return tuple(T().warning[0])
    else:
        return tuple(T().error[0])


def get_status_label(percentage: Union[int, float, None]) -> str:
    """Get a text label for status based on percentage.

    Args:
        percentage: The metric percentage value (0-100).

    Returns:
        String label: "ok", "warning", "critical", or "unknown".
    """
    color = color_threshold(percentage)

    if color == T().text_dim:
        return "unknown"
    elif color == T().success[0]:
        return "ok"
    elif color == T().warning[0]:
        return "warning"
    elif color == T().error[0]:
        return "critical"
    else:
        return "unknown"


def format_system_metric(name: str, percentage: Union[int, float, None]) -> str:
    """Format a system metric with name, percentage, and status label.

    Args:
        name: Metric name (e.g., "CPU", "Memory", "Disk")
        percentage: The metric percentage value (0-100)

    Returns:
        Formatted string like "CPU: 45% (ok)" or "Network: N/A (unknown)"
    """
    status = get_status_label(percentage)
    if percentage is None:
        return f"{name}: N/A ({status})"
    return f"{name}: {percentage}% ({status})"


class SystemDataCollector:
    """Collects and caches system metrics with TTL support.

    Uses psutil to gather CPU, memory, and disk usage data with
    configurable time-to-live caching to avoid expensive system calls.
    """

    def __init__(self, ttl: float = 10.0):
        """Initialize data collector.

        Args:
            ttl: Time-to-live for cached data in seconds (default: 10).
        """
        self.ttl = ttl
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._available = PSUTIL_AVAILABLE

    def _get_cached(self, key: str, fetch_fn) -> Any:
        """Get cached data or fetch fresh data if TTL expired.

        Args:
            key: Cache key for data
            fetch_fn: Function to fetch fresh data

        Returns:
            Cached or fresh data value
        """
        now = time.time()

        # Check if we have cached data within TTL
        if key in self._cache:
            timestamp, value = self._cache[key]
            if now - timestamp < self.ttl:
                return value

        # Fetch fresh data
        try:
            value = fetch_fn()
            self._cache[key] = (now, value)
            return value
        except Exception as e:
            logger.debug(f"Error fetching {key}: {e}")
            # Return cached value if available, even if expired
            if key in self._cache:
                return self._cache[key][1]
            return None

    def get_cpu_usage(self) -> Optional[float]:
        """Get CPU usage percentage.

        Returns:
            CPU usage as percentage (0-100), or None if unavailable.
        """
        if not self._available:
            return None

        def fetch() -> float:
            return psutil.cpu_percent(interval=None)  # type: ignore[no-any-return]

        result = self._get_cached("cpu", fetch)
        if result is None:
            return None
        return float(result)  # type: ignore[no-any-return]

    def get_memory_usage(self) -> Optional[float]:
        """Get memory usage percentage.

        Returns:
            Memory usage as percentage (0-100), or None if unavailable.
        """
        if not self._available:
            return None

        def fetch() -> float:
            mem = psutil.virtual_memory()
            return mem.percent  # type: ignore[no-any-return]

        result = self._get_cached("memory", fetch)
        if result is None:
            return None
        return float(result)  # type: ignore[no-any-return]

    def get_disk_usage(self) -> Optional[float]:
        """Get disk usage percentage for current working directory.

        Returns:
            Disk usage as percentage (0-100), or None if unavailable.
        """
        if not self._available:
            return None

        def fetch() -> float:
            disk = psutil.disk_usage("/")
            return disk.percent  # type: ignore[no-any-return]

        result = self._get_cached("disk", fetch)
        if result is None:
            return None
        return float(result)  # type: ignore[no-any-return]

    def get_all_metrics(self) -> Dict[str, Optional[float]]:
        """Get all system metrics in one call.

        Returns:
            Dict with keys: 'cpu', 'memory', 'disk'
        """
        return {
            "cpu": self.get_cpu_usage(),
            "memory": self.get_memory_usage(),
            "disk": self.get_disk_usage(),
        }


# Global data collector instance
_system_collector = SystemDataCollector(ttl=10.0)


def get_system_collector() -> SystemDataCollector:
    """Get the global system data collector instance.

    Returns:
        SystemDataCollector instance
    """
    return _system_collector


def get_sysmon_config(widget_config: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Get system monitor configuration from widget config.

    Returns sensible defaults for missing configuration.

    Args:
        widget_config: Optional widget_config dict from context

    Returns:
        Configuration dict with keys:
            - refresh_interval: TTL for cached data (default: 10)
            - show_cpu: Show CPU metric (default: True)
            - show_memory: Show memory metric (default: True)
            - show_disk: Show disk metric (default: True)
            - thresholds: Dict with low, medium, high values
    """
    # Start with defaults
    config = {
        "refresh_interval": 10,
        "show_cpu": True,
        "show_memory": True,
        "show_disk": True,
        "thresholds": {"low": 70, "medium": 90, "high": 100},
    }

    # Merge user config, with special handling for thresholds
    if widget_config:
        config.update(widget_config)

        # If thresholds is partial, merge with defaults
        if "thresholds" in widget_config and isinstance(
            widget_config["thresholds"], dict
        ):
            default_thresholds = {"low": 70, "medium": 90, "high": 100}
            default_thresholds.update(widget_config["thresholds"])
            config["thresholds"] = default_thresholds

    return config


def render_sysmon(width: int, ctx: Optional[Any] = None) -> str:
    """Render system monitor widget with CPU, memory, and disk usage.

    Width-aware formats:
        - Full (30+ chars): "cpu:45% mem:60% disk:70%"
        - Compact (20+ chars): "cpu:45% mem:60%"
        - Minimal (15+ chars): "cpu:45%"
        - Ultra-compact (<15 chars): progress bars only

    Color thresholds:
        - <70%: Green (healthy)
        - 70-90%: Yellow (elevated)
        - >90%: Red (critical)

    Args:
        width: Available width in characters
        ctx: Optional WidgetContext with widget_config

    Returns:
        Rendered widget string with ANSI color codes
    """
    try:
        # Get configuration
        widget_config = None
        if ctx and hasattr(ctx, "widget_config") and ctx.widget_config:
            widget_config = ctx.widget_config

        config = get_sysmon_config(widget_config)

        # Get metrics from collector
        collector = get_system_collector()
        metrics = collector.get_all_metrics()

        # Build metric strings with colors
        parts = []

        # CPU
        if config.get("show_cpu", True):
            cpu = metrics.get("cpu")
            cpu_color = color_threshold(cpu)
            cpu_val = int(cpu) if cpu is not None else 0
            parts.append(("cpu", cpu_val, cpu_color))

        # Memory
        if config.get("show_memory", True):
            mem = metrics.get("memory")
            mem_color = color_threshold(mem)
            mem_val = int(mem) if mem is not None else 0
            parts.append(("mem", mem_val, mem_color))

        # Disk
        if config.get("show_disk", True):
            disk = metrics.get("disk")
            disk_color = color_threshold(disk)
            disk_val = int(disk) if disk is not None else 0
            parts.append(("disk", disk_val, disk_color))

        if not parts:
            return _fg("sys:?", T().text_dim)

        # Width-aware rendering
        if width >= 30:
            # Full format: "cpu:45% mem:60% disk:70%"
            formatted = []
            for name, value, color in parts:
                formatted.append(_fg(f"{name}:{value}%", color))
            return " ".join(formatted)

        elif width >= 20:
            # Compact: show first 2 metrics
            formatted = []
            for name, value, color in parts[:2]:
                formatted.append(_fg(f"{name}:{value}%", color))
            return " ".join(formatted)

        elif width >= 15:
            # Minimal: show first metric only
            name, value, color = parts[0]
            return _fg(f"{name}:{value}%", color)

        else:
            # Ultra-compact: progress bars only
            # Use block characters: ▮ for filled, ░ for empty
            bars = []
            for name, value, color in parts:
                # Calculate bar width (max 8 chars per metric)
                bar_width = min(8, max(2, width // len(parts)))
                filled = int(bar_width * value / 100)
                empty = bar_width - filled
                bar = _fg("▮" * filled, color) + _fg("░" * empty, T().text_dim)
                bars.append(bar)
            return " ".join(bars)

    except Exception as e:
        logger.error(f"sysmon widget error: {e}")
        return _fg("sys:err", T().error[0])
