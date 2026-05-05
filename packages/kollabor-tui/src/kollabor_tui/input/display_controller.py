"""Display controller component for Kollab.

Responsible for coordinating terminal display updates during input handling.
This is a thin wrapper that manages rendering state and delegates to the terminal renderer.
"""

import asyncio
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


class DisplayController:
    """Controls display updates during input handling.

    This component manages:
    - Display updates from buffer changes
    - Rendering pause/resume for special effects (Matrix, etc.)
    - Cursor position tracking

    Attributes:
        renderer: Terminal renderer for actual rendering.
        buffer_manager: Buffer manager for getting display content.
        error_handler: Error handler for display errors.
    """

    def __init__(
        self,
        renderer: Any,
        buffer_manager: Any,
        error_handler: Optional[Any] = None,
    ) -> None:
        """Initialize the display controller.

        Args:
            renderer: Terminal renderer instance.
            buffer_manager: Buffer manager for display content.
            error_handler: Optional error handler for display errors.
        """
        self.renderer = renderer
        self.buffer_manager = buffer_manager
        self.error_handler = error_handler

        # Rendering state
        self.rendering_paused = False
        self._last_cursor_pos = 0

        # Reference to event-driven render loop (set after startup)
        self._render_loop = None

        # AltView integration -- lazy lookup via event bus
        self._event_bus = None
        self._altview_stack_manager = None

        logger.debug("DisplayController initialized")

    def set_render_loop(self, render_loop: Any) -> None:
        """Set the event-driven render loop reference.

        Args:
            render_loop: EventDrivenRenderLoop instance.
        """
        self._render_loop = render_loop

    def set_event_bus(self, event_bus: Any) -> None:
        """Set the event bus for lazy service lookups (e.g. altview stack manager).

        Args:
            event_bus: EventBus instance with register_service/get_service.
        """
        self._event_bus = event_bus

    def set_altview_stack_manager(self, manager: Any) -> None:
        """Directly set the altview stack manager reference.

        Prefer set_event_bus for lazy lookup. Use this only when you
        already have the manager instance and want to skip the service
        lookup.

        Args:
            manager: AltViewStackManager instance.
        """
        self._altview_stack_manager = manager

    def _get_altview_stack_manager(self) -> Optional[Any]:
        """Resolve the altview stack manager via direct ref or event bus lookup.

        Returns:
            AltViewStackManager or None if not available.
        """
        if self._altview_stack_manager is not None:
            return self._altview_stack_manager

        if self._event_bus is not None:
            mgr = self._event_bus.get_service("altview_stack_manager")
            if mgr is not None:
                self._altview_stack_manager = mgr
            return mgr

        return None

    async def update_display(self, force_render: bool = False) -> None:
        """Update the terminal display with current buffer state.

        Args:
            force_render: If True, force immediate rendering even if paused.
        """
        try:
            # Skip rendering if paused (during special effects like Matrix)
            if self.rendering_paused and not force_render:
                logger.debug("update_display: skipped (rendering paused)")
                return

            buffer_content, cursor_pos = self.buffer_manager.get_display_info()

            # Update renderer with buffer content and cursor position
            self.renderer.input_buffer = buffer_content
            self.renderer.cursor_position = cursor_pos

            # Wake shimmer animation on input activity
            if hasattr(self.renderer, "wake_shimmer"):
                self.renderer.wake_shimmer()

            # Input needs immediate rendering for responsiveness
            # Render directly, don't go through event-driven loop
            if not self.rendering_paused or force_render:
                await self._render_directly()

            # Only update cursor if position changed
            if cursor_pos != self._last_cursor_pos:
                # Could implement cursor positioning in renderer
                self._last_cursor_pos = cursor_pos

        except Exception as e:
            if self.error_handler:
                from kollabor_tui.input_errors import ErrorSeverity, ErrorType

                await self.error_handler.handle_error(
                    ErrorType.SYSTEM_ERROR,
                    f"Error updating display: {e}",
                    ErrorSeverity.LOW,
                    {"buffer_manager": self.buffer_manager},
                )
            else:
                logger.error(f"Error updating display: {e}")

    async def _render_directly(self) -> None:
        """Request render from event-driven loop or render directly as fallback."""
        try:
            # Gate: skip rendering when an AltView session is active.
            # The altview owns the terminal; normal input rendering would
            # corrupt its output. Frame capture happens via the altview's
            # own render path through the stack manager.
            altview_mgr = self._get_altview_stack_manager()
            if altview_mgr is not None and altview_mgr.is_in_altview:
                queue = altview_mgr.active_display_queue
                if queue is not None and queue.is_capturing:
                    return

            # Try to use event-driven render loop (for animations like shimmer)
            if self._render_loop:
                self._render_loop.request_render()
                return

            # Fallback to direct rendering (for tests or when render loop not available)
            if hasattr(
                self.renderer, "render_active_area"
            ) and asyncio.iscoroutinefunction(self.renderer.render_active_area):
                await self.renderer.render_active_area()
            elif hasattr(self.renderer, "render_input") and asyncio.iscoroutinefunction(
                self.renderer.render_input
            ):
                await self.renderer.render_input()
            elif hasattr(self.renderer, "render_active_area"):
                self.renderer.render_active_area()
            elif hasattr(self.renderer, "render_input"):
                self.renderer.render_input()
        except Exception as e:
            logger.error(f"Direct render failed: {e}")
            # Continue without rendering

    def pause_rendering(self) -> None:
        """Pause all UI rendering for special effects."""
        self.rendering_paused = True
        logger.debug("Input rendering paused")

    def resume_rendering(self) -> None:
        """Resume normal UI rendering."""
        self.rendering_paused = False
        logger.debug("Input rendering resumed")

    @property
    def last_cursor_pos(self) -> int:
        """Get the last cursor position."""
        return self._last_cursor_pos

    @last_cursor_pos.setter
    def last_cursor_pos(self, value: int) -> None:
        """Set the last cursor position."""
        self._last_cursor_pos = value
