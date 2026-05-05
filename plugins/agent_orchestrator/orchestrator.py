"""Manages agent sub-processes via subprocess.Popen."""

import asyncio
import fnmatch
import hashlib
import logging
import os
import shlex
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Coroutine, Dict, List, Optional, Tuple

from .file_attacher import FileAttacher
from .models import AgentSession, AgentTask
from .ring_buffer import RingBuffer

logger = logging.getLogger(__name__)


class AgentOrchestrator:
    """Manages agent sub-processes via subprocess.Popen."""

    def __init__(
        self,
        project_name: Optional[str] = None,
        session_init_delay: float = 3.0,
        kollab_init_delay: float = 4.0,
        message_delay: float = 2.0,
        ready_timeout: float = 30.0,
        ready_poll_interval: float = 0.5,
        ready_stable_threshold: int = 2,
        ready_initial_wait: float = 1.0,
    ):
        """Initialize orchestrator.

        Args:
            project_name: Project name for session naming. Defaults to cwd.
                          Uses KOLLAB_ROOT_SOCKET env var for nested agents.
            session_init_delay: Seconds to wait after creating subprocess.
            kollab_init_delay: Fallback delay if ready detection fails.
            message_delay: Seconds to wait after sending a message.
            ready_timeout: Max seconds to wait for kollab to be ready.
            ready_poll_interval: Seconds between ready detection polls.
            ready_stable_threshold: Consecutive stable polls required.
            ready_initial_wait: Seconds to wait before starting ready detection.
        """
        self.project_name = (
            os.environ.get("KOLLAB_ROOT_SOCKET") or project_name or Path.cwd().name
        )
        self.agents: Dict[str, AgentSession] = {}
        self.file_attacher = FileAttacher()

        self._background_tasks: set = set()

        # Timing configuration
        self.session_init_delay = session_init_delay
        self.kollab_init_delay = kollab_init_delay
        self.message_delay = message_delay

        # Ready detection configuration
        self.ready_timeout = ready_timeout
        self.ready_poll_interval = ready_poll_interval
        self.ready_stable_threshold = ready_stable_threshold
        self.ready_initial_wait = ready_initial_wait

    # -------------------------------------------------------------------------
    # DRY Helpers
    # -------------------------------------------------------------------------

    def _full_name(self, name: str) -> str:
        """Build full session name (kept for backward compat)."""
        return f"{self.project_name}-{name}"

    def _process_alive(self, name: str) -> bool:
        """Check if agent subprocess is still running."""
        agent = self.agents.get(name)
        if agent is None:
            return False
        return agent.is_alive

    async def _setup_session(
        self,
        name: str,
        clean_stale: bool = True,
        agent_type: str = "",
        skills: Optional[List[str]] = None,
        identity: str = "",
        initial_task: str = "",
        profile: str = "",
    ) -> Optional[str]:
        """Create session and track agent.

        Args:
            name: Agent name
            clean_stale: If True, clean up stale sessions with same name
            agent_type: Agent bundle name (passed as --agent flag)
            skills: Skill names (passed as --skill flags)
            identity: Optional hub identity to request via --as flag
            initial_task: Optional task to pass as positional arg so the
                spawned agent processes it as initial_message on startup.
                Required for --detached spawns since their stdin is
                redirected to /dev/null (writes to proc.stdin go nowhere).
            profile: Optional LLM profile to use (passed as --profile flag)

        Returns:
            Full session name on success, None on failure
        """
        full_name = self._full_name(name)

        if name in self.agents:
            agent = self.agents[name]
            if agent.is_alive:
                logger.warning(f"Agent {name} is already running")
                return None
            elif clean_stale:
                logger.warning(f"Found stale agent {name}, cleaning up...")
                self._kill_session(full_name)
                del self.agents[name]

        proc, ring_buf = await self._create_session(
            full_name, agent_name=name, agent_type=agent_type,
            skills=skills, identity=identity, initial_task=initial_task,
            profile=profile,
        )
        if proc is None:
            logger.error(f"Failed to create subprocess for agent: {name}")
            return None

        self.agents[name] = AgentSession(
            name=name,
            full_name=full_name,
            status="initializing",
            start_time=time.time(),
            proc=proc,
            ring_buffer=ring_buf,
            pid=proc.pid,
        )
        return full_name

    def _schedule_background(self, coro: Coroutine[Any, Any, None]) -> None:
        """Schedule coroutine as tracked background task."""
        task = asyncio.create_task(coro)
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _run_or_schedule(
        self, coro: Coroutine[Any, Any, None], wait: bool, name: str, desc: str
    ) -> None:
        """Run coroutine immediately or schedule in background."""
        if wait:
            await coro
            logger.info(f"Spawn complete for {desc}: {name} (waited)")
        else:
            self._schedule_background(coro)
            logger.info(f"Spawn initiated for {desc}: {name} (background)")

    def _update_status(self, name: str, status: str) -> None:
        """Update agent status if tracked."""
        if name in self.agents:
            self.agents[name].status = status

    def _build_task_message(self, task: str, files: Optional[List[str]] = None) -> str:
        """Build message with optional file attachments."""
        message = ""
        if files:
            message = self.file_attacher.attach(files) + "\n\n"
        message += task
        return message

    def _build_team_lead_message(self, max_workers: int, task: AgentTask) -> str:
        """Build team lead message with instructions and task."""
        prompt = (
            "You are a team lead agent.\n"
            f"You can spawn up to {max_workers} subagents using <agent> tags.\n"
            "Coordinate their work and integrate results.\n"
            "Use <status /> to check on workers.\n"
            "Use <capture>worker-name 100</capture> to see their progress.\n\n"
        )
        return prompt + self._build_task_message(task.task, task.files)

    async def _wait_for_ready(self, full_name: str) -> bool:
        """Wait for kollab to be ready by detecting stable output.

        Polls the ring buffer and waits for output to stabilize, indicating
        the app has finished initializing and is ready for input.

        Uses instance configuration:
            - ready_timeout: Max seconds to wait
            - ready_poll_interval: Seconds between polls
            - ready_stable_threshold: Consecutive stable polls required
            - ready_initial_wait: Initial wait before polling

        Args:
            full_name: Full session name.

        Returns:
            True if ready detected, False if timeout.
        """
        last_hash = ""
        stable_count = 0
        start = time.time()

        # Wait a minimum time for app to start producing output
        await asyncio.sleep(self.ready_initial_wait)

        while time.time() - start < self.ready_timeout:
            content = self.capture_output(full_name, 50)
            current_hash = hashlib.md5(content.encode()).hexdigest()

            if current_hash == last_hash and content.strip():
                stable_count += 1
                logger.debug(
                    f"[ready] {full_name} stable count: {stable_count}/{self.ready_stable_threshold}"
                )
                if stable_count >= self.ready_stable_threshold:
                    logger.info(
                        f"[ready] {full_name} ready after {time.time() - start:.1f}s"
                    )
                    return True
            else:
                if stable_count > 0:
                    logger.debug(f"[ready] {full_name} content changed, resetting")
                stable_count = 0
                last_hash = current_hash

            await asyncio.sleep(self.ready_poll_interval)

        logger.warning(f"[ready] {full_name} timeout after {self.ready_timeout}s")
        return False

    # -------------------------------------------------------------------------
    # Spawn
    # -------------------------------------------------------------------------

    def _build_kollab_cmd(
        self,
        agent_type: str = "",
        skills: Optional[List[str]] = None,
        extra_args: str = "",
    ) -> str:
        """Build kollab command with optional flags."""
        cmd = "kollab --detached --permissions trust "
        agent_flag = agent_type or getattr(self, "agent_name", "")
        if agent_flag:
            cmd += f" --agent {shlex.quote(agent_flag)}"
        for skill in skills or []:
            cmd += f" --skill {shlex.quote(skill)}"
        if extra_args:
            cmd += f" {extra_args}"
        return cmd

    async def spawn(
        self,
        name: str,
        task: str,
        files: Optional[List[str]] = None,
        wait: bool = False,
        agent_type: str = "",
        skills: Optional[List[str]] = None,
        identity: str = "",
        profile: str = "",
    ) -> bool:
        """Spawn a new agent session.

        By default, this is NON-BLOCKING - it creates the session and kicks off
        background initialization, returning immediately so multiple agents
        can be spawned in parallel.

        If wait=True, waits for full initialization before returning.

        Args:
            name: Agent name
            task: Task description
            files: Optional list of files to attach
            wait: If True, wait for initialization to complete
            agent_type: Optional agent type to use (e.g., "coder", "research")
            skills: Optional list of skills to load
            identity: Optional hub identity to request via --as flag
            profile: Optional LLM profile to use (e.g., "glm4", "claude")

        Returns:
            True if spawn was initiated successfully
        """
        message = self._build_task_message(task, files)
        full_name = await self._setup_session(
            name, agent_type=agent_type, skills=skills, identity=identity,
            initial_task=message, profile=profile,
        )
        if not full_name:
            return False

        # Task delivered as positional arg in _create_session. The agent
        # processes it as initial_message during its own startup.
        # Background coro just monitors readiness and updates status.
        coro = self._wait_and_mark_ready(name, full_name)
        await self._run_or_schedule(coro, wait, name, "agent")

        return True

    async def _wait_and_mark_ready(self, name: str, full_name: str) -> None:
        """Mark agent running. Initial task already passed via cmd arg.

        Detached agents redirect stdout to /dev/null so ring-buffer-based
        readiness detection can't work. We just record startup -- if the
        task fails the agent will surface errors via hub messages.
        """
        try:
            await asyncio.sleep(self.kollab_init_delay)
            self._update_status(name, "running")
            logger.info(f"Background initialization complete for agent: {name}")
        except Exception as e:
            logger.error(f"Error during background initialization of {name}: {e}")
            self._update_status(name, "error")

    async def spawn_clone(
        self,
        name: str,
        task: str,
        files: List[str],
        conversation_file: str,
        wait: bool = False,
    ) -> bool:
        """Spawn agent with conversation context.

        By default NON-BLOCKING. If wait=True, waits for initialization.

        Args:
            name: Agent name
            task: Task description
            files: Files to attach
            conversation_file: Path to exported conversation JSON
            wait: If True, wait for initialization to complete

        Returns:
            True if spawn was initiated successfully
        """
        message = self._build_task_message(task, files)
        full_name = await self._setup_session(
            name, clean_stale=False, initial_task=message,
        )
        if not full_name:
            return False

        coro = self._wait_and_mark_ready(name, full_name)
        await self._run_or_schedule(coro, wait, name, "clone agent")

        return True

    async def spawn_team_lead(
        self,
        lead_name: str,
        max_workers: int,
        task: AgentTask,
        wait: bool = False,
    ) -> bool:
        """Spawn a team lead agent that can spawn workers.

        By default NON-BLOCKING. If wait=True, waits for initialization.

        Args:
            lead_name: Lead agent name
            max_workers: Maximum number of workers the lead can spawn
            task: Task for the lead
            wait: If True, wait for initialization to complete

        Returns:
            True if spawn was initiated successfully
        """
        message = self._build_team_lead_message(max_workers, task)
        full_name = await self._setup_session(
            lead_name, clean_stale=False, initial_task=message,
        )
        if not full_name:
            return False

        coro = self._wait_and_mark_ready(lead_name, full_name)
        await self._run_or_schedule(coro, wait, lead_name, "team lead")

        return True

    # -------------------------------------------------------------------------
    # Message
    # -------------------------------------------------------------------------

    async def message(self, name: str, content: str) -> bool:
        """Send message to agent (legacy /sub orchestrator path).

        WARNING: this writes to proc.stdin via _send_keys. Agents spawned
        with --detached have stdin redirected to /dev/null after fork,
        so this is a no-op for them. Hub agents should use the hub socket
        (kollab --hub msg) instead. This method is kept for the legacy
        agent_orchestrator (/sub) flow only.

        Args:
            name: Agent name
            content: Message content

        Returns:
            True if successful
        """
        if not self._process_alive(name):
            full_name = self._full_name(name)
            logger.warning(f"Process not alive for agent: {full_name}")
            return False

        full_name = self._full_name(name)
        await self._send_keys(full_name, content)
        logger.info(f"Sent message to agent: {name}")
        return True

    # -------------------------------------------------------------------------
    # Stop
    # -------------------------------------------------------------------------

    async def stop(self, name: str) -> Tuple[str, str]:
        """Stop agent and return final output + duration.

        Args:
            name: Agent name

        Returns:
            Tuple of (output, duration)
        """
        full_name = self._full_name(name)

        # Wait for agent to finish initializing if needed
        agent = self.agents.get(name)
        max_wait = 10
        wait_start = time.time()

        while (
            agent
            and agent.status == "initializing"
            and (time.time() - wait_start) < max_wait
        ):
            logger.debug(f"Waiting for {name} to finish initializing...")
            await asyncio.sleep(0.5)
            agent = self.agents.get(name)

        # Capture final output
        output = self.capture_output(name, 100)

        # Get duration
        duration = agent.duration if agent else "?"

        # Kill process
        self._kill_session(full_name)

        # Remove from tracking
        if name in self.agents:
            del self.agents[name]

        logger.info(f"Stopped agent: {name} @ {duration}")
        return output, duration

    # -------------------------------------------------------------------------
    # Status / Capture
    # -------------------------------------------------------------------------

    def list_agents(self) -> List[AgentSession]:
        """List all active agents."""
        self._refresh_agents()
        return list(self.agents.values())

    def get_agent(self, name: str) -> Optional[AgentSession]:
        """Get specific agent."""
        return self.agents.get(name)

    def get_all_agents(self) -> Dict[str, AgentSession]:
        """Get all tracked agents."""
        self._refresh_agents()
        return self.agents.copy()

    def find_agents(self, pattern: str) -> List[str]:
        """Find agents matching glob pattern."""
        return [name for name in self.agents.keys() if fnmatch.fnmatch(name, pattern)]

    def capture_output(self, name: str, lines: int = 50) -> str:
        """Capture last N lines from agent's ring buffer.

        Args:
            name: Agent name (or full_name -- both are resolved)
            lines: Number of lines to capture

        Returns:
            Captured output as a single string
        """
        # Resolve: callers may pass either name or full_name
        agent = self.agents.get(name)
        if agent is None:
            # Try stripping project prefix (full_name -> name)
            for a in self.agents.values():
                if a.full_name == name:
                    agent = a
                    break

        if agent is None or agent.ring_buffer is None:
            return ""

        return "\n".join(agent.ring_buffer.get_last(lines))

    # -------------------------------------------------------------------------
    # Private Helpers -- subprocess management
    # -------------------------------------------------------------------------

    async def _create_session(
        self,
        full_name: str,
        agent_name: str = "",
        agent_type: str = "",
        skills: Optional[List[str]] = None,
        identity: str = "",
        initial_task: str = "",
        profile: str = "",
    ) -> Tuple[Optional[subprocess.Popen], Optional[RingBuffer]]:
        """Spawn a new agent subprocess.

        Args:
            full_name: Full session name (project-agent) for logging
            agent_name: Agent name for KOLLAB_AGENT_NAME env var
            agent_type: Agent bundle name (passed as --agent flag)
            skills: Skill names (passed as --skill flags)
            identity: Optional hub identity to request via --as flag
            initial_task: Optional task passed as positional arg so the
                spawned agent picks it up as initial_message.
            profile: Optional LLM profile to use (passed as --profile flag)

        Returns:
            Tuple of (Popen process, RingBuffer) on success, (None, None) on failure
        """
        cwd = str(Path.cwd())
        root_socket = os.environ.get("KOLLAB_ROOT_SOCKET", self.project_name)

        env = os.environ.copy()
        env["KOLLAB_ROOT_SOCKET"] = root_socket
        env["KOLLAB_AGENT_NAME"] = agent_name
        env["KOLLAB_PARENT_PID"] = str(os.getpid())

        cmd = ["python3", "main.py", "--detached", "--permissions", "trust"]

        # Add agent bundle flag so the spawned agent joins the hub mesh
        agent_flag = agent_type or agent_name
        if agent_flag:
            cmd.extend(["--agent", agent_flag])

        # Pass profile so spawned agent uses the correct LLM provider.
        # Without this, agents with no profile in agent.json fall back
        # to the "default" profile (usually Anthropic) instead of the
        # parent's active profile or the agent's preferred profile.
        if profile:
            cmd.extend(["--profile", profile])

        for skill in skills or []:
            cmd.extend(["--skill", skill])

        # Pass hub identity request via --as flag so the spawned agent
        # requests a specific identity from the pool during hub startup
        if identity:
            cmd.extend(["--as", identity])

        # Pass initial task as positional arg. --detached redirects stdin
        # to /dev/null in the child after fork, so writing to proc.stdin
        # via _send_keys is a no-op. The positional arg is parsed by
        # argparse in the child and becomes initial_message in cli.py.
        # The "--" separator stops argparse from interpreting tasks that
        # start with "-" as flags.
        if initial_task:
            cmd.append("--")
            cmd.append(initial_task)

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=cwd,
                env=env,
                start_new_session=True,
            )
        except Exception as e:
            logger.error(f"Failed to spawn subprocess for {full_name}: {e}")
            return None, None

        ring_buf = RingBuffer()

        # Start daemon thread to pump stdout into ring buffer
        pump_thread = threading.Thread(
            target=self._pump_output,
            args=(proc, ring_buf, full_name),
            daemon=True,
            name=f"pump-{agent_name}",
        )
        pump_thread.start()

        logger.info(f"Spawned subprocess for {full_name} (pid={proc.pid})")
        await asyncio.sleep(self.session_init_delay)
        return proc, ring_buf

    @staticmethod
    def _pump_output(proc: subprocess.Popen, ring_buf: RingBuffer, label: str) -> None:
        """Read proc.stdout line-by-line into ring buffer.

        Runs in a daemon thread. Stops when stdout returns empty
        bytes (process exited / pipe closed).
        """
        stdout = proc.stdout
        if stdout is None:
            return
        try:
            for raw_line in iter(stdout.readline, b""):
                try:
                    line = raw_line.decode("utf-8", errors="replace").rstrip("\n")
                except Exception:
                    line = repr(raw_line)
                ring_buf.append(line)
        except Exception as e:
            logger.debug(f"[pump] {label} pump ended: {e}")
        finally:
            logger.debug(f"[pump] {label} stdout closed")

    async def _send_keys(self, full_name: str, content: str) -> bool:
        """Write content to agent's stdin.

        Uses a thread executor to avoid blocking the event loop on
        the synchronous stdin.write / stdin.flush calls.
        """
        # Resolve agent
        agent = None
        for a in self.agents.values():
            if a.full_name == full_name:
                agent = a
                break

        if agent is None or agent.proc is None or agent.proc.stdin is None:
            logger.error(f"[send_keys] No valid stdin for {full_name}")
            return False

        if not agent.is_alive:
            logger.error(f"[send_keys] Process dead for {full_name}")
            return False

        logger.debug(f"[send_keys] Sending to {full_name}: {len(content)} chars")

        loop = asyncio.get_running_loop()

        def _write():
            try:
                data = (content + "\n").encode("utf-8")
                agent.proc.stdin.write(data)
                agent.proc.stdin.flush()
                return True
            except (BrokenPipeError, OSError) as e:
                logger.error(f"[send_keys] Write failed for {full_name}: {e}")
                return False

        result: bool = await loop.run_in_executor(None, _write)

        logger.debug(f"[send_keys] Waiting {self.message_delay}s message delay")
        await asyncio.sleep(self.message_delay)
        return bool(result)

    def _kill_session(self, full_name: str) -> bool:
        """Terminate agent subprocess.

        Sends SIGTERM, waits up to 5s, then SIGKILL if still alive.
        """
        agent = None
        for a in self.agents.values():
            if a.full_name == full_name:
                agent = a
                break

        if agent is None or agent.proc is None:
            logger.warning(f"No process found for {full_name}")
            return False

        proc = agent.proc
        if proc.poll() is not None:
            logger.info(f"Process already exited for {full_name}")
            return True

        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
                logger.info(f"Terminated process for {full_name} (pid={proc.pid})")
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2)
                logger.warning(f"Killed process for {full_name} (pid={proc.pid})")
            return True
        except Exception as e:
            logger.error(f"Failed to kill process for {full_name}: {e}")
            return False

    def cleanup_stale_sessions(self) -> int:
        """Clean up stale agent processes that have exited.

        Returns:
            Number of agents cleaned up.
        """
        cleaned = 0
        dead_names = []

        for name, agent in self.agents.items():
            if not agent.is_alive and agent.status != "initializing":
                dead_names.append(name)

        for name in dead_names:
            agent = self.agents[name]
            if agent.status == "error" or not agent.is_alive:
                logger.info(f"Cleaning up stale agent: {name}")
                self._kill_session(agent.full_name)
                del self.agents[name]
                cleaned += 1

        if cleaned > 0:
            logger.info(f"Cleaned up {cleaned} stale agent(s)")

        return cleaned

    def _refresh_agents(self) -> None:
        """Refresh agent status from subprocess poll."""
        dead = []
        for name, agent in self.agents.items():
            if not agent.is_alive and agent.status not in ("initializing", "stopped"):
                dead.append(name)

        for name in dead:
            del self.agents[name]

    async def shutdown(self) -> None:
        """Cancel all pending background tasks and kill all agent processes."""
        # Cancel async tasks
        if self._background_tasks:
            count = len(self._background_tasks)
            logger.info(f"Cancelling {count} pending background tasks")
            for task in self._background_tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
            self._background_tasks.clear()
            logger.info("All background tasks cancelled")

        # Kill all agent subprocesses
        for name in list(self.agents.keys()):
            agent = self.agents[name]
            self._kill_session(agent.full_name)

        self.agents.clear()
        logger.info("All agent processes terminated")
