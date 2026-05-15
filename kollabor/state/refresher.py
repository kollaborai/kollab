"""WidgetStateRefresher: periodically pulls StateService snapshots into ctx.remote_state.

Status widgets in kollab are SYNC (render_cwd, render_stats, render_hub,
etc. all run on the render loop and can't await). They read daemon state from
``ctx.remote_state``, a plain dict on the widget context.

Before this module existed, ``ctx.remote_state`` was populated exclusively by
the hub plugin's periodic ``_publish_state_snapshot`` call over DisplayTap.
That path only works when the client is attached to a daemon, and it publishes
a bespoke flat dict that doesn't match StateService's snapshot shapes.

WidgetStateRefresher replaces (or augments) that path with one that:
  1. Pulls fresh snapshots from the injected StateService on a timer.
  2. Flattens them into the dict shape that widgets already read.
  3. Assigns the result to ``ctx.remote_state`` atomically.

Because StateService.get_* methods are async, the refresher runs as a
background coroutine. Widgets stay fully sync -- they just read a dict
that's kept up to date by the refresher.

Works in both local and attach mode without code changes in the widget.
The StateService resolution at app startup (LocalStateService vs
RemoteStateService) is the only mode-aware line.

The refresher never raises -- all exceptions are caught and logged at
debug level, with the old ``ctx.remote_state`` preserved on error. This
keeps the render loop running even if the daemon is momentarily
unreachable.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from .widget_state import WidgetState

if TYPE_CHECKING:
    from .interface import StateService

logger = logging.getLogger(__name__)


class WidgetStateRefresher:
    """Background coroutine that keeps ``ctx.remote_state`` fresh.

    Owns a reference to a widget context (any object with a ``remote_state``
    dict attribute) and a StateService. On each tick it fetches the
    snapshots widgets care about, flattens them, and assigns the result
    to ``ctx.remote_state``.

    Configured with a single interval (seconds between ticks). 2 seconds
    is a reasonable default -- slow enough that we don't hammer the
    daemon, fast enough that users don't notice widget staleness.

    Lifecycle:
        start()  -- schedules the background task
        stop()   -- cancels the task, waits for cleanup
        task     -- the underlying asyncio.Task, None before start()

    Multiple instances can coexist (e.g. one per widget context) though
    in practice there's one per kollab process.
    """

    DEFAULT_INTERVAL_SECONDS: float = 2.0

    def __init__(
        self,
        widget_context: Any,
        state_service: "StateService",
        *,
        interval_seconds: float | None = None,
        request_render: Any | None = None,
    ) -> None:
        self._ctx = widget_context
        self._state = state_service
        self._interval = (
            interval_seconds
            if interval_seconds is not None
            else self.DEFAULT_INTERVAL_SECONDS
        )
        self._request_render = request_render
        self._task: asyncio.Task | None = None
        self._stopped: bool = False

    @property
    def task(self) -> asyncio.Task | None:
        """The underlying asyncio task, or None before start()."""
        return self._task

    def start(self) -> None:
        """Schedule the background refresh coroutine on the running loop.

        Safe to call multiple times -- subsequent calls are no-ops if
        the task is already running.
        """
        if self._task is not None and not self._task.done():
            return
        self._stopped = False
        self._task = asyncio.create_task(self._loop(), name="widget_state_refresher")

    async def stop(self) -> None:
        """Cancel the background task and wait for it to exit cleanly.

        Safe to call after stop() or before start().
        """
        self._stopped = True
        task = self._task
        if task is None:
            return
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug("widget_state_refresher stop: %s", e)
        self._task = None

    async def refresh_once(self) -> None:
        """Pull one round of state and update ``ctx.remote_state``.

        Exposed for tests and for the first-call-on-startup path that
        wants a fresh snapshot before the render loop starts drawing.
        """
        try:
            flat = await self._gather_flat_state()
        except Exception as e:
            logger.debug("widget state refresh: gather failed: %s", e)
            return
        try:
            # Assign atomically -- widgets reading concurrently will see
            # either the old dict or the new dict, never a half-populated
            # mutation in progress.
            current_raw = getattr(self._ctx, "remote_state", {}) or {}
            current = WidgetState.from_flat_dict(current_raw, source="existing")
            updated = current.update_from(
                WidgetState.from_flat_dict(flat, source="state_service")
            )
            preserved = {
                key: value
                for key, value in current_raw.items()
                if key not in WidgetState.state_fields()
                and key not in {"type", "_source", "_updated_at", "_stale", "_degraded"}
            }
            self._ctx.remote_state = {**preserved, **updated.to_dict()}
            if self._request_render is not None:
                self._request_render()
        except Exception as e:
            logger.debug("widget state refresh: assign failed: %s", e)

    async def _loop(self) -> None:
        """Main refresh loop. Fetches every interval until stopped."""
        try:
            # Prime the first read immediately so widgets have fresh data
            # as soon as the render loop starts.
            await self.refresh_once()
            while not self._stopped:
                try:
                    await asyncio.sleep(self._interval)
                except asyncio.CancelledError:
                    raise
                if self._stopped:
                    break
                await self.refresh_once()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug("widget_state_refresher loop exited: %s", e)

    async def _gather_flat_state(self) -> dict[str, Any]:
        """Fetch snapshots from StateService and flatten into the dict shape
        widgets read from ``ctx.remote_state``.

        Each snapshot fetch is wrapped in its own try/except so one
        transient failure doesn't wipe out unrelated fields.
        """
        flat: dict[str, Any] = {}

        # --- session stats ---
        try:
            stats = await self._state.get_session_stats()
            flat["messages"] = stats.messages
            flat["input_tokens"] = stats.input_tokens
            flat["output_tokens"] = stats.output_tokens
            flat["total_input_tokens"] = stats.total_input_tokens
            flat["total_output_tokens"] = stats.total_output_tokens
            flat["cache_read_tokens"] = stats.cache_read_tokens
            flat["cost_usd"] = stats.cost_usd
            flat["total_cost_usd"] = stats.total_cost_usd
            flat["session"] = stats.session_id
        except Exception as e:
            logger.debug("refresher get_session_stats failed: %s", e)

        # --- processing state ---
        try:
            proc = await self._state.get_processing_state()
            flat["is_processing"] = proc.is_processing
            flat["bg_tasks"] = proc.bg_tasks_count
            flat["pending_tools"] = proc.pending_tools_count
        except Exception as e:
            logger.debug("refresher get_processing_state failed: %s", e)

        # --- system info (terminal sessions + daemon pid/uptime for debugging) ---
        try:
            sysinfo = await self._state.get_system_info()
            flat["tmux_sessions"] = sysinfo.tmux_sessions_count
            flat["cwd"] = sysinfo.cwd
            flat["git_branch"] = sysinfo.git_branch
            flat["daemon_pid"] = sysinfo.daemon_pid
            flat["daemon_uptime"] = sysinfo.daemon_uptime_seconds
        except Exception as e:
            logger.debug("refresher get_system_info failed: %s", e)

        # --- hub state ---
        try:
            hub = await self._state.get_hub_state()
            flat["hub_identity"] = hub.my_identity
            flat["hub_is_coordinator"] = hub.my_is_coordinator
            flat["hub_peers"] = hub.peer_count
        except Exception as e:
            logger.debug("refresher get_hub_state failed: %s", e)

        # --- mcp state ---
        try:
            mcp = await self._state.get_mcp_state()
            flat["mcp"] = {
                "connected": mcp.connected_servers,
                "tools": mcp.total_tools,
            }
        except Exception as e:
            logger.debug("refresher get_mcp_state failed: %s", e)

        # --- profile state ---
        try:
            profile = await self._state.get_active_profile()
            flat["profile_name"] = profile.name
            flat["model"] = profile.model
            flat["provider"] = profile.provider
            flat["endpoint"] = profile.endpoint
        except Exception as e:
            logger.debug("refresher get_active_profile failed: %s", e)

        # --- permission state ---
        try:
            perm = await self._state.get_permission_state()
            flat["approval_mode"] = perm.approval_mode
        except Exception as e:
            logger.debug("refresher get_permission_state failed: %s", e)

        # --- active agent / skills ---
        try:
            agent = await self._state.get_active_agent()
            if agent.name:
                flat["agent"] = agent.name
        except Exception as e:
            logger.debug("refresher get_active_agent failed: %s", e)

        try:
            skills = await self._state.list_skills()
            visible = [
                skill.name
                for skill in skills.skills
                if skill.active and skill.name != "system_prompt"
            ]
            if visible:
                flat["skills"] = ", ".join(visible)
            elif skills.skills:
                flat["skills"] = "no-skill"
        except Exception as e:
            logger.debug("refresher list_skills failed: %s", e)

        return flat
