"""Bridge service connecting kollabor-engine to the hub mesh.

Reads agent presence from disk and queries agent unix sockets
to expose hub state through the engine REST API.

Presence files: ~/.kollab/projects/<encoded>/hub/presence/{agent_id}.json
Agent sockets:  /tmp/kollabor-hub/<project-hash>/<identity>.sock  (from socket_path field)
"""

import asyncio
import glob as _glob
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, AsyncIterator, Collection, Dict, List, Optional, Tuple

from kollabor_ai.session_naming import session_display_name
from kollabor_config.config_utils import (
    get_config_directory_candidates,
    get_project_data_dir_candidates,
)

logger = logging.getLogger(__name__)

STALE_THRESHOLD_SECONDS = 60
WAITER_FALLBACK = "Network halted by another machine. Resume needs a person on two machines: Settings → Network."


def waiter_line(socket_path: Path) -> str:
    """What a halt waiter's status socket says, e.g. "Network halted by mac. Resume needs ..."."""
    import socket

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(1.0)
            sock.connect(str(socket_path))
            sock.sendall(b'{"action": "get_status"}\n')
            reply = json.loads(sock.makefile("rb").readline(65536) or b"{}")
        line = reply.get("halted") if isinstance(reply, dict) else None
        return line if isinstance(line, str) and line else WAITER_FALLBACK
    except (OSError, ValueError):
        return WAITER_FALLBACK
CACHE_TTL_SECONDS = 5


def _pid_alive(pid: Any) -> bool:
    """False only for a pid that is known and gone.

    A daemon that crashes or is killed leaves its presence file (and a fresh
    heartbeat) behind for up to STALE_THRESHOLD_SECONDS.
    """
    try:
        pid = int(pid or 0)
    except (TypeError, ValueError):
        return True
    if pid <= 0:
        return True
    if os.name == "nt":  # os.kill(pid, 0) sends Ctrl+C there (#268)
        from kollabor_config import proc_ctl

        return proc_ctl.pid_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _current_project_presence_dir() -> Optional[Path]:
    """Return the presence dir for the current project, if it exists.

    Uses git root or cwd to determine the project, matching the same
    logic as plugins/hub/project_scope.py::resolve_project_root().
    """
    import subprocess

    try:
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=2.0, check=False,
        )
        root = Path(r.stdout.strip()).resolve() if r.returncode == 0 else Path.cwd().resolve()
    except Exception:
        root = Path.cwd().resolve()

    for project_dir in get_project_data_dir_candidates(root):
        d = project_dir / "hub" / "presence"
        if d.is_dir():
            return d
    return None


def _find_presence_dirs() -> List[Path]:
    """Find all active presence directories.

    Returns current project's presence dir first (if resolvable), then
    all other project-scoped dirs, then the legacy global dir.
    This ordering ensures that when multiple projects have an agent with
    the same identity, the current project's agent wins.
    """
    dirs: List[Path] = []

    # Current project first (highest priority)
    current = _current_project_presence_dir()
    if current is not None:
        dirs.append(current)

    # All project-scoped dirs (skip current to avoid duplicate)
    for config_dir in get_config_directory_candidates():
        pattern = str(config_dir / "projects" / "*" / "hub" / "presence")
        for d in sorted(_glob.glob(pattern)):
            p = Path(d)
            if p.is_dir() and p != current:
                dirs.append(p)

    # Legacy global fallback
    for config_dir in get_config_directory_candidates():
        global_presence = config_dir / "hub" / "presence"
        if global_presence.is_dir():
            dirs.append(global_presence)

    return dirs


class HubBridge:
    """Reads hub presence + queries agent sockets on demand."""

    def __init__(self) -> None:
        self._cache: Optional[List[Dict[str, Any]]] = None
        self._cache_ts: float = 0.0

    def _read_presence_files(self) -> List[Dict[str, Any]]:
        """Read all presence files across all project-scoped dirs, filter stale agents."""
        now = time.time()
        agents: List[Dict[str, Any]] = []
        seen: set = set()

        for presence_dir in _find_presence_dirs():
            for f in sorted(presence_dir.glob("*.json")):
                try:
                    data = json.loads(f.read_text())
                    agent_id = data.get("agent_id", f.stem)
                    if agent_id in seen:
                        continue
                    heartbeat = data.get("last_heartbeat", 0)
                    # A halt waiter writes its record once and never beats; its pid is the liveness.
                    if data.get("state") != "halted" and now - heartbeat > STALE_THRESHOLD_SECONDS:
                        continue
                    if not _pid_alive(data.get("pid")):
                        continue
                    seen.add(agent_id)
                    data["alive"] = True
                    agents.append(data)
                except (json.JSONDecodeError, OSError) as e:
                    logger.debug(f"Skipping presence file {f.name}: {e}")

        return agents

    def get_agents(self, use_cache: bool = True) -> List[Dict[str, Any]]:
        """Return list of active agents from presence files.

        Results are cached for CACHE_TTL_SECONDS to avoid disk thrash.
        """
        now = time.time()
        if use_cache and self._cache and (now - self._cache_ts) < CACHE_TTL_SECONDS:
            return self._cache

        agents = self._read_presence_files()
        self._cache = agents
        self._cache_ts = now
        return agents

    def get_agent(
        self, agent_id: str, use_cache: bool = True
    ) -> Optional[Dict[str, Any]]:
        """Return a single agent's presence data."""
        for agent in self.get_agents(use_cache=use_cache):
            if agent.get("agent_id") == agent_id:
                return agent
        return None

    def discover_sessions(self, use_cache: bool = True) -> List[Dict[str, Any]]:
        """Return every live agent on this computer that has an attach socket.

        Terminal sessions, detached daemons and other engines' daemons alike:
        the engine opens one on first use (``EngineSession.adopt``). Presence
        is the discovery contract: only fresh entries with a usable socket are
        exposed. ``session_id`` is the presence agent id, because gem names
        repeat across folders (a koordinator per project).
        """
        sessions: List[Dict[str, Any]] = []
        for agent in self.get_agents(use_cache=use_cache):
            socket_path = self._socket_path_for_agent(agent)
            if not socket_path or not socket_path.exists():
                continue
            identity = str(agent.get("identity") or agent.get("agent_id") or "")
            if not identity:
                continue
            # The conversation its terminal shows ("session: nexus-drift"); the
            # web UI drops the log's timestamp prefix.
            log_name = Path(str(agent.get("session_log") or "")).stem
            tags = agent.get("tags") or {}
            sessions.append({
                "harness": tags.get("harness", ""),
                "provider": tags.get("provider", ""),
                # What the agent publishes, so a row reads the same before the
                # engine opens it as after (EngineSession.to_dict).
                "profile": str(agent.get("profile_name") or ""),
                "model": str(tags.get("model") or ""),
                "acp_waiting": bool(tags.get("acp_waiting", False)),
                "session_id": str(agent.get("agent_id") or identity),
                "name": log_name or session_display_name(identity),
                "identity": identity,
                "agent": agent.get("agent_name") or agent.get("name") or "default",
                "workspace": agent.get("project") or "",
                "daemon_pid": int(agent.get("pid") or 0),
                "socket_path": str(socket_path),
                "session_log": agent.get("session_log") or "",
                "created_at": agent.get("started_at") or 0,
                "active": True,
                "discovered": True,
                "external": True,
                "source": "hub_presence",
            })
            if agent.get("state") == "halted":
                # A halt waiter: no model, its socket only answers status (halt_runtime._StatusSocket).
                sessions[-1]["halted"] = waiter_line(socket_path)
        return sessions

    def get_agent_by_identity(
        self, identity: str, use_cache: bool = True, project: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Return a single agent's presence data by identity (e.g. 'koordinator').

        Pass use_cache=False when polling for an agent that is expected to
        appear: the cached list is a snapshot from up to CACHE_TTL_SECONDS ago
        and will never contain an agent that started after it was taken.
        Gem names repeat across folders (a koordinator per project): pass
        ``project`` to match only the agent running in that folder.
        """
        folder = os.path.realpath(project) if project else None
        for agent in self.get_agents(use_cache=use_cache):
            if agent.get("identity") != identity:
                continue
            if folder and os.path.realpath(str(agent.get("project") or "")) != folder:
                continue
            return agent
        return None

    def _socket_path_for_agent(self, agent_data: Dict[str, Any]) -> Optional[Path]:
        """Return socket path for an agent from its presence data.

        Prefers the socket_path field (written by the agent itself) over
        any derived path — avoids the agent_id vs identity naming mismatch.
        """
        sp = agent_data.get("socket_path")
        if sp:
            return Path(sp)
        # Fallback: identity-named socket in the project socket dir
        identity = agent_data.get("identity")
        if identity:
            # Try to infer project hash from existing sockets
            socks = _glob.glob(f"/tmp/kollabor-hub/**/{identity}.sock", recursive=True)
            if socks:
                return Path(socks[0])
        return None

    async def query_socket(
        self,
        agent_id: str,
        action: str,
        payload: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
    ) -> Optional[Dict[str, Any]]:
        """Send a JSON action to an agent's unix socket and read response.

        Resolves socket path from presence data (uses the socket_path field
        written by the agent, which is identity-named, not agent_id-named).
        Returns parsed response dict, or None on any failure.
        """
        agent_data = self.get_agent(agent_id)
        if agent_data is None:
            logger.debug(f"No presence data for agent {agent_id}")
            return None

        sock_path = self._socket_path_for_agent(agent_data)
        if sock_path is None or not sock_path.exists():
            logger.debug(f"Socket not found for {agent_id}: {sock_path}")
            return None

        return await self._query_socket_path(str(sock_path), action, payload, timeout, agent_id)

    async def query_socket_by_identity(
        self,
        identity: str,
        action: str,
        payload: Optional[Dict[str, Any]] = None,
        timeout: float = 5.0,
    ) -> Optional[Dict[str, Any]]:
        """Send a JSON action to an agent identified by its identity string."""
        agent_data = self.get_agent_by_identity(identity)
        if agent_data is None:
            logger.debug(f"No presence data for identity {identity}")
            return None

        sock_path = self._socket_path_for_agent(agent_data)
        if sock_path is None or not sock_path.exists():
            logger.debug(f"Socket not found for identity {identity}: {sock_path}")
            return None

        return await self._query_socket_path(str(sock_path), action, payload, timeout, identity)

    async def _query_socket_path(
        self,
        sock_path: str,
        action: str,
        payload: Optional[Dict[str, Any]],
        timeout: float,
        label: str,
    ) -> Optional[Dict[str, Any]]:
        """Low-level: open socket, write action, read response."""
        msg = {"action": action}
        if payload:
            msg.update(payload)

        try:
            if os.name == "nt":  # an endpoint file naming a loopback port (#268)
                from kollabor_config import local_ipc

                opening = local_ipc.connect(sock_path)
            else:
                opening = asyncio.open_unix_connection(sock_path)
            reader, writer = await asyncio.wait_for(opening, timeout=timeout)
            try:
                writer.write((json.dumps(msg) + "\n").encode())
                await writer.drain()

                raw = await asyncio.wait_for(reader.readline(), timeout=timeout)
                if not raw:
                    return None

                result: dict[str, Any] = json.loads(raw.decode().strip())
                return result
            finally:
                writer.close()
                await writer.wait_closed()

        except (ConnectionRefusedError, FileNotFoundError):
            logger.debug(f"Agent {label} socket not accepting connections")
            return None
        except asyncio.TimeoutError:
            logger.debug(f"Agent {label} socket query timed out")
            return None
        except Exception as e:
            logger.warning(f"Socket query failed for {label}: {e}")
            return None

    async def get_agent_capture(
        self,
        agent_id: str,
        *,
        capture_format: str = "json",
        lines: int = 100,
        max_chars: Optional[int] = None,
        cursor: Any = None,
    ) -> Optional[Dict[str, Any]]:
        """Fetch an opt-in normalized capture envelope."""
        return await self.query_socket(
            agent_id,
            "get_output",
            {
                "lines": lines,
                "format": capture_format,
                **({"max_chars": max_chars} if max_chars is not None else {}),
                **({"cursor": cursor} if cursor is not None else {}),
            },
        )

    async def get_agent_output(self, agent_id: str, lines: int = 100) -> Optional[str]:
        """Fetch recent output from an agent via get_output socket action."""
        resp = await self.query_socket(agent_id, "get_output", {"lines": lines})
        if resp and resp.get("type") == "output":
            # The socket replies {"type": "output", "lines": [...]} (messenger.py).
            return "\n".join(str(line) for line in resp.get("lines") or [])
        return None

    async def get_agent_status(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Fetch current status from an agent via get_status socket action."""
        resp = await self.query_socket(agent_id, "get_status")
        if resp:
            return resp
        return None

    async def send_message(
        self,
        target_agent_id: str,
        content: str,
        from_identity: str = "api",
    ) -> bool:
        """Send a message to an agent via its unix socket.

        Sets the 'to' field to the agent's identity so TRIGGER_LLM_CONTINUE fires.
        Returns True if ack received, False otherwise.
        """
        import time as _time
        import uuid

        agent_data = self.get_agent(target_agent_id)
        target_identity = agent_data.get("identity", target_agent_id) if agent_data else target_agent_id

        resp = await self.query_socket(
            target_agent_id,
            "message",
            {
                "id": uuid.uuid4().hex,
                "content": content,
                "from_identity": from_identity,
                "to": target_identity,
                "timestamp": _time.time(),
            },
        )
        return resp is not None and resp.get("type") == "ack"

    async def send_message_to_identity(
        self,
        identity: str,
        content: str,
        from_identity: str = "api",
    ) -> bool:
        """Send a message to an agent by identity string (e.g. 'koordinator').

        Sets the 'to' field correctly so TRIGGER_LLM_CONTINUE fires.
        Returns True if ack received, False otherwise.
        """
        import time as _time
        import uuid

        resp = await self.query_socket_by_identity(
            identity,
            "message",
            {
                "id": uuid.uuid4().hex,
                "content": content,
                "from_identity": from_identity,
                "to": identity,
                "timestamp": _time.time(),
            },
        )
        return resp is not None and resp.get("type") == "ack"

    async def ping_agent(self, agent_id: str) -> bool:
        """Ping an agent socket to verify liveness."""
        resp = await self.query_socket(agent_id, "ping")
        return resp is not None and resp.get("type") == "pong"

    async def ping_agent_by_identity(self, identity: str) -> bool:
        """Ping an agent by identity string."""
        resp = await self.query_socket_by_identity(identity, "ping")
        return resp is not None and resp.get("type") == "pong"

    def invalidate_cache(self) -> None:
        """Force next get_agents() call to re-read disk."""
        self._cache = None
        self._cache_ts = 0.0


async def network_daemons(skip: Collection[Any] = ()) -> AsyncIterator[Tuple[Dict[str, Any], Dict[str, Any]]]:
    """Local daemons on a network, each with its ``network_status`` answer.

    Every live agent on this computer but the ones ``skip`` names (session ids
    and pids) is asked over its hub socket, read-only (``kollab --hub status``
    asks the same). All are asked at once, so a busy agent costs its timeout
    once rather than once per agent after it, and the answers come in order,
    this engine's folder first; a caller that stops early cancels the asks
    still out. A daemon on no network is passed.
    """
    from plugins.hub.messenger import AgentMessenger

    home = os.path.realpath(os.getcwd())
    rows = [
        row
        for row in HubBridge().discover_sessions(use_cache=False)
        if row.get("session_id") not in skip and row.get("daemon_pid") not in skip
    ]
    rows.sort(key=lambda row: not row.get("workspace") or os.path.realpath(row["workspace"]) != home)
    asks = [
        asyncio.ensure_future(AgentMessenger.request_network_status(row["socket_path"], timeout=3)) for row in rows
    ]
    try:
        for row, ask in zip(rows, asks):
            status = await ask
            # An older agent sends no network name; its rows still say it is on one.
            if status and (status.get("network") or status.get("agents")):
                yield row, status
    finally:
        for ask in asks:
            ask.cancel()
