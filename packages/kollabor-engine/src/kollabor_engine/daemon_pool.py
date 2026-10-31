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
import math
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import psutil

from .folders import agents_in_folder
from .hub_bridge import HubBridge


def _pid_start_time(pid: int) -> Optional[float]:
    if pid <= 0:
        return None
    try:
        process = psutil.Process(pid)
        if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
            return None
        return float(process.create_time())
    except psutil.Error:
        return None


def _pid_matches_start_time(pid: int, started_at: Optional[float]) -> bool:
    if pid <= 0 or started_at is None:
        return False
    try:
        process = psutil.Process(pid)
        return (
            process.is_running()
            and process.status() != psutil.STATUS_ZOMBIE
            and math.isclose(
                float(process.create_time()), float(started_at), rel_tol=0.0, abs_tol=0.02
            )
        )
    except psutil.Error:
        return False



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
            else f"{tool_type}: {file_path}" if file_path else tool_type
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
    for key in ("ts", "session_id", "request_id", "turn_id", "input_source", "generation"):
        if key in event:
            flat[key] = event[key]
    return flat


class DaemonHandle:
    """One headless kollab daemon, plus the socket the engine drives it over."""

    def __init__(
        self, session_id: str, identity: str, launcher: Optional[subprocess.Popen]
    ):
        self.session_id = session_id
        self.identity = identity
        self.agent_name: str = ""
        # `kollab --detached` double-forks: the process we spawned is only a
        # launcher and exits 0 as soon as the real daemon is detached. The pid
        # that matters comes from hub presence, once the daemon publishes it.
        # None for an agent this pool attached to but did not start
        # (DaemonPool.adopt): closing that handle detaches and never signals it.
        self.launcher = launcher
        # Process-kill authority is independent of the launcher object. A daemon
        # reowned from a WebUI manifest has no Popen launcher, but this engine
        # is now responsible for stopping that same PID on explicit Delete.
        self.owned = launcher is not None
        self.webui_owned = False
        self.pid: int = 0

        self.process_started_at: Optional[float] = None
        # Folder a spawned daemon runs in (its presence ``project``). Gem names
        # repeat across folders, so finding it by identity alone can return
        # another folder's agent.
        self.project: str = ""
        self.socket_path: str = ""
        self.created_at = time.time()

        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._read_task: Optional[asyncio.Task] = None
        self._subscribers: Set[asyncio.Queue] = set()
        self._closed = False
        self.startup_error: str = ""
        # A cancelled turn's tool may ignore the stop signal and report
        # afterwards. Its late result still flows (the transcript mirror
        # follows it) but carries `cancelled: true`, so no gem reads it as a
        # live turn. Only tools running at the cancel are marked: the next
        # turn, whatever starts it (a client, a hub message, a goal), never is.
        self._running_tools: Set[str] = set()
        self._cancelled_tools: Set[str] = set()

        self.rpc: Any = None
        self.state: Any = None

    # === lifecycle ===

    async def connect(self, socket_path: str) -> None:
        """Attach to the daemon's socket and start pumping its event stream."""
        from kollabor.state import RemoteStateService
        from kollabor_rpc import RpcClient, open_unix_connection_with_large_buffer

        self.socket_path = socket_path
        if os.name == "nt":
            # An endpoint file naming a loopback port; local_ipc checks it is ours
            # and runs the handshake (#268).
            from kollabor_config import local_ipc
            from kollabor_rpc.transport import LARGE_BUFFER_LIMIT

            try:
                self._reader, self._writer = await local_ipc.connect(
                    socket_path, limit=LARGE_BUFFER_LIMIT
                )
            except (ConnectionError, TimeoutError, PermissionError) as exc:
                # Not listening yet, reset mid-handshake, or the endpoint file being
                # replaced: the spawn loop retries ConnectionRefusedError until its
                # deadline.
                raise ConnectionRefusedError(str(exc)) from exc
        else:
            self._reader, self._writer = await open_unix_connection_with_large_buffer(
                socket_path
            )

        request = (
            json.dumps(
                {
                    "action": "attach",
                    "mode": "interactive",
                    # The daemon keys subscribers by this id: two engines
                    # attached to one agent must not replace each other.
                    "client_id": f"engine-{os.getpid()}-{self.session_id}",
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

                if message.get("type") == "error" and message.get("phase") == "startup":
                    self.startup_error = str(message.get("message") or "ACP startup failed")
                self._fan_out(_normalize_event(message))
        except asyncio.CancelledError:
            self._fan_out({"type": "daemon_closed", "session_id": self.session_id})
            raise
        except Exception as e:  # any read failure takes the reattach path below
            logger.info("daemon %s stream closed: %s", self.identity, e)
        else:
            logger.info("daemon %s closed the stream", self.identity)
        # A live local daemon that dropped us (its hub socket was re-created) takes
        # us back on the same path; without this every later call is a
        # BrokenPipeError until the engine restarts.
        if not self._closed and self.socket_path and self.pid and self.alive:
            if await self._reattach():
                return
        # Wake every SSE consumer so requests don't hang on a dead daemon.
        self._fan_out({"type": "daemon_closed", "session_id": self.session_id})

    async def _reattach(
        self, attempts: int = 20, delay: float = 0.5, slow_delay: float = 5.0
    ) -> bool:
        """Attach again to the same socket. True once a new read loop runs.

        A daemon that stays alive is retried for as long as it lives: ``attempts``
        quick tries, then one every ``slow_delay`` seconds. Giving up while it
        lived left a handle whose every call failed "client closed" until the
        engine restarted (#541). False only once the handle closes or the
        daemon dies.
        """
        if self.rpc is not None:
            self.rpc.close()  # pending calls fail now instead of hanging
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:
                pass
        tries = 0
        while True:
            await asyncio.sleep(delay if tries < attempts else slow_delay)
            tries += 1
            if self._closed or not self.alive:
                return False
            try:
                await self.connect(self.socket_path)
            except Exception as e:
                if tries == attempts:
                    logger.warning(
                        "daemon %s (pid %s) is alive but not taking the engine back; "
                        "retrying every %ss: %s",
                        self.identity,
                        self.pid,
                        slow_delay,
                        e,
                    )
                else:
                    logger.debug("daemon %s reattach failed: %s", self.identity, e)
                continue
            logger.info("engine session %s reattached to daemon %s", self.session_id, self.identity)
            return True

    def _fan_out(self, event: Dict[str, Any]) -> None:
        event.setdefault("session_id", self.session_id)
        tool_id = event.get("tool_id")
        if tool_id and event.get("type") == "tool_start":
            self._running_tools.add(tool_id)
            self._cancelled_tools.discard(tool_id)  # a new call reusing the id
        elif tool_id and event.get("type") == "tool_result":
            self._running_tools.discard(tool_id)
            if tool_id in self._cancelled_tools:
                self._cancelled_tools.discard(tool_id)
                event = {**event, "cancelled": True}
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning(
                    "dropping event for slow subscriber on session %s", self.session_id
                )

    def mark_cancelled(self) -> None:
        """The turn was cancelled: a result from a tool still running arrives marked."""
        self._cancelled_tools |= self._running_tools
        self._running_tools.clear()

    def fan_out(self, event: Dict[str, Any]) -> None:
        """Engine-generated event to every SSE subscriber of this session."""
        self._fan_out(event)

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    @property
    def alive(self) -> bool:
        """Liveness of the detached daemon, not of the launcher that spawned it.

        An agent on another computer (adopted through the network) has no pid
        here: it is alive while its connection is.
        """
        if not self.pid:
            return (
                self.launcher is None
                and self._read_task is not None
                and not self._read_task.done()
                and not self._closed
            )
        if self.process_started_at is not None:
            return _pid_matches_start_time(self.pid, self.process_started_at)

        if os.name == "nt":  # os.kill(pid, 0) sends Ctrl+C there (#268)
            from kollabor_config import proc_ctl

            return proc_ctl.pid_alive(self.pid)
        try:
            os.kill(self.pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    async def close(self, timeout: float = 10.0, *, stop_process: bool = True) -> None:
        """Close this transport, optionally stop the owned daemon process.

        A WebUI engine shutdown passes ``stop_process=False`` so its explicitly
        persistent daemon survives. Explicit Delete still uses the default.
        """
        process_handle: Optional[psutil.Process] = None

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

        if not stop_process or not self.owned:
            logger.info("detached from %s (session %s)", self.identity, self.session_id)
            return

        # A spawn that failed before presence was read has no pid yet. Look it
        # up one last time so a failed create never leaves a live daemon behind.
        # Only in its own folder: never signal another folder's same-named agent.
        if not self.pid and self.project:
            try:
                from .hub_bridge import HubBridge

                agent = HubBridge().get_agent_by_identity(
                    self.identity, use_cache=False, project=self.project
                )
                if agent:
                    self.pid = int(agent.get("pid") or 0)
                    self.process_started_at = _pid_start_time(self.pid)
            except Exception as e:
                logger.debug("pid lookup during close failed: %s", e)

        # Never signal a recycled PID. Spawn and manifest-reown paths capture
        # creation time; an owned handle without that identity cannot safely kill.
        if self.owned and self.pid and not _pid_matches_start_time(
            self.pid, self.process_started_at
        ):
            logger.warning(
                "not stopping daemon %s: pid %s no longer matches its recorded start time",
                self.identity,
                self.pid,
            )
            return
        if self.owned and self.pid:
            try:
                # psutil's Process handle also checks create time when sending a
                # signal, closing the gap between validation and os.kill(pid).
                process_handle = psutil.Process(self.pid)
            except psutil.Error:
                return


        # The launcher is long gone; signal the detached daemon by pid.
        if self.pid and self.alive:
            if process_handle is None:
                return
            if os.name == "nt":
                await self._stop_windows(process_handle, timeout)
                logger.info(
                    "daemon %s stopped (session %s)", self.identity, self.session_id
                )
                return
            try:
                process_handle.send_signal(signal.SIGTERM)
            except psutil.NoSuchProcess:
                return
            except psutil.Error as exc:
                raise RuntimeError(
                    f"could not signal daemon {self.identity} (pid {self.pid}): {exc}"
                ) from exc

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
                    process_handle.send_signal(signal.SIGKILL)
                except psutil.NoSuchProcess:
                    pass
                except psutil.Error as exc:
                    raise RuntimeError(
                        f"could not kill daemon {self.identity} (pid {self.pid}): {exc}"
                    ) from exc
                kill_deadline = time.monotonic() + 2.0
                while time.monotonic() < kill_deadline and self.alive:
                    await asyncio.sleep(0.05)
                if self.alive:
                    raise RuntimeError(
                        f"daemon {self.identity} (pid {self.pid}) remained alive after SIGKILL"
                    )

        logger.info("daemon %s stopped (session %s)", self.identity, self.session_id)

    async def _stop_windows(
        self, process_handle: psutil.Process, timeout: float
    ) -> None:
        """Ask the daemon to shut down, then end it and every process it started.

        On Windows psutil's SIGTERM is TerminateProcess: the daemon gets no cleanup
        and its children, such as stdio MCP servers, would outlive it (#268).
        """
        try:
            children = process_handle.children(recursive=True)
        except psutil.Error:
            children = []
        if self.socket_path:
            from plugins.hub.messenger import AgentMessenger

            try:
                await AgentMessenger.signal_shutdown(
                    self.socket_path, reason="session closed"
                )
            except Exception as exc:
                logger.debug("shutdown request to %s failed: %s", self.identity, exc)
        deadline = time.monotonic() + timeout
        while self.alive and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        if self.alive:
            logger.warning(
                "daemon %s did not stop on request, ending pid %s",
                self.identity,
                self.pid,
            )
            try:  # and what it started since the snapshot
                children += process_handle.children(recursive=True)
            except psutil.Error:
                pass
        for process in (process_handle, *children):
            try:
                process.kill()  # psutil refuses a pid that was reused
            except psutil.NoSuchProcess:
                pass
            except psutil.Error as exc:
                logger.warning(
                    "could not end pid %s of daemon %s: %s",
                    process.pid,
                    self.identity,
                    exc,
                )
        kill_deadline = time.monotonic() + 2.0
        while time.monotonic() < kill_deadline and self.alive:
            await asyncio.sleep(0.05)
        if self.alive:
            raise RuntimeError(
                f"daemon {self.identity} (pid {self.pid}) remained alive after it was ended"
            )


class DaemonPool:
    """Spawns and tracks one daemon per engine session."""

    def __init__(self) -> None:
        self._daemons: Dict[str, DaemonHandle] = {}
        # (identity, folder) of the daemons still starting, by session id.
        self._starting: Dict[str, tuple] = {}
        self._bridge = HubBridge()
        self._spawn_lock = asyncio.Lock()

    def get(self, session_id: str) -> Optional[DaemonHandle]:
        return self._daemons.get(session_id)

    def owns(self, row: Dict[str, Any]) -> bool:
        """Whether a discovered agent is one of this pool's daemons.

        A daemon publishes presence before the engine attaches to it and before
        its session is registered; until then discovery would list it as an
        agent started elsewhere, a second row a click would open twice.
        """
        pid = int(row.get("daemon_pid") or 0)
        if pid and any(handle.pid == pid for handle in self._daemons.values()):
            return True
        folder = os.path.realpath(str(row.get("workspace") or ""))
        return (str(row.get("identity") or ""), folder) in self._starting.values()

    def all(self) -> List[DaemonHandle]:
        return list(self._daemons.values())

    def starting_folders(self) -> List[str]:
        """Folders with an agent being launched by this engine."""
        return [folder for _identity, folder in self._starting.values()]

    def _assign_identity(
        self, preferred: Optional[str] = None, folder: Optional[str] = None
    ) -> str:
        """Choose a live hub identity using the same pool as the CLI.

        Web sessions used to pin themselves to ``web-<session id>``. That
        bypassed the normal hub roster and made the sidebar show an identity
        that could never be used for agent-to-agent work. Keep allocation in
        the daemon pool, where concurrent browser session creation is already
        serialized, and delegate the actual pool ordering to the hub's
        ``IdentityAssigner``.
        """
        # `roster/name` names a persona; the hub name is the part after the slash.
        requested = (preferred or "").strip().rpartition("/")[2]
        # Every folder has its own hub, a koordinator and every gem each: only
        # an agent in this folder's hub holds a name here.
        here = os.path.realpath(folder or os.getcwd())
        taken = {
            str(agent.get("identity") or "")
            for agent in agents_in_folder(self._bridge.get_agents(use_cache=False), here)
            if agent.get("identity")
        }
        # A stalled agent may miss heartbeat freshness while its process and
        # socket still exist. It still owns its name in this folder.
        from plugins.hub.presence import live_agents_on_machine

        taken.update(
            str(row["identity"])
            for row in agents_in_folder(
                (row for _hub, row in live_agents_on_machine()), here
            )
            if row.get("identity")
        )
        # Presence can be late or temporarily stale. The pool already knows
        # which live processes it owns or has adopted in this hub.
        owned_rows = (
            {"identity": handle.identity, "project": handle.project}
            for handle in self._daemons.values()
            if getattr(handle, "identity", "")
            and getattr(handle, "pid", 0)
            and getattr(handle, "alive", False)
        )
        taken.update(row["identity"] for row in agents_in_folder(owned_rows, here))

        if requested and requested in taken:
            raise ValueError(
                f"hub identity '{requested}' is already in use in {here}; "
                "open it from the sidebar or choose another agent"
            )

        try:
            from plugins.hub.coordinator import IdentityAssigner

            return IdentityAssigner(folder=folder).assign(sorted(taken), requested)
        except Exception as exc:
            logger.warning("hub pool identity assignment failed: %s", exc)

        # Keep a deterministic, pool-shaped fallback for minimal installs
        # where the hub plugin is unavailable. Never recreate the old web-
        # session convention here.
        fallback_names = [
            "lapis",
            "sapphire",
            "aquamarine",
            "zircon",
            "peridot",
            "jasper",
        ]
        for name in fallback_names:
            if name not in taken:
                return name
        for number in range(2, 100):
            for name in fallback_names:
                candidate = f"{name}-{number}"
                if candidate not in taken:
                    return candidate
        return f"agent-{os.getpid()}"

    async def spawn(
        self,
        session_id: str,
        *,
        profile: Optional[str] = None,
        agent: Optional[str] = None,
        identity: Optional[str] = None,
        workspace: Optional[str] = None,
        system_prompt: Optional[str] = None,
        launch_brief: Optional[str] = None,
        user_token: Optional[str] = None,
        solo: bool = False,
        profile_env: Optional[Dict[str, str]] = None,
        stop_on_owner_exit: bool = False,
        webui_owned: bool = False,
    ) -> DaemonHandle:
        """Start a daemon for `session_id` and attach to it.

        Serialized: two daemons racing to claim hub identities at the same
        moment is exactly the collision the hub's spawn guard rejects.
        """
        async with self._spawn_lock:
            existing = await self._live_handle(session_id)
            if existing is not None:
                return existing

            identity_name = self._assign_identity(identity, workspace)
            # Keep `roster/name` so the daemon picks that roster's persona.
            as_arg = identity if identity and identity.endswith("/" + identity_name) else identity_name
            # The kollab this engine runs from: a binary or Kollab.app launcher,
            # else this interpreter. Never whichever `kollab` PATH finds first, or a
            # checkout's engine starts an older install's daemons.
            from kollabor.binary import kollab_argv

            argv = [*kollab_argv(), "--detached", "--as", as_arg]
            if agent:
                argv += ["--agent", agent]
            if profile:
                argv += ["--llm", profile]

            env = dict(os.environ)
            if system_prompt:
                env["KOLLAB_SYSTEM_PROMPT"] = system_prompt
            # Never inherit another agent's brief from the engine's own env.
            env.pop("KOLLAB_LAUNCH_BRIEF", None)
            if launch_brief:
                env["KOLLAB_LAUNCH_BRIEF"] = launch_brief
            # The daemon's MCP layer forwards these to MCP subprocesses (see
            # MCPIntegration's ambient-env fallback); the session id routes UI
            # effects back to this session. Never inherit another identity's
            # token from the engine's own env.
            env["MENTIKO_SESSION_ID"] = session_id
            if user_token:
                env["MENTIKO_SESSION_TOKEN"] = user_token
            else:
                env.pop("MENTIKO_SESSION_TOKEN", None)
            # Solo: keep presence + socket for this pool, stay off the mesh
            # (kollabor.hub_env.hub_solo_by_env).
            if solo:
                env["KOLLAB_HUB_SOLO"] = "1"
            else:
                env.pop("KOLLAB_HUB_SOLO", None)
            # An inline profile (request credentials) exists only in these vars.
            env.update(profile_env or {})
            if stop_on_owner_exit:
                import psutil

                # ACP's stdio process owns this daemon even when a client drops
                # the process before its finally block can run. Ordinary
                # detached/Web sessions keep their independent lifetime.
                env["KOLLAB_PARENT_PID"] = str(os.getpid())
                env["KOLLAB_PARENT_CREATE_TIME"] = str(
                    psutil.Process(os.getpid()).create_time()
                )

            cwd = workspace or os.getcwd()
            if not Path(cwd).is_dir():
                raise ValueError(f"workspace does not exist: {cwd}")

            logger.info("spawning daemon %s for session %s", identity_name, session_id)
            folder = os.path.realpath(cwd)
            self._starting[session_id] = (identity_name, folder)
            try:
                from kollabor_config import proc_ctl

                # Its own session on POSIX; on Windows its own process group, outside
                # the engine's job, so it outlives the engine as a setsid'd daemon does (#268).
                process = proc_ctl.start_child(
                    argv,
                    detached=True,
                    cwd=cwd,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

                handle = DaemonHandle(session_id, identity_name, process)
                handle.webui_owned = webui_owned
                handle.project = folder
                try:
                    deadline = time.monotonic() + SPAWN_TIMEOUT_SECONDS
                    while True:
                        socket_path = await self._await_socket(handle)
                        try:
                            await handle.connect(socket_path)
                            break
                        except (ConnectionRefusedError, FileNotFoundError):
                            # The socket file can predate the listener: a dead
                            # daemon of the same gem leaves it behind, and the
                            # new one publishes presence before it binds.
                            if time.monotonic() >= deadline:
                                raise
                            await asyncio.sleep(0.25)
                except BaseException:
                    try:
                        await handle.close()
                    except BaseException as cleanup_error:
                        logger.warning("failed daemon startup cleanup (%s)", type(cleanup_error).__name__)
                    raise

                self._daemons[session_id] = handle
                return handle
            finally:
                self._starting.pop(session_id, None)

    async def adopt(
        self,
        session_id: str,
        found: Dict[str, Any],
        *,
        owned: bool = False,
        webui_owned: bool = False,
    ) -> DaemonHandle:
        """Attach to an existing daemon, optionally reowning a saved WebUI PID.

        Ordinary discovery defaults to unowned/detach-only. A validated
        WebUI-manifest reown explicitly sets process-kill authority so Delete
        can stop the same PID after the engine restarts.
        """
        async with self._spawn_lock:
            existing = await self._live_handle(session_id)
            if existing is not None:
                if owned:
                    expected_pid = int(found.get("daemon_pid") or found.get("pid") or 0)
                    stored_start_time = found.get("daemon_started_at")
                    if not expected_pid or (existing.pid and existing.pid != expected_pid):
                        raise RuntimeError(
                            f"cannot reown session {session_id}: daemon PID does not match manifest"
                        )
                    expected_start = (
                        float(stored_start_time)
                        if isinstance(stored_start_time, (int, float))
                        else existing.process_started_at or _pid_start_time(expected_pid)
                    )
                    if not _pid_matches_start_time(expected_pid, expected_start):
                        raise RuntimeError(
                            f"cannot reown session {session_id}: daemon process identity changed"
                        )
                    existing.pid = expected_pid
                    existing.process_started_at = expected_start
                    existing.owned = True
                    existing.webui_owned = webui_owned
                return existing

            handle = DaemonHandle(session_id, str(found.get("identity") or ""), None)
            handle.owned = owned
            handle.webui_owned = webui_owned
            handle.pid = int(found.get("daemon_pid") or found.get("pid") or 0)

            stored_start_time = found.get("daemon_started_at")
            handle.process_started_at = (
                float(stored_start_time)
                if isinstance(stored_start_time, (int, float))
                else _pid_start_time(handle.pid)
            )
            handle.project = str(found.get("workspace") or found.get("project") or "")
            handle.agent_name = str(found.get("agent") or "")
            try:
                await handle.connect(str(found.get("socket_path") or ""))
            except BaseException:
                try:
                    # Attach failure is not an explicit Delete or /quit request.
                    await handle.close(stop_process=False)
                except BaseException as cleanup_error:
                    logger.warning("failed daemon attach cleanup (%s)", type(cleanup_error).__name__)
                raise
            self._daemons[session_id] = handle
            return handle

    async def _live_handle(self, session_id: str) -> Optional[DaemonHandle]:
        """The session's handle if its daemon is alive; a dead one is closed."""
        existing = self._daemons.get(session_id)
        if existing is None or existing.alive:
            return existing

        # A daemon can die outside this pool (for example after a Hub stop or
        # a crashed detached process). Do not overwrite a dead handle while its
        # reader/socket resources are still attached; close it before claiming
        # the session again.
        self._daemons.pop(session_id, None)
        try:
            await existing.close()
        except Exception as e:
            logger.debug("stale daemon handle cleanup failed for %s: %s", session_id, e)
        return None

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
                handle.identity, use_cache=False, project=handle.project
            )
            if agent:
                # Record the pid the moment presence reports it, before the
                # socket check - otherwise a failure between here and connect()
                # leaves close() with no pid and the daemon orphaned.
                handle.pid = int(agent.get("pid") or 0) or handle.pid

                handle.process_started_at = _pid_start_time(handle.pid)
                handle.agent_name = str(
                    agent.get("agent_name") or agent.get("name") or ""
                )
                socket_path = self._bridge._socket_path_for_agent(agent)
                if socket_path and Path(socket_path).exists():
                    return str(socket_path)

            await asyncio.sleep(0.25)

        raise TimeoutError(
            f"daemon {handle.identity} did not publish a socket within "
            f"{SPAWN_TIMEOUT_SECONDS}s"
        )

    async def detach(self, session_id: str) -> bool:
        """Close this engine's transport without signaling or killing the PID."""
        async with self._spawn_lock:
            handle = self._daemons.get(session_id)
            if handle is None:
                return False
            try:
                await handle.close(stop_process=False)
            except BaseException:
                handle._closed = False
                raise
            if self._daemons.get(session_id) is handle:
                self._daemons.pop(session_id)
            return True


    async def stop(self, session_id: str) -> bool:
        async with self._spawn_lock:
            handle = self._daemons.get(session_id)
            if handle is None:
                return False
            try:
                await handle.close()
            except BaseException:
                # Keep the owner reachable for a later EOF/shutdown retry.
                # close() reserves its closed flag before awaited cleanup.
                handle._closed = False
                raise
            if self._daemons.get(session_id) is handle:
                self._daemons.pop(session_id)
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
