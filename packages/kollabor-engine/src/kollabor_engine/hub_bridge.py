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
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from kollabor_config.config_utils import (
    encode_project_path,
    get_config_directory_candidates,
    get_project_data_dir_candidates,
)

logger = logging.getLogger(__name__)

STALE_THRESHOLD_SECONDS = 60
CACHE_TTL_SECONDS = 5


def _encode_project_path(path: Path) -> str:
    """Match the encoder used for ~/.kollab/projects/<encoded>/."""
    return encode_project_path(path)


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
                    if now - heartbeat > STALE_THRESHOLD_SECONDS:
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

    def get_agent(self, agent_id: str) -> Optional[Dict[str, Any]]:
        """Return a single agent's presence data."""
        for agent in self.get_agents():
            if agent.get("agent_id") == agent_id:
                return agent
        return None

    def get_agent_by_identity(self, identity: str) -> Optional[Dict[str, Any]]:
        """Return a single agent's presence data by identity (e.g. 'koordinator')."""
        for agent in self.get_agents():
            if agent.get("identity") == identity:
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
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(sock_path),
                timeout=timeout,
            )
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

    async def get_agent_output(self, agent_id: str, lines: int = 100) -> Optional[str]:
        """Fetch recent output from an agent via get_output socket action."""
        resp = await self.query_socket(agent_id, "get_output", {"lines": lines})
        if resp and resp.get("type") == "output":
            return str(resp.get("content", ""))
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
        import uuid
        import time as _time

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
        import uuid
        import time as _time

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
