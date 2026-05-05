"""Animation framework for full-screen plugins."""

import math
from dataclasses import dataclass
from typing import Any, Callable


@dataclass
class AnimationState:
    """State for an animation."""

    start_value: float
    end_value: float
    duration: float
    start_time: float
    current_time: float = 0.0
    completed: bool = False

    @property
    def progress(self) -> float:
        """Get animation progress (0.0 to 1.0)."""
        if self.duration <= 0:
            return 1.0
        return min(1.0, (self.current_time - self.start_time) / self.duration)

    @property
    def is_complete(self) -> bool:
        """Check if animation is complete."""
        return self.progress >= 1.0 or self.completed


class EasingFunctions:
    """Common easing functions for animations."""

    @staticmethod
    def linear(t: float) -> float:
        """Linear interpolation."""
        return t

    @staticmethod
    def ease_in_quad(t: float) -> float:
        """Quadratic ease-in."""
        return t * t

    @staticmethod
    def ease_out_quad(t: float) -> float:
        """Quadratic ease-out."""
        return 1 - (1 - t) * (1 - t)

    @staticmethod
    def ease_in_out_quad(t: float) -> float:
        """Quadratic ease-in-out."""
        if t < 0.5:
            return 2 * t * t
        return 1 - pow(-2 * t + 2, 2) / 2

    @staticmethod
    def ease_in_cubic(t: float) -> float:
        """Cubic ease-in."""
        return t * t * t

    @staticmethod
    def ease_out_cubic(t: float) -> float:
        """Cubic ease-out."""
        return 1 - pow(1 - t, 3)

    @staticmethod
    def ease_in_out_cubic(t: float) -> float:
        """Cubic ease-in-out."""
        if t < 0.5:
            return 4 * t * t * t
        return 1 - pow(-2 * t + 2, 3) / 2

    @staticmethod
    def bounce_out(t: float) -> float:
        """Bounce ease-out."""
        n1 = 7.5625
        d1 = 2.75
        if t < 1 / d1:
            return n1 * t * t
        elif t < 2 / d1:
            t -= 1.5 / d1
            return n1 * t * t + 0.75
        elif t < 2.5 / d1:
            t -= 2.25 / d1
            return n1 * t * t + 0.9375
        else:
            t -= 2.625 / d1
            return n1 * t * t + 0.984375

    @staticmethod
    def elastic_out(t: float) -> float:
        """Elastic ease-out."""
        if t == 0 or t == 1:
            return t
        c4 = (2 * math.pi) / 3
        return pow(2, -10 * t) * math.sin((t * 10 - 0.75) * c4) + 1


class AnimationFramework:
    """Framework for managing animations in full-screen plugins."""

    def __init__(self) -> None:
        """Initialize the animation framework."""
        self.animations: dict[int, dict[str, Any]] = {}
        self.next_id: int = 0

    def animate(
        self,
        start_value: float,
        end_value: float,
        duration: float,
        start_time: float,
        easing: Callable[[float], float] = EasingFunctions.linear,
    ) -> int:
        """Create a new animation.

        Args:
            start_value: Starting value
            end_value: Ending value
            duration: Animation duration in seconds
            start_time: Animation start time
            easing: Easing function

        Returns:
            Animation ID
        """
        animation_id = self.next_id
        self.next_id += 1

        self.animations[animation_id] = {
            "state": AnimationState(start_value, end_value, duration, start_time),
            "easing": easing,
        }

        return animation_id

    def update(self, current_time: float):
        """Update all animations.

        Args:
            current_time: Current time
        """
        completed_animations = []

        for anim_id, animation in self.animations.items():
            state = animation["state"]
            state.current_time = current_time

            if state.is_complete:
                completed_animations.append(anim_id)

        # Remove completed animations
        for anim_id in completed_animations:
            del self.animations[anim_id]

    def get_value(self, animation_id: int) -> float:
        """Get current animated value.

        Args:
            animation_id: Animation ID

        Returns:
            Current animated value
        """
        if animation_id not in self.animations:
            return 0.0

        animation = self.animations[animation_id]
        state: AnimationState = animation["state"]
        easing: Callable[[float], float] = animation["easing"]

        if state.is_complete:
            return state.end_value

        # Apply easing to progress
        eased_progress = easing(state.progress)

        # Interpolate between start and end values
        return (
            state.start_value + (state.end_value - state.start_value) * eased_progress
        )

    def is_complete(self, animation_id: int) -> bool:
        """Check if animation is complete.

        Args:
            animation_id: Animation ID

        Returns:
            True if animation is complete or doesn't exist
        """
        if animation_id not in self.animations:
            return True

        return self.animations[animation_id]["state"].is_complete  # type: ignore[no-any-return]

    def stop_animation(self, animation_id: int):
        """Stop an animation.

        Args:
            animation_id: Animation ID to stop
        """
        if animation_id in self.animations:
            del self.animations[animation_id]

    def clear_all(self):
        """Clear all animations."""
        self.animations.clear()

    def get_active_count(self) -> int:
        """Get number of active animations.

        Returns:
            Number of active animations
        """
        return len(self.animations)

    # Utility methods for common animation patterns
    def fade_in(self, duration: float, start_time: float) -> int:
        """Create a fade-in animation (0 to 1).

        Args:
            duration: Fade duration
            start_time: Start time

        Returns:
            Animation ID
        """
        return self.animate(
            0.0, 1.0, duration, start_time, EasingFunctions.ease_out_quad
        )

    def fade_out(self, duration: float, start_time: float) -> int:
        """Create a fade-out animation (1 to 0).

        Args:
            duration: Fade duration
            start_time: Start time

        Returns:
            Animation ID
        """
        return self.animate(
            1.0, 0.0, duration, start_time, EasingFunctions.ease_in_quad
        )

    def slide_in(self, distance: float, duration: float, start_time: float) -> int:
        """Create a slide-in animation.

        Args:
            distance: Distance to slide
            duration: Animation duration
            start_time: Start time

        Returns:
            Animation ID
        """
        return self.animate(
            -distance, 0.0, duration, start_time, EasingFunctions.ease_out_cubic
        )

    def bounce_in(self, duration: float, start_time: float) -> int:
        """Create a bounce-in animation.

        Args:
            duration: Animation duration
            start_time: Start time

        Returns:
            Animation ID
        """
        return self.animate(0.0, 1.0, duration, start_time, EasingFunctions.bounce_out)
