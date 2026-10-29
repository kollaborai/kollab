"""Durable ownership records for web-UI-owned agent daemons.

The engine is a disposable socket/API process; the daemon is the conversation
process. This module stores only enough identity metadata to reattach the same
local process after an engine restart. It deliberately does not persist profile
credentials, conversation contents, or arbitrary environment values.
"""

from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import psutil

from kollabor_config.config_utils import get_config_directory
from kollabor_config.file_lock import try_lock, unlock

logger = logging.getLogger(__name__)

MANIFEST_VERSION = 1
WEBUI_SOURCE = "webui"


class HandoverManifestError(RuntimeError):
    """The port-scoped agent ownership record is invalid or unsafe to claim."""


def _process_started_at(pid: int) -> Optional[float]:
    if pid <= 0:
        return None
    try:
        process = psutil.Process(pid)
        if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
            return None
        return float(process.create_time())
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return None


def process_matches(pid: int, started_at: float) -> bool:
    """True only when a live PID still has the recorded process identity."""
    current = _process_started_at(pid)
    return current is not None and math.isclose(
        current, float(started_at), rel_tol=0.0, abs_tol=0.02
    )


def _safe_profile(profile: Any) -> Dict[str, str]:
    """Return display-only profile identity; never persist endpoints or secrets."""
    result: Dict[str, str] = {}
    for field in ("name", "provider", "model"):
        value = getattr(profile, field, None)
        if isinstance(value, str) and value:
            result[field] = value
    return result


def _normalized_path(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return ""
    try:
        return os.path.realpath(os.path.expanduser(value))
    except (OSError, ValueError):
        return value


class HandoverStore:
    """Atomic, private manifest for WebUI-owned agents on one engine port.

    A separate lock file prevents two engine instances for the same port from
    claiming records concurrently. The owner PID *and* process creation time
    defend against PID reuse; each daemon record repeats that identity check.
    """

    def __init__(
        self,
        port: int,
        *,
        config_dir: Optional[Path] = None,
        path: Optional[Path] = None,
        owner_pid: Optional[int] = None,
        owner_started_at: Optional[float] = None,
    ) -> None:
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError("engine port must be between 1 and 65535")
        root = Path(config_dir) if config_dir is not None else get_config_directory()
        self.port = port
        self.path = Path(path) if path is not None else root / "engine" / f"webui-{port}.json"
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.owner_pid = int(owner_pid if owner_pid is not None else os.getpid())
        detected_started_at = _process_started_at(self.owner_pid)
        self.owner_started_at = float(
            owner_started_at if owner_started_at is not None else detected_started_at or time.time()
        )
        self._lock_file: Any = None
        self._data: Optional[Dict[str, Any]] = None
        self._acquired = False

    @property
    def enabled(self) -> bool:
        return True

    def acquire(self) -> List[Dict[str, Any]]:
        """Claim this port's manifest and return records left by the old engine.

        An active engine or malformed manifest is a hard error: silently
        replacing either could orphan an agent or let two engines own one PID.
        """
        if self._acquired:
            return [dict(row) for row in self._data["sessions"]]

        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)

        if os.name != "nt":
            self.path.parent.chmod(0o700)
        self._acquire_file_lock()
        try:
            previous = self._read()
            records = [] if previous is None else previous["sessions"]
            if previous is not None:
                owner = previous["owner"]
                if owner.get("source") != WEBUI_SOURCE:
                    raise HandoverManifestError("manifest owner is not a Web UI engine")
                if int(previous.get("port", -1)) != self.port:
                    raise HandoverManifestError("manifest port does not match this engine")
                if not owner.get("shutdown", False) and process_matches(
                    int(owner["pid"]), float(owner["started_at"])
                ):
                    raise HandoverManifestError(
                        f"engine port {self.port} is already owned by live pid {owner['pid']}"
                    )

            self._data = {
                "version": MANIFEST_VERSION,
                "port": self.port,
                "owner": {
                    "source": WEBUI_SOURCE,
                    "pid": self.owner_pid,
                    "started_at": self.owner_started_at,
                    "shutdown": False,
                },
                "sessions": [dict(row) for row in records],
            }
            self._write()
            self._acquired = True
            return [dict(row) for row in records]
        except BaseException:
            self._release_file_lock()
            raise

    def register_session(self, session: Any) -> None:
        """Atomically record a newly spawned Web UI session, without secrets."""
        self._require_acquired()
        daemon = getattr(session, "daemon", None)
        if daemon is None:
            raise HandoverManifestError("cannot persist a session before daemon spawn")
        pid = int(getattr(daemon, "pid", 0) or 0)
        daemon_started_at = _process_started_at(pid)
        if not pid or daemon_started_at is None:
            raise HandoverManifestError("spawned daemon has no verifiable live PID")

        workspace = str(getattr(session, "workspace", None) or getattr(daemon, "project", ""))
        identity = str(
            getattr(session, "requested_identity", None)
            or getattr(daemon, "identity", "")
        )
        session_id = str(getattr(session, "session_id", ""))
        if not session_id or not identity or not workspace:
            raise HandoverManifestError("session is missing its stable identity fields")

        profile = _safe_profile(getattr(session, "profile", None))
        created_at = getattr(session, "created_at", None)
        created_epoch = created_at.timestamp() if created_at is not None else time.time()
        record: Dict[str, Any] = {
            "source": WEBUI_SOURCE,
            "session_id": session_id,
            "daemon_pid": pid,
            "daemon_started_at": daemon_started_at,
            "identity": identity,
            "agent": str(getattr(session, "agent", None) or getattr(daemon, "agent_name", "")),
            "workspace": _normalized_path(workspace),
            "socket_path": str(getattr(daemon, "socket_path", "")),
            "created_at": created_epoch,
            "conversation_id": str(getattr(session, "conversation_id", "") or ""),
            "display_name": str(getattr(session, "display_name", "") or ""),
            "profile": profile,
        }
        # Presence can already have the conversation log path by this point. It
        # is metadata only; the file's contents stay in the normal conversation
        # store and are never copied into this launch manifest.
        try:
            from .hub_bridge import HubBridge

            present = HubBridge().get_agent_by_identity(
                identity, use_cache=False, project=str(getattr(daemon, "project", workspace))
            )
            if present:
                log_path = present.get("session_log")
                if isinstance(log_path, str) and log_path:
                    record["session_log"] = log_path
        except Exception as exc:
            logger.debug("could not add session-log path to handover metadata: %s", exc)

        sessions = self._data["sessions"]
        sessions[:] = [row for row in sessions if row.get("session_id") != session_id]
        sessions.append(record)
        self._write()

    def remove_session(self, session_id: str) -> None:
        """Remove a session only after its explicit stop has succeeded."""
        self._require_acquired()
        sessions = self._data["sessions"]
        retained = [row for row in sessions if row.get("session_id") != session_id]
        if len(retained) == len(sessions):
            return
        # The durable file is the truth: swap the in-memory view only once the
        # write succeeds, so a failed remove stays visible and retryable.
        self._data["sessions"] = retained
        try:
            self._write()
        except BaseException:
            self._data["sessions"] = sessions
            raise


    def prune_dead_sessions(self) -> int:
        """Drop dead records and identical aliases of the same live process."""
        self._require_acquired()
        sessions = self._data["sessions"]
        retained = [
            row
            for row in sessions
            if process_matches(
                int(row.get("daemon_pid", 0) or 0),
                float(row.get("daemon_started_at", 0) or 0),
            )
        ]
        # A prior engine could register one daemon twice. Remove only aliases
        # with identical process, socket, workspace and conversation metadata;
        # retain the oldest session ID and never touch the conversation log.
        fingerprints: Dict[tuple, Dict[str, Any]] = {}
        for row in retained:
            fingerprint = (
                row["daemon_pid"], row["daemon_started_at"], row["identity"],
                _normalized_path(row["workspace"]), _normalized_path(row["socket_path"]),
                row.get("conversation_id") or "", row.get("session_log") or "",
            )
            previous = fingerprints.get(fingerprint)
            if previous is None or row["created_at"] < previous["created_at"]:
                fingerprints[fingerprint] = row
        canonical_ids = {id(row) for row in fingerprints.values()}
        retained = [row for row in retained if id(row) in canonical_ids]
        removed = len(sessions) - len(retained)
        if removed:
            self._data["sessions"] = retained
            try:
                self._write()
            except BaseException:
                self._data["sessions"] = sessions
                raise
        return removed

    def prepare_handover(self) -> int:
        """Flush the already durable records before an orderly UI restart."""
        self._require_acquired()
        self._write()
        return len(self._data["sessions"])

    def reown_rows(self, discovered: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Build safe adoption rows for stored processes still matching presence.

        A valid manifest row can also be used when HubBridge discovery is late,
        but a conflicting row with the same session id is never adopted.
        """
        self._require_acquired()
        by_session: Dict[str, Dict[str, Any]] = {
            str(row.get("session_id")): row
            for row in discovered
            if row.get("session_id")
        }
        results: List[Dict[str, Any]] = []
        seen_pids: set[int] = set()
        for record in sorted(self._data["sessions"], key=lambda item: item["created_at"]):
            if record.get("source") != WEBUI_SOURCE:
                continue
            pid = int(record.get("daemon_pid", 0) or 0)
            started_at = record.get("daemon_started_at")
            if not pid or not isinstance(started_at, (int, float)):
                continue
            if not process_matches(pid, float(started_at)):
                continue
            socket_path = str(record.get("socket_path") or "")
            if not socket_path or not Path(socket_path).exists():
                continue

            present = by_session.get(str(record["session_id"]))
            if present is not None and not self._matches_presence(record, present):
                continue
            if pid in seen_pids:
                logger.warning("ignoring another owner record for daemon pid %s", pid)
                continue
            seen_pids.add(pid)
            row = self._presence_row(record)
            if present is not None:
                # Prefer current presence facts such as the actual display name
                # and conversation log, while keeping validated ownership fields
                # from the manifest authoritative.
                for key in ("name", "device", "session_log", "model", "provider", "profile", "harness"):
                    value = present.get(key)
                    if key == "profile" and not isinstance(value, str):
                        continue
                    if value not in (None, ""):
                        row[key] = value
            results.append(row)
        return results

    def close(self, *, graceful: bool = True) -> None:
        # Mark a completed lifespan before releasing the lock. This lets a new
        # app instance reown safely even when embedded/test use restarts the
        # server within one long-lived Python process. Abrupt death never writes
        # this marker and is still detected through PID plus creation time.
        try:
            if graceful and self._acquired and self._data is not None:
                self._data["owner"]["shutdown"] = True
                self._data["owner"]["shutdown_at"] = time.time()
                try:
                    self._write()
                except OSError:
                    # The marker is an optimization; PID plus start time still
                    # detects this owner after exit. A failed marker write must
                    # never keep the lock or break engine shutdown.
                    logger.exception(
                        "could not write the shutdown marker for port %s", self.port
                    )
        finally:
            self._release_file_lock()
            self._acquired = False

    def _matches_presence(self, record: Dict[str, Any], present: Dict[str, Any]) -> bool:
        if str(present.get("session_id")) != str(record.get("session_id")):
            return False
        if int(present.get("daemon_pid") or present.get("pid") or 0) != int(record["daemon_pid"]):
            return False

        present_started_at = present.get("daemon_started_at")
        if isinstance(present_started_at, (int, float)) and not math.isclose(
            float(present_started_at),
            float(record["daemon_started_at"]),
            rel_tol=0.0,
            abs_tol=0.02,
        ):
            return False
        if str(present.get("identity") or "") != str(record["identity"]):
            return False
        workspace = present.get("workspace")
        if workspace and _normalized_path(workspace) != _normalized_path(record.get("workspace")):
            return False
        socket_path = present.get("socket_path")
        if socket_path and _normalized_path(socket_path) != _normalized_path(record.get("socket_path")):
            return False
        return True

    def _presence_row(self, record: Dict[str, Any]) -> Dict[str, Any]:
        profile = record.get("profile") if isinstance(record.get("profile"), dict) else {}
        return {
            "session_id": str(record["session_id"]),
            "daemon_pid": int(record["daemon_pid"]),

            "daemon_started_at": float(record["daemon_started_at"]),
            "identity": str(record["identity"]),
            "agent": str(record.get("agent") or ""),
            "workspace": str(record.get("workspace") or ""),
            "socket_path": str(record["socket_path"]),
            "session_log": str(record.get("session_log") or ""),
            "created_at": float(record.get("created_at") or time.time()),
            "conversation_id": str(record.get("conversation_id") or ""),
            "name": str(record.get("display_name") or record.get("identity") or ""),
            "profile": str(profile.get("name") or ""),
            "model": str(profile.get("model") or ""),
            "provider": str(profile.get("provider") or ""),
            "harness": "",
            "device": "",
        }

    def _read(self) -> Optional[Dict[str, Any]]:
        if not self.path.exists():
            return None
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise HandoverManifestError(f"cannot read handover manifest {self.path}: {exc}") from exc
        if (
            not isinstance(data, dict)
            or data.get("version") != MANIFEST_VERSION
            or not isinstance(data.get("port"), int)
            or not isinstance(data.get("owner"), dict)
            or not isinstance(data.get("sessions"), list)
        ):
            raise HandoverManifestError(f"unsupported or malformed handover manifest: {self.path}")
        owner = data["owner"]
        if (
            owner.get("source") != WEBUI_SOURCE
            or not isinstance(owner.get("pid"), int)
            or owner["pid"] <= 0
            or not isinstance(owner.get("started_at"), (int, float))
        ):
            raise HandoverManifestError(f"invalid owner identity in handover manifest: {self.path}")
        required_strings = ("session_id", "identity", "workspace", "socket_path")
        for row in data["sessions"]:
            if (
                not isinstance(row, dict)
                or row.get("source") != WEBUI_SOURCE
                or any(not isinstance(row.get(key), str) or not row[key] for key in required_strings)
                or not isinstance(row.get("daemon_pid"), int)
                or row["daemon_pid"] <= 0
                or not isinstance(row.get("daemon_started_at"), (int, float))
                or not isinstance(row.get("created_at"), (int, float))
                or not isinstance(row.get("profile", {}), dict)
            ):
                raise HandoverManifestError(f"invalid session row in handover manifest: {self.path}")
        return data

    def _write(self) -> None:
        assert self._data is not None
        payload = json.dumps(self._data, sort_keys=True, separators=(",", ":")) + "\n"
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            if os.name != "nt":
                self.path.chmod(0o600)
            try:
                directory_fd = os.open(self.path.parent, os.O_RDONLY)
            except OSError:
                return
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass

    def _require_acquired(self) -> None:
        if not self._acquired or self._data is None:
            raise HandoverManifestError("handover manifest is not claimed by this engine")

    def _acquire_file_lock(self) -> None:
        self.lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._lock_file = self.lock_path.open("a+b")
        if os.name != "nt":
            self.lock_path.chmod(0o600)
        try:
            if not try_lock(self._lock_file.fileno()):
                raise BlockingIOError("handover lock is held")
        except (OSError, BlockingIOError) as exc:
            self._lock_file.close()
            self._lock_file = None
            raise HandoverManifestError(
                f"another engine already owns the handover manifest for port {self.port}"
            ) from exc

    def _release_file_lock(self) -> None:
        if self._lock_file is None:
            return
        try:
            unlock(self._lock_file.fileno())
        except OSError:
            pass
        finally:
            self._lock_file.close()
            self._lock_file = None
