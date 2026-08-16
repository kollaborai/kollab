"""Daemon pool - one headless kollab process per engine session.

The engine used to run its own turn loop (``turn_runner.py``) over the shared
``kollabor_ai`` / ``kollabor_agent`` packages. That was a second implementation
of a loop the terminal client already had, with none of its features: no
plugins, no XML tag pipeline, no vault, no compaction, no conversation log.

Instead each session now owns a real ``kollab --detached`` daemon - the exact
process the terminal client forks - and the engine talks to it the same way an
attach client does:

  * one unix socket carries both directions
  * ``{"action": "rpc_request"}`` frames drive ``state.*`` methods
  * ``{"action": "rpc_reply"}`` frames resolve those calls
  * everything else on the wire is a display event, which the pool fans out to
    SSE subscribers

The daemon publishes structured ``token`` / ``tool_start`` / ``tool_result`` /
``turn_complete`` events alongside its ANSI ``output`` events (see
``kollabor_tui.display_tap.publish_semantic``), so the engine can serve a turn
without parsing escape codes.

One process per session is not a cost we chose - it is the shape the terminal
stack already enforces. Hub presence is keyed to a PID, the terminal state is a
module singleton, and a daemon owns its stdio outright. Sharing one process
across sessions would mean sharing one hub identity between browser tabs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .hub_bridge import HubBridge

logger = logging.getLogger(__name__)

# How long to wait for a freshly spawned daemon to publish its hub socket.
# Plugin discovery plus the hub join dominate this; a cold start on a laptop is
# a few seconds, so the ceiling is generous rather than tight.
SPAWN_TIMEOUT_SECONDS = 45.0

# Events the daemon publishes that are rendered ANSI rather than structured
# data. Web clients cannot use them, and forwarding them would double every
# message, so the pool drops them at the boundary.
_RENDERED_EVENT_TYPES = frozenset({"output", "active_area", "heartbeat", "clear"})


def _normalize_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """Reshape a daemon display event into the engine's documented SSE shape.

    The daemon's permission prompt arrives as
    ``{"type": "permission_request", "details": {...}}`` because that is what
    the terminal attach client renders. ``kollabor_engine.sse`` defines the
    flat shape API clients consume, so flatten it here rather than making every
    client learn a second layout.
    """
    if event.get("type") != "permission_request":
        return event

    details = event.get("details")
    if not isinstance(details, dict):
        return event

    tool_type = details.get("tool_type", "unknown")
    # `tool_name` is empty for terminal tools - the command is the useful label.
    tool_name = details.get("tool_name") or ""
    if not tool_name:
        command = details.get("command", "")
        file_path = details.get("file_path", "")
        tool_name = (
            f"{tool_type}: {command}"
            if command
            else f"{tool_type}: {file_path}"
            if file_path
            else tool_type
        )

    flat = {
        "type": "permission_request",
        "tool_id": details.get("tool_id", ""),
        "tool_name": tool_name,
        "tool_type": tool_type,
        "input": {
            k: v
            for k, v in details.items()
            if k in ("command", "file_path", "server_name", "content_preview")
        },
        "risk_level": str(details.get("risk_level", "unknown")).lower(),
        "risk_reason": details.get("risk_reason", ""),
        "details": details,
    }
    for key in ("ts", "session_id"):
        if key in event:
            flat[key] = event[key]
    return flat


def _kollab_command() -> List[str]:
    """Return the argv prefix that launches kollab in this environment."""
    console_script = shutil.which("kollab")
    if console_script:
        return [console_script]
    # Editable checkout without the console script on PATH.
    return [sys.executable, str(Path(__file__).resolve().parents[4] / "main.py")]


class DaemonHandle:
    """One headless kollab daemon, plus the socket the engine drives it over."""

    def __init__(self, session_id: str, identity: str, launcher: subprocess.Popen):
        self.session_id = session_id
        self.identity = identity
        # `kollab --detached` double-forks: the process we spawned is only a
        # launcher and exits 0 as soon as the real daemon is detached. The pid
        # that matters comes from hub presence, once the daemon publishes it.
        self.launcher = launcher
        self.pid: int = 0
        self.socket_path: str = ""
        self.created_at = time.time()

        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._read_task: Optional[asyncio.Task] = None
        self._subscribers: Set[asyncio.Queue] = set()
        self._closed = False

        self.rpc: Any = None
        self.state: Any = None

    # === lifecycle ===

    async def connect(self, socket_path: str) -> None:
        """Attach to the daemon's socket and start pumping its event stream."""
        from kollabor.state import RemoteStateService
        from kollabor_rpc import RpcClient, open_unix_connection_with_large_buffer

        self.socket_path = socket_path
        self._reader, self._writer = await open_unix_connection_with_large_buffer(
            socket_path
        )

        request = (
            json.dumps(
                {
                    "action": "attach",
                    "mode": "interactive",
                    "client_id": f"engine-{self.session_id}",
                }
            )
            + "\n"
        )
        self._writer.write(request.encode())
        await self._writer.drain()

        ack_line = await asyncio.wait_for(self._reader.readline(), timeout=10.0)
        if not ack_line:
            raise RuntimeError(f"daemon {self.identity} closed during attach")
        ack = json.loads(ack_line.decode().strip())
        if ack.get("type") != "attach_ack":
            raise RuntimeError(
                f"daemon {self.identity} refused attach: {ack.get('msg', ack)}"
            )

        # RPC replies and display events share this one connection, so the read
        # loop below is the only thing allowed to consume the reader.
        self.rpc = RpcClient(self._writer)
        self.state = RemoteStateService(rpc_client=self.rpc)
        self._read_task = asyncio.create_task(
            self._read_loop(), name=f"daemon-read-{self.session_id}"
        )
        logger.info(
            "engine session %s attached to daemon %s (pid %s)",
            self.session_id,
            self.identity,
            self.pid,
        )

    async def _read_loop(self) -> None:
        """Split the socket into RPC replies and display events."""
        assert self._reader is not None
        try:
            while True:
                line = await self._reader.readline()
                if not line:
                    break
                try:
                    message = json.loads(line.decode().strip())
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue

                if message.get("action") == "rpc_reply":
                    if self.rpc is not None:
                        self.rpc.on_reply(message)
                    continue

                if message.get("type") in _RENDERED_EVENT_TYPES:
                    continue

                self._fan_out(_normalize_event(message))
        except asyncio.CancelledError:
            raise
        except (ConnectionError, OSError) as e:
            logger.info("daemon %s stream closed: %s", self.identity, e)
        finally:
            # Wake every SSE consumer so requests don't hang on a dead daemon.
            self._fan_out({"type": "daemon_closed", "session_id": self.session_id})

    def _fan_out(self, event: Dict[str, Any]) -> None:
        event.setdefault("session_id", self.session_id)
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(
                    "dropping event for slow subscriber on session %s", self.session_id
                )

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    @property
    def alive(self) -> bool:
        """Liveness of the detached daemon, not of the launcher that spawned it."""
        if not self.pid:
            return False
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    async def close(self, timeout: float = 10.0) -> None:
        """Detach, then stop the daemon. SIGKILL only if it refuses to exit."""
        if self._closed:
            return
        self._closed = True

        if self._read_task is not None:
            self._read_task.cancel()
            try:
                await self._read_task
            except (asyncio.CancelledError, Exception):
                pass

        if self.rpc is not None:
            self.rpc.close()

        if self._writer is not None:
            try:
                self._writer.write((json.dumps({"type": "detach"}) + "\n").encode())
                await self._writer.drain()
            except (ConnectionError, OSError):
                pass
            try:
                self._writer.close()
            except Exception:
                pass
            else:
                # StreamWriter.close() only schedules transport shutdown. Await
                # wait_closed() so the socket transport is fully released before
                # the handle returns; otherwise asyncio debug mode reports
                # unclosed transports (and close races with a subsequent spawn).
                wait_closed = getattr(self._writer, "wait_closed", None)
                if wait_closed is not None:
                    try:
                        await wait_closed()
                    except (ConnectionError, OSError, RuntimeError):
                        # The peer/event loop may already have gone away. The
                        # daemon is still stopped below, so this is best effort.
                        pass

        # A spawn that failed before presence was read has no pid yet. Look it
        # up one last time so a failed create never leaves a live daemon behind.
        if not self.pid:
            try:
                from .hub_bridge import HubBridge

                agent = HubBridge().get_agent_by_identity(
                    self.identity, use_cache=False
                )
                if agent:
                    self.pid = int(agent.get("pid") or 0)
            except Exception as e:
                logger.debug("pid lookup during close failed: %s", e)

        # The launcher is long gone; signal the detached daemon by pid.
        if self.pid and self.alive:
            try:
                os.kill(self.pid, signal.SIGTERM)
            except ProcessLookupError:
                return

            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if not self.alive:
                    break
                await asyncio.sleep(0.1)
            else:
                logger.warning(
                    "daemon %s ignored SIGTERM, killing pid %s",
                    self.identity,
                    self.pid,
                )
                try:
                    os.kill(self.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

        logger.info("daemon %s stopped (session %s)", self.identity, self.session_id)


class DaemonPool:
    """Spawns and tracks one daemon per engine session."""

    def __init__(self) -> None:
        self._daemons: Dict[str, DaemonHandle] = {}
        self._bridge = HubBridge()
        self._spawn_lock = asyncio.Lock()

    def get(self, session_id: str) -> Optional[DaemonHandle]:
        return self._daemons.get(session_id)

    def all(self) -> List[DaemonHandle]:
        return list(self._daemons.values())

    async def spawn(
        self,
        session_id: str,
        *,
        profile: Optional[str] = None,
        agent: Optional[str] = None,
        workspace: Optional[str] = None,
        system_prompt: Optional[str] = None,
    ) -> DaemonHandle:
        """Start a daemon for `session_id` and attach to it.

        Serialized: two daemons racing to claim hub identities at the same
        moment is exactly the collision the hub's spawn guard rejects.
        """
        async with self._spawn_lock:
            existing = self._daemons.get(session_id)
            if existing is not None:
                if existing.alive:
                    return existing

                # A daemon can die outside this pool (for example after a
                # Hub stop or a crashed detached process). Do not overwrite a
                # dead handle while its reader/socket resources are still
                # attached; close it before claiming the session again.
                self._daemons.pop(session_id, None)
                try:
                    await existing.close()
                except Exception as e:
                    logger.debug(
                        "stale daemon handle cleanup failed for %s: %s",
                        session_id,
                        e,
                    )

            identity = f"web-{session_id.replace('sess_', '')[:12]}"
            argv = _kollab_command() + ["--detached", "--as", identity]
            if agent:
                argv += ["--agent", agent]
            if profile:
                argv += ["--llm", profile]

            env = dict(os.environ)
            if system_prompt:
                env["KOLLAB_SYSTEM_PROMPT"] = system_prompt

            cwd = workspace or os.getcwd()
            if not Path(cwd).is_dir():
                raise ValueError(f"workspace does not exist: {cwd}")

            logger.info("spawning daemon %s for session %s", identity, session_id)
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )

            handle = DaemonHandle(session_id, identity, process)
            try:
                socket_path = await self._await_socket(handle)
                await handle.connect(socket_path)
            except Exception:
                await handle.close()
                raise

            self._daemons[session_id] = handle
            return handle

    async def _await_socket(self, handle: DaemonHandle) -> str:
        """Poll hub presence until the daemon publishes a live socket.

        Also records the real daemon pid: `kollab --detached` forks and the
        launcher exits 0 immediately, so a zero exit is success, not failure.
        Only a non-zero exit means the spawn actually failed.
        """
        deadline = time.monotonic() + SPAWN_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            code = handle.launcher.poll()
            if code is not None and code != 0:
                raise RuntimeError(
                    f"daemon {handle.identity} failed to launch (exit code {code})"
                )

            # use_cache=False is essential: we are polling for an agent that
            # starts *after* the cached presence snapshot was taken, so a
            # cached lookup can never see it.
            agent = self._bridge.get_agent_by_identity(
                handle.identity, use_cache=False
            )
            if agent:
                # Record the pid the moment presence reports it, before the
                # socket check - otherwise a failure between here and connect()
                # leaves close() with no pid and the daemon orphaned.
                handle.pid = int(agent.get("pid") or 0) or handle.pid
                socket_path = self._bridge._socket_path_for_agent(agent)
                if socket_path and Path(socket_path).exists():
                    return str(socket_path)

            await asyncio.sleep(0.25)

        raise TimeoutError(
            f"daemon {handle.identity} did not publish a socket within "
            f"{SPAWN_TIMEOUT_SECONDS}s"
        )

    async def stop(self, session_id: str) -> bool:
        handle = self._daemons.pop(session_id, None)
        if handle is None:
            return False
        await handle.close()
        return True

    async def stop_all(self) -> None:
        for session_id in list(self._daemons):
            try:
                await self.stop(session_id)
            except Exception as e:
                logger.warning("error stopping daemon for %s: %s", session_id, e)


_pool: Optional[DaemonPool] = None


def get_daemon_pool() -> DaemonPool:
    """Return the process-wide pool (created on first use)."""
    global _pool
    if _pool is None:
        _pool = DaemonPool()
    return _pool
