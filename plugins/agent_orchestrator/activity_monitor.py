"""Monitor agent panes for completion via MD5 hashing."""

import asyncio
import hashlib
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict

logger = logging.getLogger(__name__)


@dataclass
class AgentState:
    """Tracks activity state for a single agent.

    Stores the monitoring state for each agent being tracked, including
    the last observed content hash and consecutive idle poll counts.

    Attributes:
        name: The unique identifier for the agent.
        last_hash: MD5 hash of the last observed pane content.
        idle_count: Number of consecutive polls with unchanged content.
    """

    name: str
    last_hash: str = ""
    idle_count: int = 0


class ActivityMonitor:
    """Monitor agent panes for completion via MD5 hashing.

    This class polls agent output at regular intervals and computes MD5 hash
    of their content. When content remains unchanged for a threshold number
    of consecutive polls, the agent is considered complete and a callback
    is triggered.

    The monitor operates asynchronously in a background loop, checking all
    tracked agents at each poll interval.
    """

    def __init__(
        self,
        orchestrator,
        on_agent_complete: Callable[[str, str, str], Awaitable[None]],
        poll_interval: int = 2,
        idle_threshold: int = 3,
        capture_lines: int = 500,
    ):
        """Initialize the activity monitor.

        Creates a new ActivityMonitor instance with configurable polling behavior.

        Args:
            orchestrator: AgentOrchestrator instance used to capture pane output.
            on_agent_complete: Async callback function invoked when an agent completes.
                Receives (name, duration, output) as arguments.
            poll_interval: Seconds between polling checks. Defaults to 2.
            idle_threshold: Number of consecutive unchanged polls required to
                consider an agent complete. Defaults to 3.
            capture_lines: Number of lines to capture from pane when agent completes.
                Defaults to 500.
        """
        self.orchestrator = orchestrator
        self.on_agent_complete = on_agent_complete
        self.poll_interval = poll_interval
        self.idle_threshold = idle_threshold
        self.capture_lines = capture_lines

        self.tracked: Dict[str, AgentState] = {}
        self._running = False
        self._task = None

    def track(self, name: str) -> None:
        """Start tracking an agent.

        Initializes a new AgentState for the specified agent and begins
        monitoring its output for activity changes.

        Args:
            name: The unique identifier/name of the agent to track.
        """
        if name not in self.tracked:
            self.tracked[name] = AgentState(name=name)
            logger.debug(f"Now tracking agent: {name}")

    def untrack(self, name: str) -> None:
        """Stop tracking an agent.

        Removes the agent from the tracked set and stops monitoring its
        output for activity.

        Args:
            name: The unique identifier/name of the agent to stop tracking.
        """
        if name in self.tracked:
            del self.tracked[name]
            logger.debug(f"Stopped tracking agent: {name}")

    def is_tracking(self, name: str) -> bool:
        """Check if an agent is currently being tracked.

        Determines whether the specified agent is in the active tracking set.

        Args:
            name: The unique identifier/name of the agent to check.

        Returns:
            True if the agent is currently being tracked, False otherwise.
        """
        return name in self.tracked

    async def start(self) -> None:
        """Start the monitoring loop.

        Begins the asynchronous polling loop that checks all tracked agents
        at the configured poll_interval. Runs until stop() is called.

        Raises:
            Exception: Errors during polling are caught and logged but do not
                stop the monitoring loop.
        """
        self._running = True
        logger.info(
            f"Activity monitor started (poll={self.poll_interval}s, "
            f"threshold={self.idle_threshold})"
        )

        while self._running:
            try:
                await self._check_agents()
            except Exception as e:
                logger.error(f"Error in activity monitor: {e}")

            await asyncio.sleep(self.poll_interval)

    async def stop(self) -> None:
        """Stop the monitoring loop.

        Sets the running flag to False, causing the polling loop to exit
        on its next iteration.
        """
        self._running = False
        logger.info("Activity monitor stopped")

    async def _check_agents(self) -> None:
        """Check all tracked agents for completion.

        Iterates through all tracked agents, captures their current pane
        content, computes MD5 hashes, and compares against the last observed
        hash. Agents with unchanged content exceeding the idle_threshold
        are marked as complete and their completion callbacks are invoked.

        Raises:
            Exception: Errors for individual agents are caught and logged,
                but do not prevent checking other agents.
        """
        if not self.tracked:
            return

        completed = []

        for name, state in list(self.tracked.items()):
            try:
                # Capture current pane content
                content = self.orchestrator.capture_output(name, self.capture_lines)

                if not content:
                    # Session might be gone
                    continue

                current_hash = hashlib.md5(content.encode()).hexdigest()

                if current_hash == state.last_hash:
                    # No change - increment idle count
                    state.idle_count += 1
                    logger.debug(
                        f"Agent {name} idle count: {state.idle_count}/{self.idle_threshold}"
                    )

                    if state.idle_count >= self.idle_threshold:
                        # Agent is done
                        completed.append((name, content))
                else:
                    # Content changed - reset idle count
                    if state.idle_count > 0:
                        logger.debug(
                            f"Agent {name} activity detected, resetting idle count"
                        )
                    state.idle_count = 0
                    state.last_hash = current_hash

            except Exception as e:
                logger.error(f"Error checking agent {name}: {e}")

        # Notify completions
        for name, content in completed:
            agent = self.orchestrator.get_agent(name)
            duration = agent.duration if agent else "?"

            logger.info(f"Agent {name} completed @ {duration}")

            # Remove from tracking before callback to prevent re-detection
            self.untrack(name)

            try:
                await self.on_agent_complete(name, duration, content)
            except Exception as e:
                logger.error(f"Error in completion callback for {name}: {e}")

    def get_tracked_agents(self) -> list:
        """Get list of currently tracked agent names.

        Returns a snapshot of all agent names currently under active monitoring.

        Returns:
            A list of strings representing the names of all tracked agents.
        """
        return list(self.tracked.keys())

    def reset_agent_state(self, name: str) -> None:
        """Reset idle state for an agent.

        Clears the idle counter and last hash for the specified agent,
        effectively restarting the completion detection process. This
        is useful when sending new messages to an agent that should trigger
        fresh activity monitoring.

        Args:
            name: The unique identifier/name of the agent to reset.
        """
        if name in self.tracked:
            self.tracked[name].idle_count = 0
            self.tracked[name].last_hash = ""
            logger.debug(f"Reset activity state for agent: {name}")
