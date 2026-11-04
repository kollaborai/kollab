"""Hook registration component for InputHandler.

Manages registration and lifecycle of event hooks for input handling,
modal triggers, rendering control, and command output display.
"""

import logging
from typing import Callable, List

from kollabor_events import EventType
from kollabor_events.models import Hook, HookPriority

logger = logging.getLogger(__name__)


class HookRegistrar:
    """Component responsible for registering and managing InputHandler event hooks.

    Centralizes hook registration logic for:
    - Command menu rendering
    - Modal triggers (standard, status, live)
    - Modal hiding/cleanup
    - Rendering pause/resume
    - Command output display

    The registrar accepts callback functions for each hook type, allowing
    the InputHandler to inject its handler methods while keeping registration
    logic separate and testable.
    """

    def __init__(
        self,
        event_bus,
        command_menu_render_handler: Callable,
        modal_trigger_handler: Callable,
        status_modal_trigger_handler: Callable,
        status_modal_render_handler: Callable,
        command_output_display_handler: Callable,
        pause_rendering_handler: Callable,
        resume_rendering_handler: Callable,
        modal_hide_handler: Callable,
    ):
        """Initialize the hook registrar with callback handlers.

        Args:
            event_bus: Event bus for hook registration.
            command_menu_render_handler: Handler for COMMAND_MENU_RENDER events.
            modal_trigger_handler: Handler for MODAL_TRIGGER events.
            status_modal_trigger_handler: Handler for STATUS_MODAL_TRIGGER events.
            status_modal_render_handler: Handler for STATUS_MODAL_RENDER events.
            command_output_display_handler: Handler for COMMAND_OUTPUT_DISPLAY events.
            pause_rendering_handler: Handler for PAUSE_RENDERING events.
            resume_rendering_handler: Handler for RESUME_RENDERING events.
            modal_hide_handler: Handler for MODAL_HIDE events.
        """
        self.event_bus = event_bus
        self._command_menu_render_handler = command_menu_render_handler
        self._modal_trigger_handler = modal_trigger_handler
        self._status_modal_trigger_handler = status_modal_trigger_handler
        self._status_modal_render_handler = status_modal_render_handler
        self._command_output_display_handler = command_output_display_handler
        self._pause_rendering_handler = pause_rendering_handler
        self._resume_rendering_handler = resume_rendering_handler
        self._modal_hide_handler = modal_hide_handler

        # Track registered hooks for cleanup
        self._registered_hooks: List[Hook] = []

    async def register_all_hooks(self) -> None:
        """Register all InputHandler hooks with the event bus."""
        logger.info("Registering all InputHandler hooks")

        await self._register_command_menu_render_hook()
        await self._register_modal_trigger_hook()
        await self._register_status_modal_trigger_hook()
        await self._register_status_modal_render_hook()
        await self._register_command_output_display_hook()
        await self._register_pause_rendering_hook()
        await self._register_resume_rendering_hook()
        await self._register_modal_hide_hook()

        logger.info(f"Successfully registered {len(self._registered_hooks)} hooks")

    async def unregister_all_hooks(self) -> None:
        """Unregister all hooks from the event bus."""
        logger.info(f"Unregistering {len(self._registered_hooks)} InputHandler hooks")

        for hook in self._registered_hooks:
            try:
                if self.event_bus:
                    await self.event_bus.unregister_hook(hook)
            except Exception as e:
                logger.error(f"Failed to unregister hook {hook.name}: {e}")

        self._registered_hooks.clear()
        logger.info("All InputHandler hooks unregistered")

    # ==================== HOOK REGISTRATION METHODS ====================

    async def _register_command_menu_render_hook(self) -> None:
        """Register hook to provide command menu content for COMMAND_MENU_RENDER events."""
        try:
            if self.event_bus:
                hook = Hook(
                    name="command_menu_render",
                    plugin_name="input_handler",
                    event_type=EventType.COMMAND_MENU_RENDER,
                    priority=HookPriority.DISPLAY.value,
                    callback=self._command_menu_render_handler,
                )
                success = await self.event_bus.register_hook(hook)
                if success:
                    self._registered_hooks.append(hook)
                    logger.info(
                        "Successfully registered COMMAND_MENU_RENDER hook for command menu display"
                    )
                else:
                    logger.error("Failed to register COMMAND_MENU_RENDER hook")
        except Exception as e:
            logger.error(f"Failed to register COMMAND_MENU_RENDER hook: {e}")

    async def _register_modal_trigger_hook(self) -> None:
        """Register hook to handle modal trigger events."""
        try:
            if self.event_bus:
                hook = Hook(
                    name="modal_trigger",
                    plugin_name="input_handler",
                    event_type=EventType.MODAL_TRIGGER,
                    priority=HookPriority.DISPLAY.value,
                    callback=self._modal_trigger_handler,
                )
                success = await self.event_bus.register_hook(hook)
                if success:
                    self._registered_hooks.append(hook)
                    logger.info("Successfully registered MODAL_TRIGGER hook")
                else:
                    logger.error("Failed to register MODAL_TRIGGER hook")
        except Exception as e:
            logger.error(f"Failed to register MODAL_TRIGGER hook: {e}")

    async def _register_status_modal_trigger_hook(self) -> None:
        """Register hook to handle status modal trigger events."""
        try:
            if self.event_bus:
                hook = Hook(
                    name="status_modal_trigger",
                    plugin_name="input_handler",
                    event_type=EventType.STATUS_MODAL_TRIGGER,
                    priority=HookPriority.DISPLAY.value,
                    callback=self._status_modal_trigger_handler,
                )
                success = await self.event_bus.register_hook(hook)
                if success:
                    self._registered_hooks.append(hook)
                    logger.info("Successfully registered STATUS_MODAL_TRIGGER hook")
                else:
                    logger.error("Failed to register STATUS_MODAL_TRIGGER hook")
        except Exception as e:
            logger.error(f"Failed to register STATUS_MODAL_TRIGGER hook: {e}")

    async def _register_status_modal_render_hook(self) -> None:
        """Register hook to handle status modal render events."""
        try:
            if self.event_bus:
                hook = Hook(
                    name="status_modal_render",
                    plugin_name="input_handler",
                    event_type=EventType.STATUS_MODAL_RENDER,
                    priority=HookPriority.DISPLAY.value,
                    callback=self._status_modal_render_handler,
                )
                success = await self.event_bus.register_hook(hook)
                if success:
                    self._registered_hooks.append(hook)
                    logger.info("Successfully registered STATUS_MODAL_RENDER hook")
                else:
                    logger.error("Failed to register STATUS_MODAL_RENDER hook")
        except Exception as e:
            logger.error(f"Failed to register STATUS_MODAL_RENDER hook: {e}")

    async def _register_command_output_display_hook(self) -> None:
        """Register hook to handle command output display events."""
        try:
            if self.event_bus:
                hook = Hook(
                    name="command_output_display",
                    plugin_name="input_handler",
                    event_type=EventType.COMMAND_OUTPUT_DISPLAY,
                    priority=HookPriority.DISPLAY.value,
                    callback=self._command_output_display_handler,
                )
                success = await self.event_bus.register_hook(hook)
                if success:
                    self._registered_hooks.append(hook)
                    logger.info("Successfully registered COMMAND_OUTPUT_DISPLAY hook")
                else:
                    logger.error("Failed to register COMMAND_OUTPUT_DISPLAY hook")
        except Exception as e:
            logger.error(f"Failed to register COMMAND_OUTPUT_DISPLAY hook: {e}")

    async def _register_pause_rendering_hook(self) -> None:
        """Register hook for pause rendering events."""
        try:
            if self.event_bus:
                hook = Hook(
                    name="pause_rendering",
                    plugin_name="input_handler",
                    event_type=EventType.PAUSE_RENDERING,
                    priority=HookPriority.DISPLAY.value,
                    callback=self._pause_rendering_handler,
                )
                success = await self.event_bus.register_hook(hook)
                if success:
                    self._registered_hooks.append(hook)
                    logger.info("Successfully registered PAUSE_RENDERING hook")
                else:
                    logger.error("Failed to register PAUSE_RENDERING hook")
        except Exception as e:
            logger.error(f"Error registering PAUSE_RENDERING hook: {e}")

    async def _register_resume_rendering_hook(self) -> None:
        """Register hook for resume rendering events."""
        try:
            if self.event_bus:
                hook = Hook(
                    name="resume_rendering",
                    plugin_name="input_handler",
                    event_type=EventType.RESUME_RENDERING,
                    priority=HookPriority.DISPLAY.value,
                    callback=self._resume_rendering_handler,
                )
                success = await self.event_bus.register_hook(hook)
                if success:
                    self._registered_hooks.append(hook)
                    logger.info("Successfully registered RESUME_RENDERING hook")
                else:
                    logger.error("Failed to register RESUME_RENDERING hook")
        except Exception as e:
            logger.error(f"Error registering RESUME_RENDERING hook: {e}")

    async def _register_modal_hide_hook(self) -> None:
        """Register hook for modal hide events."""
        try:
            if self.event_bus:
                hook = Hook(
                    name="modal_hide",
                    plugin_name="input_handler",
                    event_type=EventType.MODAL_HIDE,
                    priority=HookPriority.DISPLAY.value,
                    callback=self._modal_hide_handler,
                )
                success = await self.event_bus.register_hook(hook)
                if success:
                    self._registered_hooks.append(hook)
                    logger.info("Successfully registered MODAL_HIDE hook")
                else:
                    logger.error("Failed to register MODAL_HIDE hook")
        except Exception as e:
            logger.error(f"Error registering MODAL_HIDE hook: {e}")
