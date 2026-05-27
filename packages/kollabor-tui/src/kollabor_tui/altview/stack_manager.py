"""AltView stack manager -- push/pop navigation with display queue replay."""

import logging
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from kollabor_events.models import EventType

from .base import AltView, AltViewState
from .display_queue import DisplayQueue
from .session import AltViewSession

logger = logging.getLogger(__name__)

MAX_STACK_DEPTH = 6


@dataclass
class SessionInfo:
    """Serializable session info for status widgets and external APIs."""

    name: str
    plugin_type: str
    state: AltViewState
    created_at: float
    last_entered: float
    background_task_count: int


class AltViewStackManager:
    """Manages a stack of AltView sessions with push/pop navigation.

    The stack allows one active (topmost) session at a time. When a new
    session is pushed, the current one is suspended. When the top session
    exits, the previous one resumes automatically.

    Sessions are kept in a registry by name so they can be re-entered
    without recreating them.
    """

    def __init__(self, event_bus, terminal_renderer) -> None:
        self.event_bus = event_bus
        self.terminal_renderer = terminal_renderer

        self._stack: List[AltViewSession] = []
        self._session_registry: Dict[str, AltViewSession] = {}
        self._main_render_loop = None  # resolved lazily from event bus

    # -- properties ---------------------------------------------------------

    @property
    def is_in_altview(self) -> bool:
        """True when at least one AltView session is on the stack."""
        return len(self._stack) > 0

    @property
    def active_session(self) -> Optional[AltViewSession]:
        """The topmost (currently running) session, or None."""
        if self._stack:
            return self._stack[-1]
        return None

    @property
    def active_display_queue(self) -> Optional[DisplayQueue]:
        """Display queue for the active session, or None."""
        session = self.active_session
        if session is not None:
            return session.display_queue
        return None

    @property
    def stack_depth(self) -> int:
        """Number of sessions currently on the stack."""
        return len(self._stack)

    def _resolve_main_render_loop(self):
        """Lazily resolve the main render loop from the event bus."""
        if self._main_render_loop is None and self.event_bus:
            self._main_render_loop = self.event_bus.get_service("main_render_loop")
        return self._main_render_loop

    def _resolve_scheduler(self):
        """Lazily resolve the refresh scheduler from the event bus."""
        if self.event_bus:
            return self.event_bus.get_service("refresh_scheduler")
        return None

    # -- push / pop ---------------------------------------------------------

    async def push(self, altview: AltView, session_name: str) -> bool:
        """Push an AltView onto the stack. Blocks until the user exits.

        If *session_name* already exists in the registry the existing
        session is re-entered. Otherwise a new session is created.

        Args:
            altview: The AltView plugin to run.
            session_name: Unique name for lookup and re-entry.

        Returns:
            False if the stack depth limit is reached, True otherwise.
        """
        if self.stack_depth >= MAX_STACK_DEPTH:
            logger.warning(
                "AltViewStackManager: stack depth limit (%d) reached, "
                "cannot push '%s'",
                MAX_STACK_DEPTH,
                session_name,
            )
            return False

        created_session = False

        # Look up or create the session
        session = self._session_registry.get(session_name)
        if session is None:
            session = AltViewSession(altview, self.event_bus, session_name)
            self._session_registry[session_name] = session
            created_session = True

        modal_started = False

        try:
            # Emit MODAL_TRIGGER + MODAL_SHOW so the input system routes
            # keypresses to FULLSCREEN_INPUT and the main render loop pauses.
            await self.event_bus.emit_with_hooks(
                EventType.MODAL_TRIGGER,
                {
                    "trigger_source": "altview",
                    "plugin_name": session_name,
                    "mode": "fullscreen",
                    "fullscreen_plugin": True,
                },
                "altview_stack_manager",
            )
            modal_started = True
            await self.event_bus.emit_with_hooks(
                EventType.MODAL_SHOW,
                {
                    "type": "altview",
                    "action": session_name,
                    "source": "altview_stack_manager",
                    "plugin_name": session_name,
                },
                "altview_stack_manager",
            )

            # Hibernate the main render loop (zero CPU while altview is active)
            main_loop = self._resolve_main_render_loop()
            if main_loop and hasattr(main_loop, "hibernate"):
                main_loop.hibernate()

            # Pause refresh scheduler to prevent queued renders bursting on thaw
            scheduler = self._resolve_scheduler()
            if scheduler and hasattr(scheduler, "pause"):
                scheduler.pause()

            await session.enter()
            self._stack.append(session)
            logger.info(
                "AltViewStackManager: pushed '%s' (depth=%d)",
                session_name,
                self.stack_depth,
            )

            # Blocks until the user exits this view
            await session.run_loop()

        finally:
            session_entered = session in self._stack
            await self._pop_current(
                session=session,
                modal_started=modal_started,
                session_entered=session_entered,
            )
            if created_session and not session_entered:
                self._session_registry.pop(session_name, None)

        return True

    async def _pop_current(
        self,
        session: Optional[AltViewSession] = None,
        modal_started: bool = True,
        session_entered: bool = True,
    ) -> None:
        """Pop the topmost session and replay its display queue if needed."""
        if session is None:
            if not self._stack:
                await self._restore_main_ui(None, modal_started=modal_started)
                return
            session = self._stack[-1]

        if self._stack and self._stack[-1] is session:
            self._stack.pop()
        elif session in self._stack:
            self._stack.remove(session)

        if session_entered:
            try:
                await session.exit()
            except Exception as e:
                logger.error(
                    "AltViewStackManager: session '%s' exit failed: %s",
                    session.session_name,
                    e,
                    exc_info=True,
                )

        await self._restore_main_ui(session.session_name, modal_started=modal_started)

        # Replay buffered frames if anything was captured while suspended
        if session.display_queue.frame_count > 0:
            await session.display_queue.replay(self._replay_frame)
            session.display_queue.clear()

        logger.info(
            "AltViewStackManager: popped '%s' (depth=%d)",
            session.session_name,
            self.stack_depth,
        )

    async def _restore_main_ui(
        self, session_name: Optional[str], modal_started: bool = True
    ) -> None:
        """Resume main UI rendering and input routing after an altview attempt."""
        # Resume refresh scheduler before emitting MODAL_HIDE
        scheduler = self._resolve_scheduler()
        if scheduler and hasattr(scheduler, "resume"):
            scheduler.resume()

        # Thaw the main render loop (was hibernated in push)
        main_loop = self._resolve_main_render_loop()
        if main_loop and hasattr(main_loop, "thaw"):
            main_loop.thaw()

        if not modal_started:
            return

        if self.terminal_renderer:
            if hasattr(self.terminal_renderer, "writing_messages"):
                self.terminal_renderer.writing_messages = False
            if hasattr(self.terminal_renderer, "input_line_written"):
                self.terminal_renderer.input_line_written = False
            if hasattr(self.terminal_renderer, "last_line_count"):
                self.terminal_renderer.last_line_count = 0
            invalidate = getattr(self.terminal_renderer, "invalidate_render_cache", None)
            if callable(invalidate):
                invalidate()

        # Emit MODAL_HIDE so input routing returns to normal
        # (modal_controller handles coordinator restore via its hook)
        try:
            await self.event_bus.emit_with_hooks(
                EventType.MODAL_HIDE,
                {
                    "source": "altview",
                    "plugin_name": session_name or "unknown",
                    "completed": True,
                },
                "altview_stack_manager",
            )
        except Exception as e:
            logger.error(
                "AltViewStackManager: MODAL_HIDE cleanup failed: %s",
                e,
                exc_info=True,
            )

    async def _replay_frame(self, content: str) -> None:
        """Write a single replay frame to the current output target.

        If another session is still on the stack (the one we are returning
        to), write into its renderer. Otherwise fall back to the main
        terminal renderer.
        """
        target = self.active_session
        if target is not None and target.renderer.is_active():
            target.renderer.write_raw(content)
        else:
            # Route through terminal_state instead of raw stdout
            # to avoid corrupting the render system
            ts = getattr(self.terminal_renderer, "terminal_state", None)
            if ts and hasattr(ts, "write_raw"):
                ts.write_raw(content)
            else:
                logger.warning("Replay frame with no terminal_state or active session")

    # -- registry -----------------------------------------------------------

    def get_all_sessions(self) -> Dict[str, AltViewSession]:
        """Return a copy of the session registry."""
        return dict(self._session_registry)

    def get_status_sessions(self) -> List[AltViewSession]:
        """Return sessions that should be visible in the status widget."""
        visible: List[AltViewSession] = list(self._stack)

        for session in self._session_registry.values():
            if session in visible:
                continue

            altview = session.altview
            metadata = altview.metadata
            state = altview.state
            supports_background = bool(
                getattr(metadata, "supports_background", False)
            )
            has_background_tasks = bool(altview.background_tasks)
            if supports_background and (
                has_background_tasks
                or state in (AltViewState.IDLE, AltViewState.COMPLETE)
            ):
                visible.append(session)

        return visible

    def get_session(self, name: str) -> Optional[AltViewSession]:
        """Look up a session by name."""
        return self._session_registry.get(name)

    def get_session_infos(self) -> List[SessionInfo]:
        """Return lightweight SessionInfo for every registered session."""
        now = time.monotonic()
        infos: List[SessionInfo] = []
        for name, session in self._session_registry.items():
            av = session.altview
            infos.append(
                SessionInfo(
                    name=name,
                    plugin_type=av.metadata.plugin_type,
                    state=av.state,
                    created_at=now,  # monotonic approximation
                    last_entered=now,
                    background_task_count=len(av.background_tasks),
                )
            )
        return infos

    async def destroy_session(self, name: str) -> bool:
        """Permanently destroy a session and remove it from the registry.

        A session that is currently on the stack cannot be destroyed.

        Returns:
            True if the session was found and destroyed.
        """
        session = self._session_registry.get(name)
        if session is None:
            logger.warning(
                "AltViewStackManager: cannot destroy unknown session '%s'",
                name,
            )
            return False

        if session in self._stack:
            logger.warning(
                "AltViewStackManager: cannot destroy active session '%s'",
                name,
            )
            return False

        await session.destroy()
        del self._session_registry[name]

        logger.info("AltViewStackManager: destroyed session '%s'", name)
        return True

    async def destroy_all_sessions(self) -> None:
        """Destroy every session that is not currently on the stack."""
        names = [
            name
            for name, session in self._session_registry.items()
            if session not in self._stack
        ]
        for name in names:
            await self.destroy_session(name)

        logger.info("AltViewStackManager: destroyed %d sessions", len(names))
