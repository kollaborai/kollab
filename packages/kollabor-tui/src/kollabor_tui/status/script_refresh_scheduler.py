"""Refresh scheduler for script-based status widgets.

Handles multiple refresh strategies:
- Time-based polling (5s, 1m, 30s formats)
- Hook-based refresh (on event triggers)
- Once (run at startup only)
- Manual (user-triggered)
- Smart (time + hooks combined)
"""

import asyncio
import inspect
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set

from kollabor_events.models import Event, EventType, Hook

logger = logging.getLogger(__name__)


class RefreshMode(Enum):
    """Refresh strategy for widget updates."""

    MANUAL = "manual"  # Only on explicit trigger or event
    TIME = "time"  # Time-based polling (5s, 1m, 30s)
    HOOK = "hook"  # On specific events only
    ONCE = "once"  # Run once at startup
    SMART = "smart"  # Combined time + hooks


@dataclass
class WidgetSchedule:
    """Scheduling configuration for a widget.

    Attributes:
        widget_id: Unique widget identifier
        refresh_mode: How the widget refreshes
        interval_seconds: Time-based interval (for TIME/SMART modes)
        hooks: Event hooks that trigger refresh (for HOOK/SMART modes)
        refresh_callback: Async function to call for refresh
        next_refresh: When next time-based refresh is due
        has_run_once: Whether widget has run (for ONCE mode)
        last_refresh: When widget last refreshed
        enabled: Whether scheduling is active
    """

    widget_id: str
    refresh_mode: RefreshMode
    refresh_callback: Callable[[], Any]
    interval_seconds: Optional[float] = None
    hooks: List[EventType] = field(default_factory=list)
    next_refresh: Optional[datetime] = None
    has_run_once: bool = False
    last_refresh: Optional[datetime] = None
    enabled: bool = True

    def should_refresh_time(self) -> bool:
        """Check if time-based refresh is due."""
        if self.refresh_mode not in (RefreshMode.TIME, RefreshMode.SMART):
            return False
        if self.next_refresh is None:
            return False
        return datetime.now() >= self.next_refresh

    def should_refresh_hook(self, event_type: EventType) -> bool:
        """Check if hook-based refresh should trigger."""
        if self.refresh_mode not in (RefreshMode.HOOK, RefreshMode.SMART):
            return False
        return event_type in self.hooks

    def should_refresh_once(self) -> bool:
        """Check if ONCE mode widget should run."""
        if self.refresh_mode != RefreshMode.ONCE:
            return False
        return not self.has_run_once

    def update_next_refresh(self) -> None:
        """Update next refresh time for time-based modes."""
        if (
            self.refresh_mode in (RefreshMode.TIME, RefreshMode.SMART)
            and self.interval_seconds
        ):
            self.next_refresh = datetime.now() + timedelta(
                seconds=self.interval_seconds
            )
        else:
            self.next_refresh = None


class ScriptWidgetRefreshScheduler:
    """Scheduler for script-based widget refresh.

    Manages time-based polling, event-driven refresh, and combined strategies.
    Registers as event listener with the event bus for hook-based refresh.

    Usage:
        scheduler = ScriptWidgetRefreshScheduler(event_bus)
        await scheduler.start()

        # Schedule a widget
        await scheduler.schedule_widget(
            widget_id="git-branch",
            refresh_mode=RefreshMode.SMART,
            interval_seconds=30,
            hooks=[EventType.USER_INPUT_POST],
            refresh_callback=widget.refresh
        )

        # Trigger manual refresh
        await scheduler.trigger_refresh("git-branch")
    """

    # Parse time interval strings like "5s", "1m", "30s"
    TIME_PATTERN = re.compile(r"^(\d+(?:\.\d+)?)(s|m|h)$")

    def __init__(self, event_bus):
        """Initialize the refresh scheduler.

        Args:
            event_bus: EventBus instance for event registration
        """
        self.event_bus = event_bus
        self._schedules: Dict[str, WidgetSchedule] = {}
        self._lock = asyncio.Lock()
        self._running = False
        self._paused = False
        self._task: Optional[asyncio.Task] = None
        self._event_hooks: Set[str] = set()

        # Reference to render loop for triggering renders on widget refresh
        self._render_loop = None

        logger.info("ScriptWidgetRefreshScheduler initialized")

    def set_render_loop(self, render_loop) -> None:
        """Set the render loop for triggering renders on widget refresh.

        Args:
            render_loop: EventDrivenRenderLoop instance
        """
        self._render_loop = render_loop
        logger.debug("Render loop set for widget refresh scheduler")

    def pause(self) -> None:
        """Pause all refresh scheduling.

        Used when entering alternate buffer (altview) to prevent
        queued render requests that burst on thaw.
        """
        self._paused = True
        logger.debug("ScriptWidgetRefreshScheduler paused")

    def resume(self) -> None:
        """Resume refresh scheduling after pause.

        Called when exiting alternate buffer to resume normal operation.
        """
        self._paused = False
        logger.debug("ScriptWidgetRefreshScheduler resumed")

    async def start(self) -> None:
        """Start the scheduler event loop.

        Begins the background task that processes time-based refreshes.
        """
        if self._running:
            logger.warning("Scheduler already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._scheduler_loop())
        logger.info("ScriptWidgetRefreshScheduler started")

    async def stop(self) -> None:
        """Stop the scheduler and clean up resources.

        Unregisters event hooks and cancels the background task.
        """
        self._running = False

        # Cancel scheduler loop
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                logger.debug("Scheduler task cancelled")
            self._task = None

        # Unregister all event hooks
        for hook_name in self._event_hooks:
            await self.event_bus.unregister_hook("script_widgets", hook_name)
        self._event_hooks.clear()

        logger.info("ScriptWidgetRefreshScheduler stopped")

    async def schedule_widget(
        self,
        widget_id: str,
        refresh_callback: Callable[[], Any],
        refresh_mode: str = "manual",
        interval: Optional[str] = None,
        hooks: Optional[List[str]] = None,
    ) -> bool:
        """Schedule a widget for refresh.

        Args:
            widget_id: Unique widget identifier
            refresh_callback: Async function to call for refresh
            refresh_mode: Refresh mode ("manual", "time", "hook", "once", "smart", or interval like "5s")
            interval: Time interval (e.g., "5s", "1m", "30s")
            hooks: List of event type names (e.g., ["post_user_input"])

        Returns:
            True if scheduled successfully, False otherwise

        Examples:
            # Manual refresh only
            await scheduler.schedule_widget("mywidget", widget.refresh, "manual")

            # Time-based refresh every 30 seconds
            await scheduler.schedule_widget("mywidget", widget.refresh, "time", "30s")

            # Hook-based refresh on user input
            await scheduler.schedule_widget("mywidget", widget.refresh, "hook", hooks=["post_user_input"])

            # Smart: time + hooks combined
            await scheduler.schedule_widget("mywidget", widget.refresh, "smart", "30s", ["pre_api_request"])

            # Once at startup
            await scheduler.schedule_widget("mywidget", widget.refresh, "once")

            # Shorthand: interval as mode
            await scheduler.schedule_widget("mywidget", widget.refresh, "5s")
        """
        async with self._lock:
            # Parse refresh mode and interval
            mode, interval_seconds = self._parse_refresh_config(refresh_mode, interval)

            # Parse hook event types
            event_types = self._parse_hook_types(hooks or [])

            # Check if need to register event hooks
            if mode in (RefreshMode.HOOK, RefreshMode.SMART):
                await self._register_event_hooks(event_types)

            # Create schedule
            schedule = WidgetSchedule(
                widget_id=widget_id,
                refresh_mode=mode,
                refresh_callback=refresh_callback,
                interval_seconds=interval_seconds,
                hooks=event_types,
            )

            # Set initial next refresh for time-based modes
            if mode in (RefreshMode.TIME, RefreshMode.SMART) and interval_seconds:
                schedule.next_refresh = datetime.now() + timedelta(
                    seconds=interval_seconds
                )

            self._schedules[widget_id] = schedule
            logger.info(
                f"Scheduled widget '{widget_id}': mode={mode.value}, "
                f"interval={interval_seconds}s, hooks={[h.value for h in event_types]}"
            )
            return True

    async def unschedule_widget(self, widget_id: str) -> bool:
        """Remove a widget from scheduling.

        Args:
            widget_id: Widget identifier to unschedule

        Returns:
            True if widget was unscheduled, False if not found
        """
        async with self._lock:
            if widget_id not in self._schedules:
                logger.warning(f"Widget '{widget_id}' not found in schedule")
                return False

            del self._schedules[widget_id]
            logger.info(f"Unscheduled widget '{widget_id}'")
            return True

    async def trigger_refresh(self, widget_id: str) -> bool:
        """Manually trigger a widget refresh.

        Args:
            widget_id: Widget identifier to refresh

        Returns:
            True if refresh triggered, False if widget not found
        """
        async with self._lock:
            if widget_id not in self._schedules:
                logger.warning(f"Widget '{widget_id}' not found in schedule")
                return False

            schedule = self._schedules[widget_id]
            if not schedule.enabled:
                logger.debug(f"Widget '{widget_id}' is disabled, skipping refresh")
                return False

            await self._execute_refresh(schedule)
            return True

    async def handle_event(self, event: Event) -> None:
        """Handle an event that may trigger widget refreshes.

        Called by event hooks when registered events occur.

        Args:
            event: The event that occurred
        """
        async with self._lock:
            for widget_id, schedule in self._schedules.items():
                if not schedule.enabled:
                    continue

                # Check if this event should trigger refresh
                if isinstance(event.type, EventType) and schedule.should_refresh_hook(
                    event.type
                ):
                    logger.debug(
                        f"Event {event.type.value} triggered refresh for '{widget_id}'"
                    )
                    await self._execute_refresh(schedule)

    async def _scheduler_loop(self) -> None:
        """Main scheduler loop for time-based refresh.

        Runs continuously while scheduler is active, checking for
        widgets that need time-based refresh.
        """
        logger.debug("Scheduler loop started")

        while self._running:
            try:
                # Skip all refresh work while paused (altview active)
                if self._paused:
                    await asyncio.sleep(0.1)
                    continue

                # Check each scheduled widget
                for widget_id, schedule in list(self._schedules.items()):
                    if not schedule.enabled:
                        continue

                    # Time-based refresh
                    if schedule.should_refresh_time():
                        logger.debug(f"Time-based refresh triggered for '{widget_id}'")
                        await self._execute_refresh(schedule)

                    # Once mode refresh
                    elif schedule.should_refresh_once():
                        logger.debug(f"Once-mode refresh for '{widget_id}'")
                        await self._execute_refresh(schedule)

                # Sleep for a short interval before next check
                await asyncio.sleep(0.1)

            except asyncio.CancelledError:
                logger.debug("Scheduler loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in scheduler loop: {e}", exc_info=True)
                await asyncio.sleep(1)

        logger.debug("Scheduler loop stopped")

    async def _execute_refresh(self, schedule: WidgetSchedule) -> None:
        """Execute the refresh callback for a widget.

        Args:
            schedule: Widget schedule to refresh
        """
        widget_id = schedule.widget_id

        try:
            # Execute refresh callback
            result = schedule.refresh_callback()
            if inspect.isawaitable(result):
                result = await result

            # Update schedule state
            schedule.last_refresh = datetime.now()
            schedule.has_run_once = True
            schedule.update_next_refresh()

            logger.debug(f"Refreshed widget '{widget_id}' successfully")

            # Trigger render to update UI with new widget content
            if self._render_loop and hasattr(self._render_loop, "request_render"):
                self._render_loop.request_render()

        except Exception as e:
            logger.error(f"Error refreshing widget '{widget_id}': {e}", exc_info=True)

    def _parse_refresh_config(
        self,
        refresh_mode: str,
        interval: Optional[str],
    ) -> tuple[RefreshMode, Optional[float]]:
        """Parse refresh mode and interval from config.

        Args:
            refresh_mode: Mode string or interval string
            interval: Optional interval string

        Returns:
            Tuple of (RefreshMode, interval_seconds)
        """
        mode = refresh_mode.lower()
        interval_seconds: Optional[float] = None

        # Check if mode is actually an interval (e.g., "5s", "1m")
        time_match = self.TIME_PATTERN.match(mode)
        if time_match:
            interval_seconds = self._parse_interval(mode)
            return RefreshMode.TIME, interval_seconds

        # Parse explicit modes
        if mode == "manual":
            return RefreshMode.MANUAL, None
        elif mode == "time":
            if interval:
                interval_seconds = self._parse_interval(interval)
            return RefreshMode.TIME, interval_seconds
        elif mode == "hook":
            return RefreshMode.HOOK, None
        elif mode == "once":
            return RefreshMode.ONCE, None
        elif mode == "smart":
            if interval:
                interval_seconds = self._parse_interval(interval)
            return RefreshMode.SMART, interval_seconds
        else:
            logger.warning(
                f"Unknown refresh mode '{refresh_mode}', defaulting to manual"
            )
            return RefreshMode.MANUAL, None

    def _parse_interval(self, interval: str) -> Optional[float]:
        """Parse time interval string to seconds.

        Args:
            interval: Interval string like "5s", "1m", "30s", "1h"

        Returns:
            Interval in seconds, or None if invalid

        Examples:
            "5s" -> 5.0
            "1m" -> 60.0
            "30s" -> 30.0
            "1.5m" -> 90.0
            "2h" -> 7200.0
        """
        match = self.TIME_PATTERN.match(interval.strip().lower())
        if not match:
            logger.error(f"Invalid interval format: {interval}")
            return None

        value = float(match.group(1))
        unit = match.group(2)

        if unit == "s":
            return value
        elif unit == "m":
            return value * 60
        elif unit == "h":
            return value * 3600
        else:
            logger.error(f"Unknown interval unit: {unit}")
            return None

    def _parse_hook_types(self, hook_names: List[str]) -> List[EventType]:
        """Parse hook type strings to EventType enums.

        Args:
            hook_names: List of event type names

        Returns:
            List of EventType enums

        Examples:
            ["post_user_input"] -> [EventType.USER_INPUT_POST]
            ["pre_api_request"] -> [EventType.LLM_REQUEST_PRE]
        """
        event_types = []

        # Map common names to EventType enum values
        name_mapping = {
            "post_user_input": EventType.USER_INPUT_POST,
            "pre_user_input": EventType.USER_INPUT_PRE,
            "pre_api_request": EventType.LLM_REQUEST_PRE,
            "post_api_request": EventType.LLM_REQUEST_POST,
            "pre_api_response": EventType.LLM_RESPONSE_PRE,
            "post_api_response": EventType.LLM_RESPONSE_POST,
            "tool_call": EventType.TOOL_CALL,
            "pre_tool_call": EventType.TOOL_CALL_PRE,
            "post_tool_call": EventType.TOOL_CALL_POST,
        }

        for name in hook_names:
            normalized = name.lower().replace("-", "_")

            # Try direct mapping first
            if normalized in name_mapping:
                event_types.append(name_mapping[normalized])
                continue

            # Try EventType enum
            try:
                event_types.append(EventType[normalized.upper()])
            except KeyError:
                logger.warning(f"Unknown event type: {name}")

        return event_types

    async def _register_event_hooks(self, event_types: List[EventType]) -> None:
        """Register event hooks for the given event types.

        Args:
            event_types: List of EventType enums to register
        """
        for event_type in event_types:
            hook_name = f"script_refresh_{event_type.value}"

            # Skip if already registered
            if hook_name in self._event_hooks:
                continue

            # Create event handler
            async def event_handler(event: Event, et=event_type):
                await self.handle_event(event)

            # Register hook
            hook = Hook(
                name=hook_name,
                plugin_name="script_widgets",
                event_type=event_type,
                priority=50,  # Normal priority
                callback=event_handler,
            )

            success = await self.event_bus.register_hook(hook)
            if success:
                self._event_hooks.add(hook_name)
                logger.debug(f"Registered event hook: {hook_name}")

    def get_schedule_status(self) -> Dict[str, Dict[str, Any]]:
        """Get status of all scheduled widgets.

        Returns:
            Dictionary mapping widget_id to schedule status
        """
        status = {}

        for widget_id, schedule in self._schedules.items():
            status[widget_id] = {
                "mode": schedule.refresh_mode.value,
                "interval_seconds": schedule.interval_seconds,
                "hooks": [h.value for h in schedule.hooks],
                "enabled": schedule.enabled,
                "last_refresh": (
                    schedule.last_refresh.isoformat() if schedule.last_refresh else None
                ),
                "next_refresh": (
                    schedule.next_refresh.isoformat() if schedule.next_refresh else None
                ),
                "has_run_once": schedule.has_run_once,
            }

        return status
