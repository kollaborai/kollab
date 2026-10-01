"""Silent machine-wide view of the existing per-workspace Hub presence files.

``agents()`` is a local-only directory. It never opens a messaging connection,
starts a model turn, or authorizes a peer. PresenceManager remains the sole
heartbeat writer and cleanup owner; dead/stale entries are pruned from this view,
not deleted from another session's state. No second agent registry is created.

``publishable_agents(workspace, workspace_id)`` is the only remote export: it
requires an explicit workspace, filters before export, and strips local paths,
socket routes, PIDs, task text, logs, and profile information. A caller still
must authorize the peer before transmitting that roster or accepting messages.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import secrets
import stat
import time
from contextlib import ExitStack
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterator

from kollabor_config.config_utils import encode_project_path, get_config_directory

from .device_names import default_device_name

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_HEX_ID = re.compile(r"[0-9a-f]{32}\Z")
_STATES = frozenset(
    {
        "booting",
        "ready",
        "working",
        "thinking",
        "blocked",
        "dreaming",
        "suspended",
        "idle",
        "waiting",
        "connecting",
        "registered",
    }
)
_MAX_FILE = 65536


class LocalDirectoryError(ValueError):
    """Unsafe or invalid local directory state; contains no private file data."""


def _directory_fd(path: str | Path, *, parent: int | None = None) -> int:
    if parent is None:
        absolute = Path(path).absolute()
        # /tmp and /var are root-owned platform aliases on macOS. Runtime
        # symlinks owned by a user are not valid discovery roots or ancestors.
        for component in (absolute, *absolute.parents):
            info = component.lstat()
            if stat.S_ISLNK(info.st_mode) and (component == absolute or info.st_uid != 0):
                raise LocalDirectoryError("local runtime paths must not contain user symlinks")
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    info = os.fstat(fd)
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o022:
        os.close(fd)
        raise LocalDirectoryError("local directory must be owned by this user and not writable by others")
    return fd


def _read_file(parent: int, name: str, *, limit: int = _MAX_FILE, single_link: bool = True) -> bytes:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or (single_link and info.st_nlink != 1)
        ):
            raise LocalDirectoryError("local state files must be private regular files owned by this user")
        raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise LocalDirectoryError("local state file exceeds size limit")
        return raw


def _json_file(parent: int, name: str) -> dict:
    def unique(items):
        data = {}
        for key, value in items:
            if key in data:
                raise LocalDirectoryError("duplicate local state field")
            data[key] = value
        return data

    value = json.loads(_read_file(parent, name), object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise LocalDirectoryError("local state object required")
    return value


@dataclass(frozen=True)
class LocalAgent:
    machine_id: str
    workspace_id: str
    agent_id: str
    name: str
    is_coordinator: bool
    state: str
    workspace: str
    workspace_label: str
    socket_path: str
    pid: int
    last_heartbeat: float

    @property
    def global_id(self) -> str:
        return f"local:{self.machine_id}:{self.workspace_id}:{self.agent_id}"

    def to_dict(self) -> dict:
        """Local-only output. Never send this dictionary over the network."""
        return {**asdict(self), "global_id": self.global_id, "online": True}


class LocalAgentDirectory:
    """Bounded, no-network adapter over Hub's existing presence records.

    Existing Hub heartbeat files provide live state. The only new persistent
    value is ``~/.kollab/machine-id``: a random identifier scoped to this OS user,
    created atomically with mode 0600, without reading credentials or device keys.
    ``truncated`` reports that the bounded scan did not cover the whole roster.
    Directory work is synchronous file I/O; async callers can use ``to_thread``.
    """

    def __init__(
        self,
        config_dir: Path | None = None,
        *,
        socket_dir: Path | None = None,
        max_entries: int = 4096,
        max_projects: int = 1024,
        stale_after: float = 60.0,
    ):
        if not 1 <= max_entries <= 4096 or not 1 <= max_projects <= 1024:
            raise ValueError("directory bounds exceed supported limits")
        if not 1 <= stale_after <= 300:
            raise ValueError("heartbeat expiry must be between 1 and 300 seconds")
        self.config_dir = Path(config_dir or get_config_directory()).absolute()
        self.socket_dir = Path(socket_dir or "/tmp/kollabor-hub").absolute()
        self.max_entries = max_entries
        self.max_projects = max_projects
        self.stale_after = stale_after
        self.truncated = False
        self.config_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Shared /tmp path space: refuse a socket dir another user owns
        # (validated only; a read-side view never creates it).
        from .presence import secure_socket_dir

        try:
            secure_socket_dir(self.socket_dir, create=False)
        except RuntimeError as exc:
            raise LocalDirectoryError(str(exc)) from None
        root = _directory_fd(self.config_dir)
        try:
            self.machine_id = self._machine_id(root)
        finally:
            os.close(root)

    @staticmethod
    def _machine_id(root: int) -> str:
        try:
            value = _read_file(root, "machine-id", limit=64, single_link=False).decode("ascii").strip()
        except FileNotFoundError:
            # Link an already fsynced private file so concurrent readers never
            # observe a partially written identifier. Never overwrite an ID.
            name = f".machine-id-{secrets.token_hex(12)}"
            fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=root)
            try:
                with os.fdopen(fd, "w") as stream:
                    stream.write(secrets.token_hex(16) + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    os.link(name, "machine-id", src_dir_fd=root, dst_dir_fd=root, follow_symlinks=False)
                except FileExistsError:
                    pass
            finally:
                os.unlink(name, dir_fd=root)
            # The temporary hard link is removed before validation/read.
            value = _read_file(root, "machine-id", limit=64, single_link=False).decode("ascii").strip()
        if not _HEX_ID.fullmatch(value):
            raise LocalDirectoryError("invalid local machine identity")
        return value

    def _presence_dirs(self, root: int, workspace: Path | None) -> Iterator[tuple[int, str | None]]:
        """Yield open directory descriptors, never following runtime symlinks."""
        try:
            projects = _directory_fd("projects", parent=root)
        except (OSError, LocalDirectoryError):
            projects = None
        if projects is not None:
            try:
                with ExitStack() as stack:
                    if workspace is not None:
                        names = iter([encode_project_path(workspace)])
                    else:
                        entries = stack.enter_context(os.scandir(projects))
                        names = (entry.name for entry in entries)
                    for number, name in enumerate(names):
                        if number >= self.max_projects:
                            self.truncated = True
                            break
                        yield from self._presence_path(projects, (name, "hub", "presence"), name)
            finally:
                os.close(projects)
        # The configured global-Hub mode uses the same payloads; still filter
        # each payload by its exact workspace when publishing remotely.
        yield from self._presence_path(root, ("hub", "presence"), None)

    @staticmethod
    def _presence_path(parent: int, parts: tuple[str, ...], project_name: str | None):
        opened = []
        try:
            for part in parts:
                parent = _directory_fd(part, parent=parent)
                opened.append(parent)
            if stat.S_IMODE(os.fstat(parent).st_mode) & 0o077:
                return
            yield parent, project_name
        except (OSError, LocalDirectoryError):
            return
        finally:
            for fd in reversed(opened):
                os.close(fd)

    def _workspace_id(self, root: int, workspace: Path) -> str:
        digest = hashlib.sha256(str(workspace).encode()).hexdigest()
        opened = []
        try:
            for part in ("network", digest):
                root = _directory_fd(part, parent=root)
                opened.append(root)
            value = _json_file(root, "state.json").get("workspace_id")
            if isinstance(value, str) and _HEX_ID.fullmatch(value):
                return value
        except (OSError, ValueError, UnicodeError, RecursionError):
            pass
        finally:
            for fd in reversed(opened):
                os.close(fd)
        # Stable offline identifier without creating networking credentials.
        # Once connected, the existing relay workspace identity takes precedence.
        return hashlib.sha256(f"{self.machine_id}:{workspace}".encode()).hexdigest()[:32]

    def _socket_valid(self, value: str) -> bool:
        opened = []
        try:
            path = Path(value)
            if not path.is_absolute() or ".." in path.parts:
                return False
            relative = path.relative_to(self.socket_dir)
            if not 1 <= len(relative.parts) <= 2:
                return False
            root = _directory_fd(self.socket_dir)
            opened.append(root)
            for part in relative.parts[:-1]:
                root = _directory_fd(part, parent=root)
                opened.append(root)
            info = os.stat(relative.name, dir_fd=root, follow_symlinks=False)
            return stat.S_ISSOCK(info.st_mode) and info.st_uid == os.getuid() and not stat.S_IMODE(info.st_mode) & 0o077
        except (OSError, ValueError):
            return False
        finally:
            for fd in reversed(opened):
                os.close(fd)

    def _record(
        self, root: int, data: dict, filename: str, project_name: str | None, workspace: Path | None, now: float
    ) -> LocalAgent | None:
        agent_id = data.get("agent_id")
        project = data.get("project")
        pid = data.get("pid")
        heartbeat = data.get("last_heartbeat")
        if (
            not isinstance(agent_id, str)
            or not _ID.fullmatch(agent_id)
            or filename != f"{agent_id}.json"
            or not isinstance(project, str)
            or not project
            or len(project) > 4096
            or not Path(project).is_absolute()
            or ".." in Path(project).parts
            or type(pid) is not int
            or not 0 < pid <= 2**31 - 1
            or type(heartbeat) not in (int, float)
            or not 0 <= heartbeat <= 2**53
            or not math.isfinite(heartbeat)
            or not -5 <= now - heartbeat <= self.stale_after
        ):
            return None
        actual_workspace = Path(project).resolve()
        if workspace is not None and actual_workspace != workspace:
            return None
        if project_name is not None and encode_project_path(actual_workspace) != project_name:
            return None
        try:
            os.kill(pid, 0)
        except OSError:
            return None
        socket_path = data.get("socket_path")
        if not isinstance(socket_path, str) or not self._socket_valid(socket_path):
            return None
        name = data.get("identity") or data.get("agent_name") or agent_id
        state = data.get("state")
        if (
            not isinstance(name, str)
            or not 1 <= len(name) <= 128
            or not name.isprintable()
            or state not in _STATES
            or type(data.get("is_coordinator")) is not bool
        ):
            return None
        return LocalAgent(
            self.machine_id,
            self._workspace_id(root, actual_workspace),
            agent_id,
            name,
            data["is_coordinator"],
            state,
            str(actual_workspace),
            actual_workspace.name,
            socket_path,
            pid,
            heartbeat,
        )

    def agents(self, workspace: Path | None = None) -> list[LocalAgent]:
        """Read live records across workspaces, without messaging any agent."""
        selected = Path(workspace).resolve() if workspace is not None else None
        self.truncated = False
        root = _directory_fd(self.config_dir)
        result: dict[str, LocalAgent] = {}
        scanned = 0
        now = time.time()
        try:
            for directory, project_name in self._presence_dirs(root, selected):
                with os.scandir(directory) as entries:
                    for entry in entries:
                        scanned += 1
                        if scanned > self.max_entries:
                            self.truncated = True
                            return list(result.values())
                        if not entry.name.endswith(".json"):
                            continue
                        try:
                            row = self._record(
                                root, _json_file(directory, entry.name), entry.name, project_name, selected, now
                            )
                        except (OSError, ValueError, TypeError, UnicodeError, RecursionError):
                            continue
                        if row is not None:
                            result[row.global_id] = row
            return list(result.values())
        finally:
            os.close(root)

    def publishable_agents(
        self, workspace: Path, workspace_id: str, device_name: str | None = None
    ) -> list[dict]:
        """Explicitly scoped remote-safe roster; caller must enforce peer grants.

        ``workspace_id`` must be the active relay client's authenticated workspace
        ID. Requiring it prevents use of an offline fallback in remote routing.
        The session's agent_id is opaque, not a display name or local socket path.
        ``device_name`` is this device's human name (agent-network-simple-flow.md
        §4); when omitted it falls back to the derived default for ``workspace``.
        """
        if not isinstance(workspace_id, str) or not _HEX_ID.fullmatch(workspace_id):
            raise LocalDirectoryError("an active relay workspace identity is required")
        if workspace is None:
            raise LocalDirectoryError("an explicit workspace is required for remote publication")
        device = device_name or default_device_name(workspace)
        return [
            {
                "machine_id": item.machine_id,
                "workspace_id": workspace_id,
                "agent_id": item.agent_id,
                "name": item.name,
                "is_coordinator": item.is_coordinator,
                "state": item.state,
                "device": device,
            }
            for item in self.agents(workspace)
        ]
