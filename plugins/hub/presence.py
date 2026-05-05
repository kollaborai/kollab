"""Presence system - heartbeat files and agent discovery."""

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Dict, List, Optional

from kollabor_agent.runtime import AgentRuntime

logger = logging.getLogger(__name__)


def get_hub_dir() -> Path:
    """Get the hub directory, creating if needed.

    When KOLLAB_HUB_PROJECT_SCOPED is set, state siloes under
    ~/.kollab/projects/<encoded>/hub/ so agents launched from
    different repos stay invisible to each other. Default is global
    at ~/.kollab/hub/ for backward compatibility with any agent
    process that was running before this flag existed.
    """
    from .project_scope import get_project_hub_dir, is_project_scoped
    from kollabor_config.config_utils import get_config_directory

    if is_project_scoped():
        return get_project_hub_dir()

    hub_dir = get_config_directory() / "hub"
    hub_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    return hub_dir


def get_presence_dir() -> Path:
    """Get the presence directory."""
    d = get_hub_dir() / "presence"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def get_messages_dir() -> Path:
    """Get the messages directory."""
    d = get_hub_dir() / "messages"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def get_socket_dir() -> Path:
    """Get the socket directory in /tmp (short paths for unix sockets).

    When project-scoped, sockets live under a per-project subdir keyed
    by a short hash of the project id (full project id would blow the
    104-byte unix socket path limit on macOS).
    """
    from .project_scope import get_project_socket_key, is_project_scoped

    if is_project_scoped():
        d = Path("/tmp/kollabor-hub") / get_project_socket_key()
    else:
        d = Path("/tmp/kollabor-hub")
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def _atomic_write(path: Path, data: dict) -> None:
    """Write JSON atomically via temp file + rename."""
    tmp = path.with_suffix(".tmp")
    try:
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        tmp.rename(path)
    except Exception as e:
        logger.error(f"Atomic write failed for {path}: {e}")
        if tmp.exists():
            tmp.unlink(missing_ok=True)


class PresenceManager:
    """Manages this agent's presence file and discovers other agents."""

    def __init__(self, identity: "AgentRuntime"):
        self.identity = identity
        self._presence_dir = get_presence_dir()
        self._presence_file = self._presence_dir / f"{identity.agent_id}.json"
        # Cached roster -- updated by discover_agents_async() in the
        # heartbeat loop.  Sync callers read from cache to avoid
        # blocking the event loop with socket checks.
        self._cached_agents: List[AgentRuntime] = []
        self._cache_time: float = 0.0

    def publish(self) -> None:
        """Write/update this agent's presence file."""
        self.identity.last_heartbeat = time.time()
        _atomic_write(self._presence_file, self.identity.to_presence_dict())

    def heartbeat(self) -> None:
        """Update heartbeat timestamp."""
        self.identity.last_heartbeat = time.time()
        _atomic_write(self._presence_file, self.identity.to_presence_dict())

    def remove(self) -> None:
        """Remove this agent's presence file (graceful shutdown)."""
        try:
            self._presence_file.unlink(missing_ok=True)
            logger.info(
                f"Removed presence file for {self.identity.identity}"
            )  # external attr
        except Exception as e:
            logger.error(f"Failed to remove presence: {e}")

    def discover_agents(self, include_self: bool = False) -> List[AgentRuntime]:
        """Scan presence directory for live agents.

        Validates PID liveness AND socket connectivity.
        Both must pass - a recycled PID with a dead socket is stale.
        """
        agents = []
        for f in self._presence_dir.glob("*.json"):
            if not include_self and f.stem == self.identity.agent_id:
                continue
            try:
                with open(f) as fh:
                    data = json.load(fh)
                agent = AgentRuntime.from_presence_dict(data)

                if not agent.is_alive():
                    logger.info(f"Cleaning dead agent: {f.stem} (pid {agent.pid})")
                    f.unlink(missing_ok=True)
                    self._cleanup_agent_socket(agent)
                    continue

                # Trust fresh heartbeats -- skip expensive socket check.
                # Only verify socket when heartbeat is stale (agent may
                # have crashed without removing presence).
                heartbeat_age = time.time() - (agent.last_heartbeat or 0)
                if heartbeat_age < 15:
                    agents.append(agent)
                    continue

                # Stale heartbeat -- verify socket is actually responding
                if agent.socket_path and not self._socket_responds(agent.socket_path):
                    logger.info(
                        f"Cleaning stale agent: {f.stem} (pid alive but socket dead)"
                    )
                    f.unlink(missing_ok=True)
                    self._cleanup_agent_socket(agent)
                    continue

                agents.append(agent)
            except (json.JSONDecodeError, Exception) as e:
                logger.debug(f"Bad presence file {f}: {e}")
        return agents

    async def discover_agents_async(
        self, include_self: bool = False
    ) -> List[AgentRuntime]:
        """Async version of discover_agents.

        Uses non-blocking socket checks so the event loop stays
        responsive.  Updates the internal cache so sync callers
        (prompt renderer, status widgets) get fresh data for free.
        """
        agents: List[AgentRuntime] = []
        for f in self._presence_dir.glob("*.json"):
            if not include_self and f.stem == self.identity.agent_id:
                continue
            try:
                with open(f) as fh:
                    data = json.load(fh)
                agent = AgentRuntime.from_presence_dict(data)

                if not agent.is_alive():
                    logger.info(f"Cleaning dead agent: {f.stem} (pid {agent.pid})")
                    f.unlink(missing_ok=True)
                    self._cleanup_agent_socket(agent)
                    continue

                # Trust fresh heartbeats
                heartbeat_age = time.time() - (agent.last_heartbeat or 0)
                if heartbeat_age < 15:
                    agents.append(agent)
                    continue

                # Stale heartbeat -- async socket check (non-blocking)
                if agent.socket_path:
                    alive = await self._socket_responds_async(agent.socket_path)
                    if not alive:
                        logger.info(
                            f"Cleaning stale agent: {f.stem} "
                            f"(pid alive but socket dead)"
                        )
                        f.unlink(missing_ok=True)
                        self._cleanup_agent_socket(agent)
                        continue

                agents.append(agent)
            except (json.JSONDecodeError, Exception) as e:
                logger.debug(f"Bad presence file {f}: {e}")

        self._cached_agents = agents
        self._cache_time = time.time()
        return agents

    def get_cached_agents(self) -> List[AgentRuntime]:
        """Return the last-known agent list without any I/O.

        Safe to call from sync contexts (prompt renderer, status
        widgets).  Returns empty list until the first async
        discover completes.
        """
        return list(self._cached_agents)

    @staticmethod
    async def _socket_responds_async(socket_path: str, timeout: float = 1.0) -> bool:
        """Non-blocking socket liveness check."""
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(socket_path),
                timeout=timeout,
            )
            writer.close()
            await writer.wait_closed()
            return True
        except Exception:
            return False

    def scan_all_presence(self, include_self: bool = False) -> List[AgentRuntime]:
        """Read all presence files without cleaning up dead ones.

        Used by stop command so it can attempt shutdown signals even
        on agents that are dying or have stale sockets.  The caller
        is responsible for cleanup after the operation.
        """
        agents = []
        for f in self._presence_dir.glob("*.json"):
            if not include_self and f.stem == self.identity.agent_id:
                continue
            try:
                with open(f) as fh:
                    data = json.load(fh)
                agents.append(AgentRuntime.from_presence_dict(data))
            except (json.JSONDecodeError, Exception) as e:
                logger.debug(f"Bad presence file {f}: {e}")
        return agents

    @staticmethod
    def _socket_responds(socket_path: str) -> bool:
        """Quick synchronous check if a socket file exists and is connectable."""
        import socket as sock_mod

        if not Path(socket_path).exists():
            return False
        s = sock_mod.socket(sock_mod.AF_UNIX, sock_mod.SOCK_STREAM)
        try:
            s.settimeout(1.0)
            s.connect(socket_path)
            return True
        except (ConnectionRefusedError, OSError, sock_mod.timeout):
            return False
        finally:
            s.close()

    @staticmethod
    def _cleanup_agent_socket(agent: "AgentRuntime") -> None:
        """Remove a dead agent's socket file."""
        if agent.socket_path:
            try:
                Path(agent.socket_path).unlink(missing_ok=True)
            except Exception:
                pass

    def cleanup_agent(self, agent: "AgentRuntime") -> None:
        """Remove an agent's presence file and socket.

        Public method for callers (like hub stop) that need to
        explicitly clean up an agent they know is dead.
        """
        pf = self._presence_dir / f"{agent.agent_id}.json"
        pf.unlink(missing_ok=True)
        self._cleanup_agent_socket(agent)

    def get_agent_by_identity(self, identity: str) -> Optional[AgentRuntime]:
        """Find an agent by its identity."""
        for agent in self.discover_agents():
            if agent.identity == identity:  # external attr
                return agent
        return None

    def get_project_agents(self, project: Optional[str] = None) -> List[AgentRuntime]:
        """Get all agents working in the same project directory."""
        project = project or self.identity.project
        return [a for a in self.discover_agents() if a.project == project]

    def startup_scan(self) -> int:
        """Eagerly scan and prune dead presence files on startup.

        Called once before the heartbeat loop starts.  Reuses the
        same pid/socket liveness checks as discover_agents but does
        NOT add survivors to any list — it's purely cleanup.

        Returns count of dead presence files removed.
        """
        removed = 0
        for f in self._presence_dir.glob("*.json"):
            try:
                with open(f) as fh:
                    data = json.load(fh)
                agent = AgentRuntime.from_presence_dict(data)

                if not agent.is_alive():
                    logger.info(
                        f"Startup cleanup: dead agent {f.stem} (pid {agent.pid})"
                    )
                    f.unlink(missing_ok=True)
                    self._cleanup_agent_socket(agent)
                    removed += 1
                    continue

                # Also check socket for stale heartbeats
                heartbeat_age = time.time() - (agent.last_heartbeat or 0)
                if heartbeat_age > 15 and agent.socket_path:
                    if not self._socket_responds(agent.socket_path):
                        logger.info(
                            f"Startup cleanup: stale agent {f.stem} "
                            f"(pid alive, socket dead)"
                        )
                        f.unlink(missing_ok=True)
                        self._cleanup_agent_socket(agent)
                        removed += 1
            except (json.JSONDecodeError, Exception) as e:
                logger.debug(f"Bad presence file during startup scan {f}: {e}")

        if removed:
            logger.info(f"Startup scan removed {removed} dead presence file(s)")
        return removed

    @staticmethod
    def cleanup_stale_sockets() -> int:
        """Remove socket files that have no matching presence file.

        Called on startup to clear orphans left by ungraceful deaths.
        Skips sockets younger than 10s to avoid racing with agents
        that are still writing their presence file.
        Returns count of sockets removed.
        """
        socket_dir = get_socket_dir()
        presence_dir = get_presence_dir()

        # Build set of socket paths referenced by live presence files.
        # Sockets can be named by agent_id OR identity, so we must
        # read the actual socket_path field from each presence file.
        live_socket_names: set[str] = set()
        for pf in presence_dir.glob("*.json"):
            try:
                with open(pf) as fh:
                    data = json.load(fh)
                sp = data.get("socket_path", "")
                if sp:
                    live_socket_names.add(Path(sp).name)
            except Exception:
                pass
            # Also keep the agent_id stem as a fallback for old-style names
            live_socket_names.add(f"{pf.stem}.sock")

        now = time.time()
        removed = 0
        for sock in socket_dir.glob("*.sock"):
            if sock.name in live_socket_names:
                continue
            # Skip very recent sockets -- agent may still be starting
            try:
                age = now - sock.stat().st_mtime
                if age < 10:
                    continue
            except OSError:
                continue
            try:
                sock.unlink(missing_ok=True)
                removed += 1
            except Exception:
                pass
        if removed:
            logger.info(f"Cleaned {removed} orphan socket(s)")
        return removed

    def get_roster_summary(self) -> List[Dict]:
        """Get a summary of all agents for system prompt injection.

        Uses cached agents to avoid blocking on socket checks.
        Cache is refreshed by discover_agents_async in heartbeat loop.
        """
        agents = self._cached_agents if self._cached_agents else self.discover_agents()
        return [
            {
                "identity": a.identity,  # external attr
                "agent_name": getattr(a, "agent_name", "") or a.identity,
                "state": a.state,
                "project": a.project,
                "current_task": a.current_task,
                "is_coordinator": a.is_coordinator,
            }
            for a in agents
        ]
