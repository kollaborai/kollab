"""Private, conversation-scoped ACP session bindings.

Bindings are device-local. They contain no credentials and never belong in a
portable profile or the network's sealed configuration.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ACPBinding:
    session_id: str
    workspace: str
    profile: str
    command: tuple[str, ...]
    capabilities: dict[str, Any]

    def matches(self, workspace: str, profile: str, command: tuple[str, ...]) -> bool:
        return (
            self.workspace == os.path.realpath(workspace)
            and self.profile == profile
            and self.command == command
        )


class ACPBindingStore:
    def __init__(self, directory: Path):
        self.directory = directory

    def _path(self, conversation_id: str) -> Path:
        key = hashlib.sha256(conversation_id.encode()).hexdigest()
        return self.directory / f"{key}.json"

    def load(self, conversation_id: str) -> ACPBinding | None:
        try:
            raw = json.loads(self._path(conversation_id).read_text())
            return ACPBinding(
                session_id=str(raw["session_id"]),
                workspace=str(raw["workspace"]),
                profile=str(raw["profile"]),
                command=tuple(raw["command"]),
                capabilities=dict(raw.get("capabilities") or {}),
            )
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def save(self, conversation_id: str, binding: ACPBinding) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(prefix=".binding-", dir=self.directory)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump(asdict(binding), stream, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path(conversation_id))
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


class ACPClientSessionStore:
    """Map a stable inbound ACP session ID to its saved Kollab conversation."""
    def __init__(self):
        self.directory = Path.home() / ".kollab" / "acp" / "clients"

    def _path(self, session_id: str) -> Path:
        return self.directory / (hashlib.sha256(session_id.encode()).hexdigest() + ".json")

    def load(self, session_id: str, workspace: str) -> str | None:
        try:
            record = json.loads(self._path(session_id).read_text())
            return str(record["conversation_id"]) if record["workspace"] == os.path.realpath(workspace) else None
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def save(self, session_id: str, workspace: str, conversation_id: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(prefix=".client-", dir=self.directory)
        try:
            with os.fdopen(fd, "w") as stream:
                json.dump({"workspace": os.path.realpath(workspace), "conversation_id": conversation_id}, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self._path(session_id))
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
