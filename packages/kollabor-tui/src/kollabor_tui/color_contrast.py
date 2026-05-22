"""Small color helpers for terminal foreground readability."""

from __future__ import annotations

RGB = tuple[int, int, int]


def _channel_luminance(value: int) -> float:
    scaled = value / 255
    return scaled / 12.92 if scaled <= 0.04045 else ((scaled + 0.055) / 1.055) ** 2.4


def relative_luminance(color: RGB) -> float:
    """Return WCAG relative luminance for an RGB color."""
    r, g, b = (_channel_luminance(value) for value in color)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg: RGB, bg: RGB) -> float:
    """Return WCAG contrast ratio between foreground and background."""
    light = max(relative_luminance(fg), relative_luminance(bg))
    dark = min(relative_luminance(fg), relative_luminance(bg))
    return (light + 0.05) / (dark + 0.05)


def blend_color(first: RGB, second: RGB, second_weight: float) -> RGB:
    """Blend first toward second by second_weight."""
    first_weight = 1 - second_weight
    return tuple(
        max(0, min(255, round(first[i] * first_weight + second[i] * second_weight)))
        for i in range(3)
    )


def coerce_rgb(color: object, fallback: RGB) -> RGB:
    """Return a safe RGB triple from a loose renderer color input."""
    if not isinstance(color, (tuple, list)) or len(color) < 3:
        return fallback
    try:
        return tuple(max(0, min(255, int(color[i]))) for i in range(3))
    except (TypeError, ValueError):
        return fallback


def readable_agent_color(
    color: object,
    *,
    background: RGB,
    target: RGB,
    muted_target: RGB,
    observing: bool = False,
) -> RGB:
    """Keep an agent color recognizable while meeting terminal contrast."""
    adjusted = coerce_rgb(color, muted_target)
    dark_background = relative_luminance(background) < 0.18
    if dark_background:
        minimum = 4.5 if observing else 6.5
    else:
        minimum = 3.5 if observing else 4.5

    if observing:
        adjusted = blend_color(adjusted, muted_target, 0.45)

    for _ in range(8):
        if contrast_ratio(adjusted, background) >= minimum:
            return adjusted
        adjusted = blend_color(adjusted, target, 0.28)

    return adjusted
