"""Base class and models for the AltView framework."""

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional

from kollabor_tui.key_parser import KeyPress

logger = logging.getLogger(__name__)


class AltViewState(Enum):
    """Lifecycle states for an AltView instance.

    States:
        CREATED: Constructed but not yet entered.
        RUNNING: Actively rendering in the foreground.
        SUSPENDED: Paused while another view has focus; background tasks
                   may still be running.
        IDLE: Suspended with no remaining background tasks.
        COMPLETE: Finished; ready for cleanup.
    """

    CREATED = "created"
    RUNNING = "running"
    SUSPENDED = "suspended"
    IDLE = "idle"
    COMPLETE = "complete"


@dataclass
class AltViewMetadata:
    """Metadata describing an AltView plugin.

    Attributes:
        plugin_type: Unique identifier for this AltView type (e.g. "matrix", "editor").
        description: Human-readable description.
        version: Semver version string.
        author: Author name or handle.
        category: Grouping category (e.g. "effect", "tool", "general").
        icon: Optional single-character icon for menus.
        aliases: Alternative names that can activate this view.
        supports_named_sessions: Whether multiple named sessions are allowed.
        supports_background: Whether background tasks continue when suspended.
    """

    plugin_type: str
    description: str = ""
    version: str = "1.0.0"
    author: str = ""
    category: str = "general"
    icon: str = ""
    aliases: list = field(default_factory=list)
    supports_named_sessions: bool = True
    supports_background: bool = False


class AltView(ABC):
    """Abstract base class for all AltView plugins.

    An AltView takes complete control of the terminal alternate buffer.
    Subclasses implement rendering, input handling, and lifecycle hooks.
    The framework manages state transitions and background task tracking.
    """

    def __init__(self, metadata: AltViewMetadata) -> None:
        """Initialize the AltView.

        Args:
            metadata: Metadata describing this view.
        """
        self.metadata = metadata
        self._state: AltViewState = AltViewState.CREATED
        self._session_name: Optional[str] = None
        self._renderer: Optional[Any] = None
        self._background_tasks: List[asyncio.Task] = []

        # frame rate control -- subclasses can override
        self.target_fps: float = 20.0

        logger.info("AltView created: %s", metadata.plugin_type)

    # -- properties --

    @property
    def session_name(self) -> Optional[str]:
        """The name of the current session, if any."""
        return self._session_name

    @property
    def state(self) -> AltViewState:
        """Current lifecycle state."""
        return self._state

    @property
    def renderer(self) -> Optional[Any]:
        """The renderer assigned to this view."""
        return self._renderer

    @property
    def background_tasks(self) -> List[asyncio.Task]:
        """Active background tasks spawned by this view."""
        return list(self._background_tasks)

    # -- lifecycle --

    async def create_session(self, session_name: str) -> bool:
        """Create a named session for this view.

        Called by the stack manager before on_enter. Sets up session
        identity and transitions state from CREATED to CREATED (ready
        for on_enter).

        Args:
            session_name: Unique name for this session.

        Returns:
            True if session was created successfully.
        """
        self._session_name = session_name
        logger.info(
            "AltView %s: session created: %s",
            self.metadata.plugin_type,
            session_name,
        )
        return True

    @abstractmethod
    async def on_enter(self, renderer: Any) -> None:
        """Called when the view takes foreground control.

        Implementations should store the renderer reference and set up
        any rendering state. The framework sets state to RUNNING after
        this returns.

        Args:
            renderer: The terminal renderer to draw to.
        """
        ...

    @abstractmethod
    async def render_frame(self, delta_time: float) -> bool:
        """Render a single frame.

        Called every frame while the view is in the RUNNING state.

        Args:
            delta_time: Seconds elapsed since the previous frame.

        Returns:
            True to continue running, False to signal completion.
        """
        ...

    @abstractmethod
    async def handle_input(self, key_press: KeyPress) -> bool:
        """Handle a user key press.

        Called when the view is in the foreground and a key is pressed.

        Args:
            key_press: The parsed key press event.

        Returns:
            True if the view should exit, False to continue.
        """
        ...

    async def on_suspend(self) -> None:
        """Called when the view is being moved to the background.

        The framework transitions state to SUSPENDED after this returns.
        Background tasks continue running unless cancelled here.
        """
        logger.debug(
            "AltView %s: suspending session %s",
            self.metadata.plugin_type,
            self._session_name,
        )

    async def on_resume(self) -> None:
        """Called when the view returns to the foreground.

        The framework transitions state to RUNNING after this returns.
        Implementations should refresh any cached render state.
        """
        logger.debug(
            "AltView %s: resuming session %s",
            self.metadata.plugin_type,
            self._session_name,
        )

    async def on_complete(self) -> None:
        """Called when the view is finished and being torn down.

        The framework cancels remaining background tasks and transitions
        state to COMPLETE after this returns. Implementations should
        release any resources here.
        """
        # cancel any remaining background tasks
        for task in self._background_tasks:
            if not task.done():
                task.cancel()

        if self._background_tasks:
            logger.debug(
                "AltView %s: cancelled %d background tasks on complete",
                self.metadata.plugin_type,
                len(self._background_tasks),
            )
            self._background_tasks.clear()

        self._renderer = None
        logger.info(
            "AltView %s: session %s complete",
            self.metadata.plugin_type,
            self._session_name,
        )

    # -- background tasks --

    def spawn_background_task(self, coro: Any, name: str = "") -> asyncio.Task:
        """Spawn a tracked background task.

        The task is automatically removed from tracking when it finishes.
        If this was the last background task and the view is SUSPENDED,
        state transitions to IDLE.

        Args:
            coro: The coroutine to run.
            name: Optional human-readable name for the task.

        Returns:
            The created asyncio.Task.
        """
        task_name = (
            f"altview:{self._session_name}:{name}"
            if name
            else (f"altview:{self._session_name}:{len(self._background_tasks)}")
        )
        task = asyncio.create_task(coro, name=task_name)
        self._background_tasks.append(task)
        task.add_done_callback(self._on_task_done)

        logger.debug(
            "AltView %s: spawned background task %s",
            self.metadata.plugin_type,
            task_name,
        )
        return task

    def _on_task_done(self, task: asyncio.Task) -> None:
        """Callback when a background task completes.

        Removes the task from tracking and transitions to IDLE if no
        more tasks remain and the view is currently SUSPENDED.

        Args:
            task: The completed task.
        """
        try:
            self._background_tasks.remove(task)
        except ValueError:
            pass  # already removed

        # log exceptions from background tasks
        if not task.cancelled() and task.exception() is not None:
            logger.error(
                "AltView %s: background task %s failed: %s",
                self.metadata.plugin_type,
                task.get_name(),
                task.exception(),
            )

        # transition to idle if suspended with no remaining tasks
        if self._state == AltViewState.SUSPENDED and not self._background_tasks:
            self._state = AltViewState.IDLE
            logger.debug(
                "AltView %s: no background tasks remain, state -> IDLE",
                self.metadata.plugin_type,
            )

    # -- internal state management (used by the stack manager) --

    def _set_state(self, new_state: AltViewState) -> None:
        """Set the lifecycle state. Called by the framework, not subclasses.

        Args:
            new_state: The new state to transition to.
        """
        old_state = self._state
        self._state = new_state
        logger.debug(
            "AltView %s: %s -> %s",
            self.metadata.plugin_type,
            old_state.value,
            new_state.value,
        )

    def _set_renderer(self, renderer: Any) -> None:
        """Assign the renderer. Called by the framework before on_enter.

        Args:
            renderer: The terminal renderer instance.
        """
        self._renderer = renderer
