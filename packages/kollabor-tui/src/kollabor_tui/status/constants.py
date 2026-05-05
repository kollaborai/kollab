"""Constants for status widget system.

This module provides centralized constants for widget colors,
configurations, and other shared values used across the status
widget system.
"""

# Widget background color names for 'c' key toggle cycle
# Order: none -> dark0 -> dark1 -> primary0 -> secondary0 -> (cycle back to none)
# These names are persisted in config and mapped to RGB tuples in layout_renderer.py
WIDGET_BG_COLOR_NAMES = [
    "none",
    "dark0",
    "dark1",
    "primary0",
    "secondary0",
]

# Widget effect names for 'x' key toggle cycle
# Order: none -> ultra -> shimmer -> pulse -> (cycle back to none)
# These names are persisted in config and applied during rendering
WIDGET_EFFECT_NAMES = [
    "none",
    "ultra",
    "shimmer",
    "pulse",
]
