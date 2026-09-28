#!/usr/bin/env python3
"""Preflight and verify one explicitly scoped cross-host Kollab conversation.

The default action is read-only preflight.  Live acceptance requires both
``--execute`` and exact workspace confirmations.  Read-only preflight status
uses the workspace-owned local Hub RPC so it cannot depend on terminal paste
timing.  The attached-terminal `/connect status` path remains an explicit,
separate acceptance boundary and is reported as unverified by this harness.

No provider credential is inspected or emitted.  ``/doctor`` reports the
configured provider/profile/model only; the later live model turn is the
provider-readiness proof.  The private pairing invitation is transferred over
SSH stdin and is never written to the evidence manifest or terminal output.

Example (after the two dedicated test-workspace agents are already running):

    python scripts/relay/verify_agent_conversation.py \\
      --local-workspace /tmp/kollab-relay-test-mac \\
      --local-agent lapis \\
      --remote-target alzan-prod \\
      --remote-workspace /tmp/kollab-relay-test-prod \\
      --remote-agent koordinator \\
      --relay-origin https://kollabor.ai \\
      --artifacts-dir ~/.kollab/acceptance-evidence \\
      --execute \\
      --confirm-local-workspace /tmp/kollab-relay-test-mac \\
      --confirm-remote-workspace /tmp/kollab-relay-test-prod \\
      --check follow-up --check question-answer --check cancel --check reconnect \\
      --check unauthorized --check revoked --check replay \\
      --check wrong-workspace

The harness never defaults to a host or relay, never restarts an agent or
service, and never removes a pre-existing local invitation or peer grant.
The uniquely named remote file is retained as acceptance evidence inside the
dedicated test workspace; its exact path and content hash are recorded privately.

For source-mode execution, set ``PYTHONPATH`` for this harness process and pass
source-bound Python wrappers with both ``--local-python`` and
``--remote-python``. Nested helpers start fresh interpreters and do not inherit
the Kollab CLI wrapper's import environment.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import hashlib
import json
import os
import pty
import re
import select
import shlex
import shutil
import stat
import struct
import subprocess
import sys
import termios
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Sequence

CHECKS = (
    "ui-command",
    "follow-up",
    "question-answer",
    "cancel",
    "reconnect",
    "unauthorized",
    "revoked",
    "replay",
    "wrong-workspace",
)
PUBLIC_KEY_RE = re.compile(r"\b[0-9a-f]{64}\b")
ID_RE = re.compile(r"[0-9a-f]{32}\Z")
RELAY_ADDRESS_RE = re.compile(
    r"relay:[0-9a-f]{64}:[0-9a-f]{32}:[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z"
)
AGENT_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
VERSION_RE = re.compile(r"\bkollab(?:or)?\s+(\d+\.\d+\.\d+(?:[-+][\w.-]+)?)\b", re.I)
ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
TASK_DEADLINE_MAX = 600
INTERACTIVE_OUTPUT_MAX_BYTES = 1_048_576
TRACE_FILE_READ_MAX_BYTES = 16 * 1024 * 1024
TRACE_TOTAL_READ_MAX_BYTES = 64 * 1024 * 1024
TRACE_MAX_FILES = 40
TRACE_MAX_DIRECTORY_ENTRIES = 4096
TRACE_MAX_RECORDS = 20_000
TRACE_MAX_RECORD_BYTES = 1024 * 1024
TERMINAL_STATES = frozenset(
    {"completed", "cancelled", "failed", "rejected", "interrupted"}
)


_TRACE_READER_SOURCE = r"""import fnmatch
import json
import os
import stat
from collections import deque
from pathlib import Path

_trace_state = {
    "bytes_read": 0,
    "records_read": 0,
    "files_read": 0,
    "reasons": {},
    "expected": {},
}
_TRACE_FILE_READ_MAX_BYTES = 16 * 1024 * 1024
_TRACE_TOTAL_READ_MAX_BYTES = 64 * 1024 * 1024
_TRACE_MAX_FILES = 40
_TRACE_MAX_DIRECTORY_ENTRIES = 4096
_TRACE_MAX_RECORDS = 20000
_TRACE_MAX_RECORD_BYTES = 1024 * 1024


def _trace_mark(reason):
    reasons = _trace_state["reasons"]
    if reason in reasons:
        reasons[reason] += 1
    elif len(reasons) < 16:
        reasons[reason] = 1
    else:
        reasons["other_bounded_reader_issues"] = reasons.get(
            "other_bounded_reader_issues", 0
        ) + 1


def _trace_dir_identity(value):
    return (value.st_dev, value.st_ino, value.st_uid, value.st_mode)


def _trace_file_identity(value):
    return (
        value.st_dev,
        value.st_ino,
        value.st_uid,
        value.st_mode,
        value.st_nlink,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _trace_safe_mode(value):
    return (
        stat.S_ISREG(value.st_mode)
        and value.st_uid == os.getuid()
        and stat.S_IMODE(value.st_mode) & 0o022 == 0
        and value.st_nlink == 1
    )


def trace_recent_paths(directory, pattern, count=40, limit=None):
    root = Path(directory)
    directory_fd = None
    try:
        before = root.lstat()
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(
            os, "O_NOFOLLOW", 0
        ) | getattr(os, "O_NONBLOCK", 0)
        directory_fd = os.open(str(root), flags)
        opened = os.fstat(directory_fd)
        if (
            not stat.S_ISDIR(before.st_mode)
            or before.st_uid != os.getuid()
            or stat.S_IMODE(before.st_mode) & 0o022
            or _trace_dir_identity(before) != _trace_dir_identity(opened)
        ):
            _trace_mark("unsafe_trace_directory")
            os.close(directory_fd)
            directory_fd = None
            return []
    except OSError:
        if directory_fd is not None:
            os.close(directory_fd)
        _trace_mark("trace_directory_unavailable")
        return []

    found = []
    examined = 0
    try:
        with os.scandir(directory_fd) as entries:
            for entry in entries:
                examined += 1
                if examined > _TRACE_MAX_DIRECTORY_ENTRIES:
                    _trace_mark("directory_entry_limit")
                    break
                if not fnmatch.fnmatchcase(entry.name, pattern):
                    continue
                try:
                    item = os.stat(
                        entry.name, dir_fd=directory_fd, follow_symlinks=False
                    )
                except OSError:
                    _trace_mark("trace_file_changed_during_listing")
                    continue
                if not _trace_safe_mode(item):
                    _trace_mark("unsafe_trace_file")
                    continue
                path = root / entry.name
                found.append((item.st_mtime_ns, entry.name, path, item))
    finally:
        os.close(directory_fd)

    found.sort(key=lambda row: (row[0], row[1]), reverse=True)
    keep = min(max(int(count), 0), _TRACE_MAX_FILES)
    if len(found) > keep:
        _trace_mark("trace_file_limit")
    selected = found[:keep]
    for _mtime, _name, path, item in selected:
        _trace_state["expected"][str(path)] = {
            "directory": _trace_dir_identity(opened),
            "file": _trace_file_identity(item),
        }
    return [row[2] for row in selected]


def trace_safe_read(path, limit=None):
    path = Path(path)
    expected = _trace_state["expected"].get(str(path))
    if expected is None:
        _trace_mark("trace_file_not_prevalidated")
        return b""
    directory_fd = None
    file_fd = None
    try:
        directory_fd = os.open(
            str(path.parent),
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
        )
        directory_stat = os.fstat(directory_fd)
        if _trace_dir_identity(directory_stat) != expected["directory"]:
            _trace_mark("trace_directory_replaced")
            os.close(directory_fd)
            directory_fd = None
            return b""
        named_before = os.stat(
            path.name, dir_fd=directory_fd, follow_symlinks=False
        )
        if (
            not _trace_safe_mode(named_before)
            or _trace_file_identity(named_before) != expected["file"]
        ):
            _trace_mark("trace_file_replaced_or_unsafe")
            os.close(directory_fd)
            directory_fd = None
            return b""
        file_fd = os.open(
            path.name,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
            dir_fd=directory_fd,
        )
    except OSError:
        if file_fd is not None:
            os.close(file_fd)
        if directory_fd is not None:
            os.close(directory_fd)
        _trace_mark("trace_file_open_failed_or_raced")
        return b""

    try:
        opened_file = os.fstat(file_fd)
        if (
            not _trace_safe_mode(opened_file)
            or _trace_file_identity(opened_file) != expected["file"]
        ):
            _trace_mark("trace_file_replaced_or_unsafe")
            return b""
        remaining = _TRACE_TOTAL_READ_MAX_BYTES - _trace_state["bytes_read"]
        per_call_limit = _TRACE_FILE_READ_MAX_BYTES
        if isinstance(limit, int) and limit > 0:
            per_call_limit = min(per_call_limit, limit)
        requested = min(opened_file.st_size, per_call_limit)
        amount = min(requested, max(remaining, 0))
        if remaining <= 0:
            _trace_mark("trace_total_byte_limit")
            return b""
        if remaining < requested:
            _trace_mark("trace_total_byte_limit")
        if opened_file.st_size > amount:
            _trace_mark("trace_tail_window_used")
        start = opened_file.st_size - amount
        previous_byte = os.pread(file_fd, 1, start - 1) if start else b""
        os.lseek(file_fd, start, os.SEEK_SET)
        chunks = []
        left = amount
        while left:
            chunk = os.read(file_fd, min(left, 65536))
            if not chunk:
                break
            chunks.append(chunk)
            left -= len(chunk)
        raw = b"".join(chunks)
        _trace_state["bytes_read"] += len(raw)
        after_file = os.fstat(file_fd)
        named_after = os.stat(
            path.name, dir_fd=directory_fd, follow_symlinks=False
        )
        after_directory = os.fstat(directory_fd)
        if (
            _trace_file_identity(after_file) != expected["file"]
            or _trace_file_identity(named_after) != expected["file"]
            or _trace_dir_identity(after_directory) != expected["directory"]
            or len(raw) != amount
        ):
            _trace_mark("trace_file_changed_during_read")
            return b""
    except OSError:
        _trace_mark("trace_file_read_failed_or_raced")
        return b""
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if directory_fd is not None:
            os.close(directory_fd)

    if start:
        if previous_byte != b"\n":
            first_newline = raw.find(b"\n")
            if first_newline < 0:
                _trace_mark("partial_first_trace_record")
                return b""
            raw = raw[first_newline + 1 :]
            _trace_mark("partial_first_trace_record")
    if raw and not raw.endswith(b"\n"):
        last_newline = raw.rfind(b"\n")
        raw = raw[: last_newline + 1] if last_newline >= 0 else b""
        _trace_mark("partial_last_trace_record")

    available = _TRACE_MAX_RECORDS - _trace_state["records_read"]
    retained = deque(maxlen=max(available, 0))
    cursor = 0
    seen = 0
    while cursor < len(raw):
        newline = raw.find(b"\n", cursor)
        if newline < 0:
            break
        line = raw[cursor : newline + 1]
        cursor = newline + 1
        seen += 1
        if len(line) > _TRACE_MAX_RECORD_BYTES:
            _trace_mark("trace_record_size_limit")
            continue
        try:
            record = json.loads(line)
            if not isinstance(record, dict):
                raise ValueError("JSONL record is not an object")
        except (ValueError, UnicodeError, RecursionError):
            _trace_mark("invalid_trace_json_record")
            continue
        retained.append(line)
    if cursor < len(raw) or seen > max(available, 0):
        _trace_mark("trace_record_limit")
    complete_lines = list(retained)
    _trace_state["records_read"] += len(complete_lines)
    _trace_state["files_read"] += 1
    return b"".join(complete_lines)


def trace_scan_status():
    return {
        "complete": not bool(_trace_state["reasons"]),
        "bytes_read": _trace_state["bytes_read"],
        "records_read": _trace_state["records_read"],
        "files_read": _trace_state["files_read"],
        "reasons": dict(sorted(_trace_state["reasons"].items())),
    }
"""


class AcceptanceError(RuntimeError):
    """Fixed, user-safe harness error.  Never store subprocess output here."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Endpoint:
    label: str
    workspace: Path
    agent: str
    host: str | None = None


class SecureProbeEventRecorder:
    """Accept bounded, correlated secure events from the isolated replay peer."""

    MAX_EVENTS = 64
    MAX_EVENT_BYTES = 32 * 1024
    MAX_TOTAL_BYTES = 256 * 1024
    EVENT_KINDS = frozenset({"progress", "question", "error", "result"})
    FIELDS = frozenset(
        {
            "id",
            "thread_id",
            "reply_to",
            "from",
            "to",
            "from_identity",
            "from_coordinator",
            "to_identity",
            "to_coordinator",
            "content",
            "kind",
            "expires_at",
        }
    )

    def __init__(self, *, sender: str, recipient: str):
        self.sender = sender
        self.recipient = recipient
        self.task_id = ""
        self.records: dict[str, dict[str, Any]] = {}
        self.total_bytes = 0

    def bind_task(self, task_id: str) -> None:
        if not ID_RE.fullmatch(task_id):
            raise AcceptanceError(
                "secure_probe_event_scope_invalid",
                "the secure-probe task correlation was invalid",
            )
        self.task_id = task_id

    def accept(self, incoming: dict[str, Any]) -> dict[str, Any]:
        if (
            not self.task_id
            or not isinstance(incoming, dict)
            or set(incoming) != self.FIELDS
            or incoming.get("kind") not in self.EVENT_KINDS
            or incoming.get("reply_to") != self.task_id
            or incoming.get("thread_id") != self.task_id
            or incoming.get("from") != self.sender
            or incoming.get("to") != self.recipient
            or not isinstance(incoming.get("id"), str)
            or not ID_RE.fullmatch(incoming["id"])
            or not isinstance(incoming.get("content"), str)
            or not incoming["content"].strip()
            or type(incoming.get("from_coordinator")) is not bool
            or type(incoming.get("to_coordinator")) is not bool
            or not isinstance(incoming.get("from_identity"), str)
            or not AGENT_ID_RE.fullmatch(incoming["from_identity"])
            or not isinstance(incoming.get("to_identity"), str)
            or not AGENT_ID_RE.fullmatch(incoming["to_identity"])
        ):
            raise ValueError("uncorrelated secure-probe response")
        try:
            content_bytes = incoming["content"].encode("utf-8")
            if len(content_bytes) > 16000:
                raise ValueError("secure-probe event content is too large")
            encoded = json.dumps(
                incoming, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        except (TypeError, UnicodeError):
            raise ValueError("invalid secure-probe response envelope") from None
        if len(encoded) > self.MAX_EVENT_BYTES:
            raise ValueError("secure-probe event envelope is too large")
        event_id = incoming["id"]
        fingerprint = hashlib.sha256(encoded).hexdigest()
        previous = self.records.get(event_id)
        if previous is not None:
            if previous["envelope_sha256"] != fingerprint:
                raise ValueError("conflicting secure-probe response replay")
            return {
                "id": event_id,
                "state": "received",
                "duplicate": True,
            }
        if (
            len(self.records) >= self.MAX_EVENTS
            or self.total_bytes + len(encoded) > self.MAX_TOTAL_BYTES
        ):
            raise ValueError("secure-probe event evidence capacity reached")
        record = {
            "id": event_id,
            "reply_to": incoming["reply_to"],
            "thread_id": incoming["thread_id"],
            "from": incoming["from"],
            "to": incoming["to"],
            "kind": incoming["kind"],
            "state": "received",
            "content_sha256": hashlib.sha256(content_bytes).hexdigest(),
            "envelope_sha256": fingerprint,
            "envelope_bytes": len(encoded),
        }
        self.records[event_id] = record
        self.total_bytes += len(encoded)
        return {"id": event_id, "state": "received", "duplicate": False}

    @property
    def result_events(self) -> list[dict[str, Any]]:
        return [
            record for record in self.records.values() if record["kind"] == "result"
        ]


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class RelayStatus:
    state: str
    origin: str
    public_key: str
    workspace_id: str
    workspace_path: str
    agent_identity: str
    agent_id: str
    online_peers: int
    approved_peers: int


class ProcessRunner:
    """Run scoped endpoint probes and submit human turns through StateService."""

    def __init__(
        self,
        *,
        local_kollab: str = "kollab",
        remote_kollab: str = "kollab",
        local_python: str | None = None,
        remote_python: str = "python3",
        ssh: str = "ssh",
        diagnostic_transcript_dir: Path | None = None,
        timeout: float = 30,
    ):
        self.local_kollab = local_kollab
        self.remote_kollab = remote_kollab
        self.local_python = local_python or sys.executable
        self.remote_python = remote_python
        self.ssh = ssh
        self.diagnostic_transcript_dir = diagnostic_transcript_dir
        self.timeout = timeout

    def command(
        self,
        endpoint: Endpoint,
        arguments: Sequence[str],
        *,
        input_bytes: bytes | None = None,
        timeout: float | None = None,
    ) -> CommandResult:
        if any("\x00" in value for value in arguments):
            raise AcceptanceError(
                "invalid_argument", "command arguments contain a null byte"
            )
        if "--connect" in arguments:
            raise AcceptanceError(
                "interactive_control_not_explicit",
                "interactive relay commands require the explicit ui-command check",
            )
        if endpoint.host is None:
            argv = [self.local_kollab, *arguments]
            cwd = str(endpoint.workspace)
        else:
            remote_argv = [self.remote_kollab, *arguments]
            remote_command = (
                "cd "
                + shlex.quote(str(endpoint.workspace))
                + " && exec "
                + shlex.join(remote_argv)
            )
            argv = [
                self.ssh,
                "-T",
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                "ConnectTimeout=10",
                "--",
                endpoint.host,
                remote_command,
            ]
            cwd = None
        return self._run(argv, cwd=cwd, input_bytes=input_bytes, timeout=timeout)

    def interactive_connect(
        self,
        endpoint: Endpoint,
        attach_arguments: Sequence[str],
        connect_arguments: Sequence[str],
        *,
        timeout: float | None = None,
    ) -> CommandResult:
        """Submit only the separately requested UI acceptance command."""
        return self._interactive_connect(
            endpoint,
            attach_arguments,
            connect_arguments,
            timeout=timeout,
        )

    def _interactive_connect(
        self,
        endpoint: Endpoint,
        attach_arguments: Sequence[str],
        connect_arguments: Sequence[str],
        *,
        timeout: float | None,
    ) -> CommandResult:
        """Submit one command as operator input, never as model prompt text."""
        if "--attach" not in attach_arguments or "--project" not in attach_arguments:
            raise AcceptanceError(
                "interactive_scope_missing",
                "network commands require an explicit attached agent and project",
            )
        try:
            attach_agent = attach_arguments[attach_arguments.index("--attach") + 1]
            project = attach_arguments[attach_arguments.index("--project") + 1]
        except IndexError:
            raise AcceptanceError(
                "interactive_scope_missing",
                "network commands require an explicit attached agent and project",
            ) from None
        if (
            attach_agent != endpoint.agent
            or not Path(project).is_absolute()
            or Path(project) != endpoint.workspace
        ):
            raise AcceptanceError(
                "interactive_scope_mismatch",
                "network commands must attach to the confirmed agent and workspace",
            )
        command_text = "/connect " + " ".join(connect_arguments)
        encoded_command = command_text.encode("utf-8")
        if (
            len(encoded_command) > 4096
            or any(ord(char) < 32 or ord(char) == 127 for char in command_text)
            or any(part.upper().startswith("K1-") for part in connect_arguments)
        ):
            raise AcceptanceError(
                "interactive_command_rejected",
                "the scoped network command was invalid or contained private enrollment material",
            )

        if endpoint.host is None:
            argv = [self.local_kollab, *attach_arguments]
            cwd = str(endpoint.workspace)
        else:
            remote_argv = [self.remote_kollab, *attach_arguments]
            remote_command = (
                "cd "
                + shlex.quote(str(endpoint.workspace))
                + " && exec "
                + shlex.join(remote_argv)
            )
            argv = [
                self.ssh,
                "-tt",
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                "ConnectTimeout=10",
                "--",
                endpoint.host,
                remote_command,
            ]
            cwd = None

        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 120, 0, 0))
        process = None
        captured = bytearray()
        command_sent = False
        result_seen = False
        command_output_offset = 0
        text_sent_at = 0.0
        echo_text = command_text[1:].lower()
        diagnostic_command = bool(
            connect_arguments and connect_arguments[0] in {"status", "peers", "agents"}
        )
        try:
            environment = os.environ.copy()
            environment.setdefault("TERM", "xterm-256color")
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                env=environment,
                close_fds=True,
                start_new_session=True,
            )
            os.close(slave)
            slave = -1
            deadline = time.monotonic() + (timeout or self.timeout)
            attach_reported = False
            quiet_since = 0.0
            attached_text = f"attached to {attach_agent}".lower()
            while time.monotonic() < deadline:
                ready, _, _ = select.select([master], [], [], 0.1)
                if ready:
                    try:
                        chunk = os.read(master, 65536)
                    except OSError:
                        chunk = b""
                    if chunk:
                        captured.extend(chunk)
                        if len(captured) > INTERACTIVE_OUTPUT_MAX_BYTES:
                            raise AcceptanceError(
                                "interactive_output_too_large",
                                "the attached command output exceeded its size limit",
                            )
                        text = _clean_text(captured.decode("utf-8", errors="replace"))
                        if "no agent found with identity" in text.lower():
                            break
                        lowered = text.lower()
                        attach_reported = attach_reported or attached_text in lowered
                        attached_at = lowered.rfind(attached_text)
                        ready_at = lowered.find(
                            "info: ready. type your message and press enter",
                            attached_at,
                        )
                        if attach_reported and ready_at >= 0 and not text_sent_at:
                            command_output_offset = len(captured)
                            os.write(master, encoded_command)
                            text_sent_at = time.monotonic()
                        elif (
                            command_sent
                            and "info:"
                            in _clean_text(
                                captured[command_output_offset:].decode(
                                    "utf-8", errors="replace"
                                )
                            ).lower()
                        ):
                            result_seen = True
                            quiet_since = time.monotonic()
                    elif process.poll() is not None:
                        break
                elif process.poll() is not None:
                    break
                elif result_seen and time.monotonic() - quiet_since >= 0.75:
                    break
                # Send Enter only after the TUI has echoed the typed command:
                # text and Enter that arrive together (e.g. coalesced over
                # SSH) are taken as a multi-line paste, not a slash command.
                if text_sent_at and not command_sent:
                    echoed = echo_text in _clean_text(
                        captured[command_output_offset:].decode(
                            "utf-8", errors="replace"
                        )
                    ).lower()
                    waited = time.monotonic() - text_sent_at
                    if (echoed and waited >= 0.3) or waited >= 3.0:
                        os.write(master, b"\r")
                        command_sent = True
                        quiet_since = time.monotonic()

            if not command_sent:
                raise AcceptanceError(
                    "interactive_attach_not_ready",
                    "the attached Hub did not reach its command prompt",
                )
            if not result_seen:
                raise AcceptanceError(
                    "interactive_command_timeout",
                    "the attached Hub did not return a network command result",
                )
        except OSError as exc:
            raise AcceptanceError(
                "interactive_command_unavailable",
                "the attached Hub command could not be started",
            ) from exc
        finally:
            if (
                self.diagnostic_transcript_dir is not None
                and command_sent
                and diagnostic_command
                and not result_seen
            ):
                self._save_readonly_transcript(
                    endpoint,
                    connect_arguments[0],
                    bytes(captured[command_output_offset:]),
                )
            if process is not None and process.poll() is None:
                try:
                    os.write(master, b"\x1a")
                    detach_deadline = time.monotonic() + min(
                        10.0, timeout or self.timeout
                    )
                    while time.monotonic() < detach_deadline:
                        ready, _, _ = select.select([master], [], [], 0.1)
                        if ready:
                            try:
                                chunk = os.read(master, 65536)
                            except OSError:
                                chunk = b""
                            if chunk:
                                captured.extend(chunk)
                                if len(captured) > INTERACTIVE_OUTPUT_MAX_BYTES:
                                    break
                        if process.poll() is not None and not ready:
                            break
                    process.wait(timeout=0.1)
                except (OSError, subprocess.TimeoutExpired):
                    pass
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        raise AcceptanceError(
                            "interactive_cleanup_pending",
                            "the scoped attached command did not exit after graceful detach",
                        ) from None
            if slave >= 0:
                os.close(slave)
            try:
                os.close(master)
            except OSError:
                pass

        if process is not None and process.returncode != 0:
            raise AcceptanceError(
                "interactive_detach_failed",
                "the attached Hub command did not detach cleanly",
            )

        return CommandResult(
            process.returncode if process is not None else 1,
            _clean_text(captured.decode("utf-8", errors="replace")),
            "",
        )

    def _save_readonly_transcript(
        self, endpoint: Endpoint, command: str, output: bytes
    ) -> None:
        """Keep only a failed read-only command's post-input PTY output, mode 0600."""
        directory = self.diagnostic_transcript_dir
        if directory is None or command not in {"status", "peers", "agents"}:
            return
        try:
            if directory.exists() or directory.is_symlink():
                info = directory.lstat()
                if (
                    not stat.S_ISDIR(info.st_mode)
                    or info.st_uid != os.getuid()
                    or stat.S_IMODE(info.st_mode) & 0o077
                    or directory.is_symlink()
                ):
                    raise OSError("unsafe transcript directory")
            else:
                directory.mkdir(mode=0o700, parents=True, exist_ok=False)
            name = f"{endpoint.label}-{command}-{time.time_ns()}.txt"
            path = directory / name
            fd = os.open(
                path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
            )
            with os.fdopen(fd, "wb") as stream:
                stream.write(
                    _clean_text(output.decode("utf-8", errors="replace")).encode(
                        "utf-8"
                    )
                )
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            raise AcceptanceError(
                "diagnostic_transcript_unavailable",
                "the private read-only command transcript could not be preserved",
            ) from exc

    def python(
        self,
        endpoint: Endpoint,
        source: str,
        arguments: Sequence[str] = (),
        *,
        input_bytes: bytes | None = None,
        timeout: float | None = None,
    ) -> CommandResult:
        """Run a fixed Python helper on the endpoint; argument values are quoted."""
        if endpoint.host is None:
            argv = [self.local_python, "-c", source, *arguments]
            return self._run(
                argv,
                cwd=str(endpoint.workspace),
                input_bytes=input_bytes,
                timeout=timeout,
            )
        remote_command = shlex.join([self.remote_python, "-c", source, *arguments])
        argv = [
            self.ssh,
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "ConnectTimeout=10",
            "--",
            endpoint.host,
            remote_command,
        ]
        return self._run(argv, cwd=None, input_bytes=input_bytes, timeout=timeout)

    def _run(
        self,
        argv: Sequence[str],
        *,
        cwd: str | None,
        input_bytes: bytes | None,
        timeout: float | None,
    ) -> CommandResult:
        try:
            completed = subprocess.run(
                list(argv),
                cwd=cwd,
                input=input_bytes,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout or self.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AcceptanceError(
                "command_timeout", "an endpoint command timed out"
            ) from exc
        except OSError as exc:
            raise AcceptanceError(
                "command_unavailable", "an endpoint command could not be started"
            ) from exc
        return CommandResult(
            completed.returncode,
            _decode(completed.stdout),
            _decode(completed.stderr),
        )


def _decode(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return value.decode("utf-8", errors="replace")


def _clean_text(value: str) -> str:
    return ANSI_RE.sub("", value).replace("\r", "")


def _require_success(result: CommandResult, step: str) -> str:
    if result.returncode:
        raise AcceptanceError(
            "endpoint_command_failed", f"endpoint step failed: {step}"
        )
    return _clean_text(result.stdout)


def _parse_version(output: str) -> str:
    match = VERSION_RE.search(_clean_text(output))
    if not match:
        raise AcceptanceError(
            "version_unavailable", "installed Kollab version was not reported"
        )
    return match.group(1)


def _doctor_fields(output: str) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    profile = ""
    for line in _clean_text(output).splitlines():
        verdict = re.match(r"\s*verdict:\s*(\S+)", line, re.I)
        if verdict:
            fields["verdict"] = verdict.group(1).lower()
        check = re.match(
            r"\s*\[(ok|warn|block)\]\s+([a-zA-Z0-9 _-]+?)\s{2,}(.+?)\s*$", line
        )
        if check:
            name = re.sub(r"\s+", "_", check.group(2).strip().lower())
            if name == "profile":
                profile = check.group(3)
            elif name == "cwd":
                fields["cwd"] = check.group(3)
    pieces = [piece.strip() for piece in profile.split("|")]
    fields["profile_configured"] = bool(
        len(pieces) >= 3
        and pieces[0]
        and pieces[1]
        and pieces[2]
        and pieces[1].lower() != "provider?"
    )
    return fields


def _workspace_scope(runner: Any, endpoint: Endpoint) -> dict[str, Any]:
    source = r"""import hashlib,json,os,stat,sys
from pathlib import Path
path=Path(sys.argv[1])
try:
    info=path.lstat()
    resolved=path.resolve(strict=True)
    real_directory=stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode)
    owned=info.st_uid==os.getuid()
    digest=hashlib.sha256(str(resolved).encode()).hexdigest()
    state_dir=Path.home()/".kollab"/"network"/digest
    state_path=state_dir/"state.json"
    workspace_id=""
    try:
        root_info=state_dir.lstat(); file_info=state_path.lstat()
        if (stat.S_ISDIR(root_info.st_mode) and root_info.st_uid==os.getuid()
            and stat.S_IMODE(root_info.st_mode)&0o077==0
            and stat.S_ISREG(file_info.st_mode) and file_info.st_uid==os.getuid()
            and stat.S_IMODE(file_info.st_mode)&0o077==0 and file_info.st_nlink==1):
            fd=os.open(state_path,os.O_RDONLY|getattr(os,"O_NOFOLLOW",0))
            with os.fdopen(fd,"rb") as stream: payload=json.loads(stream.read(65537))
            value=payload.get("workspace_id","")
            if isinstance(value,str) and len(value)==32 and all(c in "0123456789abcdef" for c in value):
                workspace_id=value
    except (OSError,ValueError,TypeError):
        pass
    print(json.dumps({"requested":str(path.absolute()),"resolved":str(resolved),"real_directory":real_directory,"owned":owned,"workspace_id":workspace_id}))
except OSError:
    print(json.dumps({"requested":str(path.absolute()),"resolved":"","real_directory":False,"owned":False,"workspace_id":""}))"""
    result = _require_success(
        runner.python(endpoint, source, [str(endpoint.workspace)]),
        endpoint.label + ".workspace_scope",
    )
    return _json_object(result)


def _relay_status(output: str) -> RelayStatus:
    clean = _clean_text(output)
    state = re.search(r"(?m)^\s*beacon:\s*(\S+)", clean)
    origin = re.search(r"(?m)^\s*address:\s*(\S+)", clean)
    key = re.search(r"(?m)^\s*your public key:\s*([0-9a-f]{64})\s*$", clean)
    workspace_id = re.search(r"(?m)^\s*workspace id:\s*([0-9a-f]{32})\s*$", clean)
    workspace_path = re.search(r"(?m)^\s*workspace path:\s*(\S.*?)\s*$", clean)
    agent_identity = re.search(r"(?m)^\s*agent identity:\s*(\S+)\s*$", clean)
    agent_id = re.search(
        r"(?m)^\s*agent id:\s*([A-Za-z0-9][A-Za-z0-9_.-]{0,127})\s*$", clean
    )
    peers = re.search(
        r"(?m)^\s*online peers:\s*(\d+)\s*;\s*approved keys:\s*(\d+)", clean
    )
    if (
        not state
        or not key
        or not workspace_id
        or not workspace_path
        or not agent_identity
        or not agent_id
        or not peers
    ):
        raise AcceptanceError(
            "relay_status_unavailable", "endpoint relay status was incomplete"
        )
    address = origin.group(1) if origin and origin.group(1) != "not" else ""
    if address == "not":
        address = ""
    return RelayStatus(
        state.group(1).lower(),
        address,
        key.group(1),
        workspace_id.group(1),
        workspace_path.group(1),
        agent_identity.group(1),
        agent_id.group(1),
        int(peers.group(1)),
        int(peers.group(2)),
    )


def _attached_relay_status_frame(output: str) -> str:
    """Extract one complete TUI beacon event without relaxing plain CLI parsing."""
    clean = _clean_text(output)
    marker = re.compile(r"(?im)info:\s*beacon:\s*(\S+)([^\r\n]*)")
    matches = list(marker.finditer(clean))
    if not matches:
        return clean

    field_pattern = re.compile(
        r"^\s*(address|your public key|workspace id|workspace path|agent identity|"
        r"agent id|online peers|reconnect on launch):\s*(.*?)\s*$",
        re.I,
    )
    field_order = (
        "address",
        "your public key",
        "workspace id",
        "workspace path",
        "agent identity",
        "agent id",
        "online peers",
        "reconnect on launch",
    )
    required = {
        "your public key",
        "workspace id",
        "workspace path",
        "agent identity",
        "agent id",
        "online peers",
    }
    frames = []
    for match in matches:
        if match.group(2).strip():
            raise AcceptanceError(
                "attached_status_frame_incomplete",
                "the attached status event did not start a complete beacon frame",
            )
        state = match.group(1).strip()
        fields: dict[str, str] = {}
        tail = clean[match.end() :]
        newline = tail.find("\n")
        if newline < 0:
            raise AcceptanceError(
                "attached_status_frame_incomplete",
                "the attached status event ended before its beacon fields",
            )
        for line in tail[newline + 1 :].splitlines():
            if not line.strip():
                break
            item = field_pattern.fullmatch(line)
            if item is None:
                break
            name = item.group(1).lower()
            value = item.group(2).strip()
            if not value or name in fields:
                raise AcceptanceError(
                    "attached_status_frame_conflict",
                    "the attached status event contained duplicate or empty fields",
                )
            fields[name] = value
        if not required.issubset(fields):
            raise AcceptanceError(
                "attached_status_frame_incomplete",
                "the attached status event did not contain a complete beacon frame",
            )
        frame = "\n".join(
            [f"beacon: {state}"]
            + [f"{name}: {fields[name]}" for name in field_order if name in fields]
        )
        if len(frame.encode("utf-8")) > 16 * 1024:
            raise AcceptanceError(
                "attached_status_frame_too_large",
                "the attached status frame exceeded its size limit",
            )
        frames.append(frame)

    if any(frame != frames[0] for frame in frames[1:]):
        raise AcceptanceError(
            "attached_status_frame_conflict",
            "the attached output contained conflicting beacon status frames",
        )
    return frames[0]


def _parse_peer_keys(output: str) -> dict[str, bool]:
    peers: dict[str, bool] = {}
    for line in _clean_text(output).splitlines():
        key = PUBLIC_KEY_RE.search(line)
        if key:
            peers[key.group(0)] = "approved" in line.lower()
    return peers


def _json_object(output: str) -> dict[str, Any]:
    """Parse a JSON object from a plain CLI result without echoing the source."""
    clean = _clean_text(output).strip()
    candidates = [clean]
    candidates.extend(reversed(clean.splitlines()))
    for candidate in candidates:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start >= 0 and end > start:
            try:
                value = json.loads(candidate[start : end + 1])
            except (ValueError, TypeError):
                continue
            if isinstance(value, dict):
                return value
    raise AcceptanceError(
        "json_result_unavailable", "endpoint did not return structured status"
    )


def _trace_scan_complete(evidence: dict[str, Any]) -> bool:
    scan = evidence.get("trace_scan") if isinstance(evidence, dict) else None
    return isinstance(scan, dict) and scan.get("complete") is True


def _relay_key(address: str) -> str:
    if not RELAY_ADDRESS_RE.fullmatch(address):
        raise AcceptanceError("relay_address_invalid", "a relay address was invalid")
    return address.split(":", 2)[1]


def _json_rows(output: str, key: str) -> list[dict[str, Any]]:
    value = _json_object(output)
    rows = value.get(key)
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise AcceptanceError(
            "directory_unavailable", "endpoint directory response was incomplete"
        )
    return rows


def _parse_invitation_path(output: str) -> Path:
    match = re.search(
        r"(?m)^Private invitation saved:\s*(.+?)\s*$", _clean_text(output)
    )
    if not match:
        raise AcceptanceError(
            "invitation_path_unavailable",
            "Kollab did not report its private invitation file",
        )
    return Path(match.group(1)).expanduser()


def _create_invitation(runner: Any, endpoint: Endpoint) -> tuple[Path, tuple[int, int]]:
    output = _run_text(
        runner,
        endpoint,
        endpoint.label + ".create_pairing_invitation",
        _endpoint_command(endpoint, endpoint.agent, "--connect", "invite"),
    )
    path = _parse_invitation_path(output)
    return path, _check_private_invitation(path, endpoint.workspace)


def _check_private_invitation(path: Path, workspace: Path) -> tuple[int, int]:
    expected_state = (
        Path.home()
        / ".kollab"
        / "network"
        / hashlib.sha256(str(workspace.resolve()).encode()).hexdigest()
    )
    try:
        info = path.lstat()
    except OSError as exc:
        raise AcceptanceError(
            "invitation_file_unavailable", "the private invitation file is unavailable"
        ) from exc
    if (
        path.parent.resolve() != expected_state.resolve()
        or not path.name.startswith("invite-")
        or path.suffix != ".txt"
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
    ):
        raise AcceptanceError(
            "invitation_file_unsafe",
            "the invitation was not a new private Kollab invitation file",
        )
    return info.st_dev, info.st_ino


def _read_invitation_for_transfer(
    path: Path, identity: tuple[int, int] | None = None
) -> bytes:
    """Read only a verified, bounded invitation for a private stdin transfer."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600
                or info.st_nlink != 1
                or identity is not None
                and (info.st_dev, info.st_ino) != identity
            ):
                raise AcceptanceError(
                    "invitation_file_unsafe",
                    "the invitation file was not private and user-owned",
                )
            raw = stream.read(4097)
    except OSError as exc:
        raise AcceptanceError(
            "invitation_file_unavailable", "the private invitation could not be read"
        ) from exc
    if not raw or len(raw) > 4096:
        raise AcceptanceError(
            "invitation_file_unsafe", "the private invitation size was invalid"
        )
    return raw


def _cleanup_local_invitation(
    path: Path, workspace: Path, device: int, inode: int
) -> bool:
    """Remove only the exact private invite file created by this acceptance run."""
    expected_state = (
        Path.home()
        / ".kollab"
        / "network"
        / hashlib.sha256(str(workspace.resolve()).encode()).hexdigest()
    )
    try:
        info = path.lstat()
        if (
            path.parent.resolve() != expected_state.resolve()
            or not path.name.startswith("invite-")
            or path.suffix != ".txt"
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_nlink != 1
            or (info.st_dev, info.st_ino) != (device, inode)
        ):
            return False
        path.unlink()
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def _resolve_workspace(value: str, label: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise AcceptanceError(
            "workspace_not_absolute", f"{label} workspace path must be absolute"
        )
    try:
        info = path.lstat()
        resolved = path.resolve(strict=True)
    except OSError as exc:
        raise AcceptanceError(
            "workspace_unavailable", f"{label} workspace is unavailable"
        ) from exc
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise AcceptanceError(
            "workspace_unsafe",
            f"{label} workspace must be a real directory, not a symlink",
        )
    return resolved


def _validate_relay_origin(value: str) -> str:
    import ipaddress
    from urllib.parse import urlsplit

    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise AcceptanceError(
            "invalid_relay_origin", "relay origin must be a canonical HTTPS origin"
        ) from None
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise AcceptanceError(
            "invalid_relay_origin", "relay origin must be a canonical HTTPS origin"
        )
    try:
        host = parsed.hostname.encode("idna").decode("ascii").lower()
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise AcceptanceError(
            "invalid_relay_origin", "relay origin must use a DNS hostname"
        )
    if (
        not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", host)
        or ".." in host
    ):
        raise AcceptanceError(
            "invalid_relay_origin", "relay origin must be a canonical HTTPS origin"
        )
    authority = host if port in (None, 443) else f"{host}:{port}"
    return f"https://{authority}"


def _endpoint_command(endpoint: Endpoint, agent: str, *command: str) -> list[str]:
    return [
        "--simple",
        "--project",
        str(endpoint.workspace),
        "--attach",
        agent,
        *command,
    ]


def _run_text(
    runner: Any,
    endpoint: Endpoint,
    step: str,
    args: Sequence[str],
    *,
    timeout: float | None = None,
) -> str:
    if "--connect" in args:
        index = args.index("--connect")
        expected_prefix = [
            "--simple",
            "--project",
            str(endpoint.workspace),
            "--attach",
            endpoint.agent,
        ]
        if list(args[:index]) != expected_prefix:
            raise AcceptanceError(
                "relay_command_scope_invalid",
                "relay commands must target the confirmed workspace and agent",
            )
        return _workspace_owner_command(
            runner,
            endpoint,
            args[index + 1 :],
            step=step,
            timeout=timeout,
        )["text"]
    return _require_success(runner.command(endpoint, args, timeout=timeout), step)


def _relay_command_value(parts: Sequence[str]) -> str:
    """Validate one narrowly scoped operator command accepted by this harness."""
    values = list(parts)
    if (
        not values
        or any(not isinstance(value, str) or not value for value in values)
        or any(
            any(ord(char) < 32 or ord(char) == 127 for char in value)
            or value.upper().startswith("K1-")
            for value in values
        )
        or len(" ".join(values).encode("utf-8")) > 4096
    ):
        raise AcceptanceError(
            "relay_command_invalid", "the scoped relay command was invalid"
        )
    head = values[0]

    def peer_key(value: str) -> bool:
        return bool(re.fullmatch(r"[0-9a-f]{64}", value))

    def relay_address(value: str) -> bool:
        return bool(
            re.fullmatch(
                r"relay:[0-9a-f]{64}:[0-9a-f]{32}:" r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}",
                value,
            )
        )

    valid = (
        (
            head in {"status", "peers", "grants", "invite", "disconnect"}
            and len(values) == 1
        )
        or (
            head == "agents"
            and len(values) == 2
            and (values[1] == "local" or peer_key(values[1]))
        )
        or (head == "join" and len(values) == 2 and Path(values[1]).is_absolute())
        or (head in {"approve", "revoke"} and len(values) == 2 and peer_key(values[1]))
        or (
            head in {"allow", "deny"}
            and len(values) == 3
            and peer_key(values[1])
            and AGENT_ID_RE.fullmatch(values[2])
        )
        or (head == "withdraw" and len(values) == 2 and ID_RE.fullmatch(values[1]))
        or (
            head == "send"
            and len(values) >= 3
            and relay_address(values[1])
            and bool(" ".join(values[2:]).strip())
        )
        or (
            head in {"cancel", "task"}
            and len(values) == 3
            and relay_address(values[1])
            and ID_RE.fullmatch(values[2])
        )
    )
    if not valid and len(values) == 1 and head.lower().startswith("https://"):
        valid = _validate_relay_origin(head) == head
    if not valid:
        raise AcceptanceError(
            "relay_command_scope_invalid",
            "the requested relay command is outside the acceptance harness scope",
        )
    if head == "join":
        return "join " + shlex.quote(values[1])
    return " ".join(values)


def _workspace_owner_command(
    runner: Any,
    endpoint: Endpoint,
    parts: Sequence[str],
    *,
    step: str,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Run one allowlisted operator command through this workspace's Hub owner."""
    _relay_command_value(parts)
    source = r"""import asyncio,json,os,re,shlex,stat,sys
from pathlib import Path
workspace=Path(sys.argv[1]).resolve(strict=True)
expected_identity=sys.argv[2]
command_parts=json.loads(sys.argv[3])
rpc_timeout=min(60.0,max(1.0,float(sys.argv[4])))
def peer_key(value): return isinstance(value,str) and re.fullmatch(r"[0-9a-f]{64}",value)
def address(value):
    pattern=(r"relay:[0-9a-f]{64}:[0-9a-f]{32}:"
             r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
    return isinstance(value,str) and re.fullmatch(pattern,value)
def valid_command(parts):
    if not isinstance(parts,list) or not parts or any(not isinstance(v,str) or not v for v in parts): return False
    if any(any(ord(c)<32 or ord(c)==127 for c in v) or v.upper().startswith("K1-") for v in parts): return False
    if len(" ".join(parts).encode("utf-8"))>4096: return False
    head=parts[0]
    if head in {"status","peers","grants","invite","disconnect"}: return len(parts)==1
    if head=="agents": return len(parts)==2 and (parts[1]=="local" or bool(peer_key(parts[1])))
    if head=="join": return len(parts)==2 and Path(parts[1]).is_absolute()
    if head in {"approve","revoke"}: return len(parts)==2 and bool(peer_key(parts[1]))
    if head in {"allow","deny"}:
        agent_pattern=r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}"
        return len(parts)==3 and bool(peer_key(parts[1])) and bool(re.fullmatch(agent_pattern,parts[2]))
    if head=="withdraw": return len(parts)==2 and bool(re.fullmatch(r"[0-9a-f]{32}",parts[1]))
    if head=="send": return len(parts)>=3 and bool(address(parts[1])) and bool(" ".join(parts[2:]).strip())
    if head in {"cancel","task"}:
        return len(parts)==3 and bool(address(parts[1])) and bool(re.fullmatch(r"[0-9a-f]{32}",parts[2]))
    return len(parts)==1 and bool(re.fullmatch(r"https://[A-Za-z0-9.-]+(?::[0-9]{1,5})?",head))
if not valid_command(command_parts):
    print(json.dumps({"ok":False,"reason":"relay_command_scope_invalid"})); raise SystemExit(0)
command=("join "+shlex.quote(command_parts[1])) if command_parts[0]=="join" else " ".join(command_parts)
try:
    from plugins.hub.relay_owner import WorkspaceRelayOwner,local_relay_rpc
    owner_service=WorkspaceRelayOwner(workspace)
    owner=owner_service.owner()
    del owner_service
    if not isinstance(owner,dict) or set(owner)!={"socket_path","agent_id","pid"}:
        print(json.dumps({"ok":False,"reason":"workspace_owner_unavailable"})); raise SystemExit(0)
    socket_path=owner["socket_path"]
    agent_id=owner["agent_id"]
    pid=owner["pid"]
    if (
        not isinstance(socket_path,str) or not socket_path.startswith("/")
        or "\x00" in socket_path or len(os.fsencode(socket_path))>103
        or not isinstance(agent_id,str) or not agent_id
        or type(pid) is not int or pid<=0
    ):
        print(json.dumps({"ok":False,"reason":"workspace_owner_invalid"})); raise SystemExit(0)
    try:
        os.kill(pid,0)
        socket_info=os.stat(socket_path,follow_symlinks=False)
    except OSError:
        print(json.dumps({"ok":False,"reason":"workspace_owner_not_live"})); raise SystemExit(0)
    if (
        not stat.S_ISSOCK(socket_info.st_mode) or socket_info.st_uid!=os.getuid()
        or stat.S_IMODE(socket_info.st_mode)&0o077
    ):
        print(json.dumps({"ok":False,"reason":"workspace_owner_socket_invalid"})); raise SystemExit(0)
    async def submit():
        status=await local_relay_rpc(
            socket_path,"relay.command",{"value":"status","agent_id":agent_id},timeout=rpc_timeout
        )
        if not isinstance(status,dict) or set(status)!={"text"} or not isinstance(status["text"],str):
            return {"ok":False,"reason":"workspace_owner_response_invalid"}
        lines={
            line.partition(":")[0].strip():line.partition(":")[2].strip()
            for line in status["text"].splitlines() if ":" in line
        }
        if (
            lines.get("workspace path")!=str(workspace)
            or lines.get("agent identity")!=expected_identity
            or lines.get("agent id")!=agent_id
        ):
            return {"ok":False,"reason":"workspace_owner_scope_mismatch"}
        result=await local_relay_rpc(
            socket_path,"relay.command",{"value":command,"agent_id":agent_id},timeout=rpc_timeout
        )
        if not isinstance(result,dict) or set(result)!={"text"} or not isinstance(result["text"],str):
            return {"ok":False,"reason":"workspace_owner_response_invalid"}
        owner_service=WorkspaceRelayOwner(workspace)
        current=owner_service.owner()
        del owner_service
        if current!=owner:
            return {"ok":False,"reason":"workspace_owner_changed"}
        return {"ok":True,"owner_agent_id":agent_id,"text":result["text"]}
    result=asyncio.run(submit())
    print(json.dumps(result))
except SystemExit:
    raise
except Exception:
    print(json.dumps({"ok":False,"reason":"workspace_owner_rpc_unavailable"}))
"""
    rpc_timeout = min(max(float(timeout or 30), 1.0), 60.0)
    text = _require_success(
        runner.python(
            endpoint,
            source,
            [
                str(endpoint.workspace),
                endpoint.agent,
                json.dumps(list(parts), separators=(",", ":")),
                str(rpc_timeout),
            ],
            timeout=rpc_timeout + 5,
        ),
        endpoint.label + "." + step,
    )
    record = _json_object(text)
    if (
        record.get("ok") is not True
        or not isinstance(record.get("owner_agent_id"), str)
        or not isinstance(record.get("text"), str)
    ):
        raise AcceptanceError(
            "relay_owner_rpc_unavailable",
            "the workspace-owned Hub did not return a scoped relay command result",
        )
    return record


def _owner_relay_command(
    runner: Any, endpoint: Endpoint, command: str
) -> dict[str, Any]:
    """Read relay status through the live Hub that owns this exact workspace."""
    if command not in {"status", "peers"}:
        raise AcceptanceError(
            "relay_owner_command_invalid",
            "only read-only relay status commands are allowed",
        )
    return _workspace_owner_command(
        runner,
        endpoint,
        [command],
        step="relay_owner_" + command,
        timeout=30,
    )


def preflight_endpoint(runner: Any, endpoint: Endpoint) -> dict[str, Any]:
    version_text = _require_success(
        runner.command(endpoint, ["--version"]), endpoint.label + ".version"
    )
    doctor_text = _run_text(
        runner,
        endpoint,
        endpoint.label + ".doctor",
        _endpoint_command(endpoint, endpoint.agent, "--doctor"),
    )
    status_result = _owner_relay_command(runner, endpoint, "status")
    peer_result = _owner_relay_command(runner, endpoint, "peers")
    if status_result["owner_agent_id"] != peer_result["owner_agent_id"]:
        raise AcceptanceError(
            "relay_owner_changed", "workspace relay ownership changed during preflight"
        )
    status_text = status_result["text"]
    peer_text = peer_result["text"]
    version = _parse_version(version_text)
    doctor = _doctor_fields(doctor_text)
    relay = _relay_status(status_text)
    scope = _workspace_scope(runner, endpoint)
    resolved_workspace = scope.get("resolved", "")
    scope_matches = bool(
        resolved_workspace
        and relay.workspace_path == resolved_workspace
        and relay.workspace_id == scope.get("workspace_id")
        and relay.agent_identity == endpoint.agent
        and relay.agent_id == status_result["owner_agent_id"]
        and scope.get("requested") == str(endpoint.workspace.absolute())
        and scope.get("real_directory") is True
        and scope.get("owned") is True
    )
    return {
        "label": endpoint.label,
        "host": endpoint.host,
        "workspace": str(endpoint.workspace),
        "agent": endpoint.agent,
        "version": version,
        "provider_profile_configured": doctor.get("profile_configured") is True,
        "provider_access_preflight": "not tested; a live model turn is required",
        "provider_credential_values_inspected": False,
        "provider_live_execution": "pending",
        "attached_ui_connect_status_preflight": {
            "status": "unverified",
            "requirement": "attached-terminal /connect status input and result remain a separate acceptance boundary",
        },
        "relay_status_source": "workspace-owned same-user Hub RPC",
        "command_scope_matches_workspace": scope_matches,
        "workspace_scope": {
            "real_directory": scope.get("real_directory") is True,
            "owned_by_endpoint_user": scope.get("owned") is True,
            "resolved_path": resolved_workspace,
            "relay_state_id_matches": relay.workspace_id == scope.get("workspace_id"),
            "daemon_path_matches": relay.workspace_path == resolved_workspace,
            "daemon_agent_matches": relay.agent_identity == endpoint.agent,
            "daemon_agent_id_matches_owner": relay.agent_id
            == status_result["owner_agent_id"],
            "daemon_agent_id": relay.agent_id,
        },
        "relay": asdict(relay),
        "peers": _parse_peer_keys(peer_text),
    }


def _check_attached_connect_command(runner: Any, endpoint: Endpoint) -> dict[str, Any]:
    """Verify read-only `/connect status` through this endpoint's attached TUI."""
    output = _require_success(
        runner.interactive_connect(
            endpoint,
            [
                "--simple",
                "--project",
                str(endpoint.workspace),
                "--attach",
                endpoint.agent,
            ],
            ["status"],
            timeout=60,
        ),
        endpoint.label + ".attached_connect_status",
    )
    relay = _relay_status(_attached_relay_status_frame(output))
    scope = _workspace_scope(runner, endpoint)
    if (
        relay.agent_identity != endpoint.agent
        or relay.workspace_path != scope.get("resolved")
        or relay.workspace_id != scope.get("workspace_id")
        or scope.get("real_directory") is not True
        or scope.get("owned") is not True
    ):
        raise AcceptanceError(
            "attached_connect_scope_mismatch",
            "the attached status result did not match the selected workspace and agent",
        )
    return {
        "passed": True,
        "status": relay.state,
        "workspace_id_matches": True,
        "workspace_path_matches": True,
        "agent_identity_matches": True,
        "input_path": "attached Hub terminal slash command",
    }


def _endpoint_from_record(record: dict[str, Any]) -> RelayStatus:
    try:
        return RelayStatus(**record["relay"])
    except (KeyError, TypeError):
        raise AcceptanceError(
            "relay_status_unavailable", "endpoint relay status was incomplete"
        ) from None


def _verified_remote_workspace_path(record: dict[str, Any]) -> str:
    """Return the canonical path reported by the remote workspace probe.

    ``Endpoint.workspace`` is a path on another host. Resolving it with the
    controller's filesystem can rewrite valid Linux paths through local aliases
    (for example, macOS maps ``/home`` into ``/System/Volumes/Data/home``).
    The preflight scope result was produced on the remote endpoint and is the
    only path representation used for remote comparisons.
    """
    scope = record.get("workspace_scope")
    value = scope.get("resolved_path") if isinstance(scope, dict) else None
    if (
        not isinstance(value, str)
        or not value
        or "\x00" in value
        or not PurePosixPath(value).is_absolute()
        or ".." in PurePosixPath(value).parts
        or str(PurePosixPath(value)) != value
    ):
        raise AcceptanceError(
            "remote_workspace_path_unverified",
            "the remote preflight did not provide a canonical absolute workspace path",
        )
    return value


def _select_secure_probe_receiver(
    rows: Sequence[dict[str, Any]],
    *,
    workspace_path: str,
    workspace_id: str,
    agent_id: str,
    name: str,
) -> dict[str, Any]:
    matches = [
        row
        for row in rows
        if isinstance(row, dict)
        and row.get("workspace") == workspace_path
        and row.get("workspace_id") == workspace_id
        and row.get("agent_id") == agent_id
        and row.get("name") == name
    ]
    if len(matches) != 1 or type(matches[0].get("is_coordinator")) is not bool:
        raise AcceptanceError(
            "secure_probe_receiver_identity_unverified",
            "the selected receiver agent role was absent from its local roster",
        )
    return matches[0]


def _one_correlated_tool_call(calls: Any, tool_results: Any) -> bool:
    """Match one provider tool call to its tool result using its opaque ID."""
    if (
        not isinstance(calls, list)
        or not isinstance(tool_results, list)
        or len(calls) != 1
        or len(tool_results) != 1
        or not isinstance(calls[0], dict)
        or not isinstance(tool_results[0], dict)
    ):
        return False
    call_id = calls[0].get("id")
    result_id = tool_results[0].get("id")
    return bool(
        isinstance(call_id, str)
        and 0 < len(call_id) <= 256
        and call_id.strip() == call_id
        and not any(ord(char) < 32 or ord(char) == 127 for char in call_id)
        and call_id == result_id
    )


def _address_for_name(
    rows: list[dict[str, Any]],
    name: str,
    *,
    expected_key: str,
    expected_workspace_id: str,
    expected_agent_id: str,
) -> str:
    named_rows = [
        row
        for row in rows
        if row.get("name") == name and isinstance(row.get("address"), str)
    ]
    if not named_rows:
        raise AcceptanceError(
            "agent_identity_not_found",
            "the requested agent was not in the peer's directory",
        )
    scoped = []
    for row in named_rows:
        address = row["address"]
        parts = address.split(":")
        if (
            len(parts) != 4
            or parts[0] != "relay"
            or not re.fullmatch(r"[0-9a-f]{64}", parts[1])
            or not ID_RE.fullmatch(parts[2])
            or not AGENT_ID_RE.fullmatch(parts[3])
        ):
            raise AcceptanceError(
                "agent_address_invalid", "the remote agent address was invalid"
            )
        if (
            parts[1] == expected_key
            and parts[2] == expected_workspace_id
            and parts[3] == expected_agent_id
        ):
            scoped.append(address)
    if not scoped:
        raise AcceptanceError(
            "agent_identity_scope_mismatch",
            "the named agent was not in the expected peer workspace",
        )
    if len(scoped) != 1:
        raise AcceptanceError(
            "agent_identity_ambiguous",
            "the requested full agent identity was not unique",
        )
    return scoped[0]


def _check_remote_file(
    runner: Any,
    endpoint: Endpoint,
    relative_path: str,
    expected: bytes,
    *,
    expected_workspace: str,
) -> dict[str, Any]:
    source = r"""import hashlib,json,os,stat,sys
from pathlib import Path
root=Path(sys.argv[1]).resolve(strict=True)
rel=Path(sys.argv[2])
if rel.is_absolute() or '..' in rel.parts:
    raise SystemExit(4)
p=root/rel
st=p.lstat()
if not (stat.S_ISREG(st.st_mode) and st.st_uid==os.getuid() and st.st_nlink==1):
    raise SystemExit(5)
fd=os.open(p,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
opened=os.fstat(fd)
if not (
    stat.S_ISREG(opened.st_mode)
    and opened.st_uid==os.getuid()
    and opened.st_nlink==1
    and (opened.st_dev,opened.st_ino)==(st.st_dev,st.st_ino)
):
    os.close(fd)
    raise SystemExit(6)
with os.fdopen(fd,'rb') as stream:
    data=stream.read(1024*1024+1)
if len(data)>1024*1024:
    raise SystemExit(7)
print(json.dumps({'workspace':str(root),'path':str(p.resolve(strict=True)),'size':len(data),'sha256':hashlib.sha256(data).hexdigest(),'matches':data==bytes.fromhex(sys.argv[3])}))"""
    result = _require_success(
        runner.python(
            endpoint, source, [str(endpoint.workspace), relative_path, expected.hex()]
        ),
        endpoint.label + ".verify_file",
    )
    record = _json_object(result)
    expected_path = str(Path(expected_workspace) / relative_path)
    expected_hash = hashlib.sha256(expected).hexdigest()
    if (
        record.get("workspace") != expected_workspace
        or record.get("path") != expected_path
        or record.get("matches") is not True
        or record.get("size") != len(expected)
        or record.get("sha256") != expected_hash
    ):
        raise AcceptanceError(
            "remote_file_mismatch",
            "the remote file did not match the requested path and bytes",
        )
    return {
        "workspace": record["workspace"],
        "path": record["path"],
        "size": record["size"],
        "sha256": record["sha256"],
        "matches": True,
    }


def _file_exists(runner: Any, endpoint: Endpoint, relative_path: str) -> bool:
    source = r"""import json,sys
from pathlib import Path
root=Path(sys.argv[1]).resolve(strict=True)
rel=Path(sys.argv[2])
if rel.is_absolute() or '..' in rel.parts:
    raise SystemExit(4)
p=root/rel
print(json.dumps({'exists':p.exists() or p.is_symlink()}))"""
    text = _require_success(
        runner.python(endpoint, source, [str(endpoint.workspace), relative_path]),
        endpoint.label + ".file_absent",
    )
    return _json_object(text).get("exists") is True


def _inspect_ledger(
    runner: Any,
    endpoint: Endpoint,
    task_id: str | None = None,
    marker: str | None = None,
) -> dict[str, Any]:
    source = r"""import hashlib,json,os,sqlite3,stat,sys
from pathlib import Path
workspace=Path(sys.argv[1]).resolve(strict=True)
digest=hashlib.sha256(str(workspace).encode()).hexdigest()
root=Path.home()/".kollab"/"network"/digest
root_st=root.lstat()
if not (
    stat.S_ISDIR(root_st.st_mode)
    and root_st.st_uid==os.getuid()
    and stat.S_IMODE(root_st.st_mode)&0o077==0
): raise SystemExit(4)
dbpath=root/"conversations.sqlite3"
if not dbpath.exists():
    print(json.dumps({"tasks":[],"matches":[],"events":[],"outbound_grants":[],"outbound_queue":[]}))
    raise SystemExit(0)
st=dbpath.lstat()
if not (
    stat.S_ISREG(st.st_mode)
    and st.st_uid==os.getuid()
    and stat.S_IMODE(st.st_mode)&0o077==0
    and st.st_nlink==1
): raise SystemExit(5)
db=sqlite3.connect(dbpath.as_uri()+"?mode=ro",uri=True,timeout=2)
db.row_factory=sqlite3.Row
rows = db.execute(
    "SELECT id,state,payload FROM tasks ORDER BY created DESC LIMIT 1024"
).fetchall()
event_rows = db.execute(
    "SELECT id,state,payload FROM conversation_events ORDER BY created DESC LIMIT 2048"
).fetchall()
grant_rows = db.execute(
    "SELECT id,state,sender,recipient,purpose,expires "
    "FROM outbound_grants ORDER BY created DESC LIMIT 1024"
).fetchall()
outbound_rows = (
    db.execute(
        "SELECT id,state,peer,thread,kind FROM outbound_queue "
        "WHERE thread=? ORDER BY created DESC LIMIT 128",
        (sys.argv[2],),
    ).fetchall()
    if sys.argv[2]
    else []
)
db.close()
tasks=[]; matches=[]; events=[]; outbound_grants=[]; outbound_queue=[]
for row in rows:
    payload=json.loads(row["payload"])
    content=payload.get("content","")
    if not isinstance(content,str): continue
    item={"id":row["id"],"state":row["state"],"reply_to":payload.get("reply_to",""),"thread_id":payload.get("thread_id",row["id"]),"from":payload.get("from"),"to":payload.get("to"),"kind":payload.get("kind"),"content_sha256":hashlib.sha256(content.encode()).hexdigest()}
    if sys.argv[2] and row["id"]==sys.argv[2]:
        tasks.append(item)
    if sys.argv[4] and sys.argv[4] in content:
        if not sys.argv[2] or row["id"] != sys.argv[2]: tasks.append(item)
for row in event_rows:
    payload=json.loads(row["payload"])
    content=payload.get("content","")
    if not isinstance(content,str): continue
    item={"id":row["id"],"state":row["state"],"kind":payload.get("kind"),"reply_to":payload.get("reply_to",""),"thread_id":payload.get("thread_id",""),"from":payload.get("from"),"to":payload.get("to"),"from_identity":payload.get("from_identity"),"to_identity":payload.get("to_identity"),"content_sha256":hashlib.sha256(content.encode()).hexdigest()}
    if sys.argv[2] and (
        row["id"] == sys.argv[2]
        or payload.get("thread_id") == sys.argv[2]
        or payload.get("reply_to") == sys.argv[2]
    ):
        events.append(item)
    if (
        sys.argv[3]
        and payload.get("kind") == "result"
        and payload.get("reply_to") == sys.argv[3]
        and payload.get("thread_id") == sys.argv[3]
    ):
        matches.append(item)
    if sys.argv[4] and sys.argv[4] in content:
        events.append(item)
for row in grant_rows:
    purpose=row["purpose"]
    if not isinstance(purpose,str): continue
    if sys.argv[4] and sys.argv[4] not in purpose: continue
    outbound_grants.append({"id":row["id"],"state":row["state"],"sender":row["sender"],"recipient":row["recipient"],"expires":row["expires"],"purpose_sha256":hashlib.sha256(purpose.encode()).hexdigest()})
for row in outbound_rows:
    outbound_queue.append({"id":row["id"],"state":row["state"],"peer":row["peer"],"thread":row["thread"],"kind":row["kind"]})
print(json.dumps({"tasks":tasks,"matches":matches,"events":events,"outbound_grants":outbound_grants,"outbound_queue":outbound_queue}))"""
    text = _require_success(
        runner.python(
            endpoint,
            source,
            [str(endpoint.workspace), task_id or "", task_id or "", marker or ""],
        ),
        endpoint.label + ".conversation_ledger",
    )
    return _json_object(text)


def _submit_human_input(
    runner: Any,
    endpoint: Endpoint,
    message: str,
    *,
    relay_answer: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Submit an ordinary user turn through the endpoint's state.send_message RPC.

    The RPC source is ``STATE_RPC`` and enters ``llm.submit_human_input``;
    it therefore exercises the same human-input hooks as the app's state API.
    The prompt is sent on stdin, never as an argv value or report field.
    """
    match = re.fullmatch(
        r"(?:please\s+)?(?:ask|tell)\s+(relay:[^\s]+)\s+to\s+(.+)",
        message,
        re.I | re.S,
    )
    answer_scope_valid = False
    if relay_answer is not None:
        if (
            isinstance(relay_answer, dict)
            and set(relay_answer) == {"peer", "task_id", "question_id", "answer"}
            and isinstance(relay_answer.get("peer"), str)
            and RELAY_ADDRESS_RE.fullmatch(relay_answer["peer"])
            and ID_RE.fullmatch(str(relay_answer.get("task_id", "")))
            and ID_RE.fullmatch(str(relay_answer.get("question_id", "")))
            and isinstance(relay_answer.get("answer"), str)
            and 0 < len(relay_answer["answer"].encode("utf-8")) <= 512
        ):
            expected_answer_input = (
                f"Please answer pending relay question {relay_answer['question_id']} "
                f"in original task {relay_answer['task_id']} from "
                f"{relay_answer['peer']} with this exact content: "
                f"{relay_answer['answer']}"
            )
            answer_scope_valid = message == expected_answer_input
    if (
        (not match and not answer_scope_valid)
        or (relay_answer is not None and not answer_scope_valid)
        or len(message.encode("utf-8")) > 4096
        or any(
            ord(char) < 32 and char not in "\n\r\t" or ord(char) == 127
            for char in message
        )
    ):
        raise AcceptanceError(
            "human_input_invalid",
            "the ordinary human request was invalid or out of scope",
        )
    source = r"""import asyncio,json,os,re,socket,stat,sys,time
from pathlib import Path
workspace=Path(sys.argv[1]).resolve(strict=True)
identity=sys.argv[2]
request=json.load(sys.stdin.buffer)
message=request.get("message") if isinstance(request,dict) else None
if not isinstance(message,str) or not message.strip():
    print(json.dumps({"accepted":False,"reason":"invalid_input"})); raise SystemExit(0)
try:
    from kollabor_config.config_utils import encode_project_path,get_config_directory
    from kollabor_rpc import RpcClient,open_unix_connection_with_large_buffer
    from plugins.hub.relay_owner import WorkspaceRelayOwner
    root=get_config_directory()
    encoded=encode_project_path(workspace)
    candidates=(root/"hub"/"presence",root/"projects"/encoded/"hub"/"presence")
    owner_service=WorkspaceRelayOwner(workspace)
    owner=owner_service.owner()
    del owner_service
    if not isinstance(owner,dict) or set(owner)!={"socket_path","agent_id","pid"}:
        print(json.dumps({"accepted":False,"reason":"workspace_owner_unavailable"})); raise SystemExit(0)
    socket_path=owner.get("socket_path")
    owner_agent_id=owner.get("agent_id")
    owner_pid=owner.get("pid")
    if (
        not isinstance(socket_path,str) or not socket_path.startswith("/")
        or "\x00" in socket_path or len(os.fsencode(socket_path))>103
        or not isinstance(owner_agent_id,str) or not owner_agent_id
        or type(owner_pid) is not int or owner_pid<=0
    ):
        print(json.dumps({"accepted":False,"reason":"workspace_owner_invalid"})); raise SystemExit(0)
    try:
        os.kill(owner_pid,0)
        socket_stat=os.stat(socket_path,follow_symlinks=False)
    except OSError:
        print(json.dumps({"accepted":False,"reason":"workspace_owner_not_live"})); raise SystemExit(0)
    if (
        not stat.S_ISSOCK(socket_stat.st_mode) or socket_stat.st_uid!=os.getuid()
        or stat.S_IMODE(socket_stat.st_mode)&0o077
    ):
        print(json.dumps({"accepted":False,"reason":"workspace_owner_socket_invalid"})); raise SystemExit(0)
    matching=[]
    rows_seen=0
    for directory in candidates:
        try:
            directory_stat=directory.lstat()
        except FileNotFoundError:
            continue
        if (
            not stat.S_ISDIR(directory_stat.st_mode) or directory.is_symlink()
            or directory_stat.st_uid!=os.getuid()
            or stat.S_IMODE(directory_stat.st_mode)&0o077
        ):
            print(json.dumps({"accepted":False,"reason":"presence_directory_unsafe"})); raise SystemExit(0)
        for path in sorted(directory.glob("*.json")):
            rows_seen+=1
            if rows_seen>4096:
                print(json.dumps({"accepted":False,"reason":"presence_scan_limit"})); raise SystemExit(0)
            try:
                st=path.lstat()
                if not (
                    stat.S_ISREG(st.st_mode)
                    and st.st_uid == os.getuid()
                    and stat.S_IMODE(st.st_mode) & 0o077 == 0
                    and st.st_nlink == 1
                    and st.st_size<=65536
                ):
                    continue
                fd=os.open(path,os.O_RDONLY|getattr(os,"O_NOFOLLOW",0))
                with os.fdopen(fd,"rb") as stream: raw=stream.read(65537)
                value=json.loads(raw.decode("utf-8"))
            except (OSError,ValueError,UnicodeError): continue
            if isinstance(value,dict) and value.get("identity")==identity:
                matching.append(value)
    if len(matching)!=1:
        reason="agent_presence_missing" if not matching else "agent_socket_ambiguous"
        print(json.dumps({"accepted":False,"reason":reason})); raise SystemExit(0)
    record=matching[0]
    heartbeat=record.get("last_heartbeat")
    project=record.get("project")
    if (
        not isinstance(project,str) or not Path(project).is_absolute()
        or Path(project).resolve()!=workspace
    ):
        print(json.dumps({"accepted":False,"reason":"agent_workspace_mismatch"})); raise SystemExit(0)
    if (
        record.get("agent_id")!=owner_agent_id or record.get("pid")!=owner_pid
        or record.get("socket_path")!=socket_path
        or not isinstance(heartbeat,(int,float)) or isinstance(heartbeat,bool)
        or not 0<=time.time()-heartbeat<=60
    ):
        print(json.dumps({"accepted":False,"reason":"agent_owner_mismatch"})); raise SystemExit(0)
    async def submit():
        reader,writer=await open_unix_connection_with_large_buffer(socket_path)
        from plugins.hub.messenger import AgentSocketServer
        connected_stat=os.stat(socket_path,follow_symlinks=False)
        if (connected_stat.st_dev,connected_stat.st_ino)!=(socket_stat.st_dev,socket_stat.st_ino):
            writer.close()
            try: await writer.wait_closed()
            except (OSError,RuntimeError): pass
            return {"accepted":False,"reason":"workspace_owner_socket_changed"}
        credentials=AgentSocketServer._get_peer_credentials(writer)
        if (
            credentials is None
            or len(credentials)!=2
            or credentials[1]!=os.getuid()
            or (credentials[0] is not None and credentials[0]!=owner_pid)
        ):
            writer.close()
            try: await writer.wait_closed()
            except (OSError,RuntimeError): pass
            return {"accepted":False,"reason":"workspace_owner_peer_unverified"}
        rpc=RpcClient(writer,default_timeout=30)
        async def route_replies():
            while True:
                line=await reader.readline()
                if not line: return
                try: frame=json.loads(line.decode("utf-8"))
                except (ValueError,UnicodeError): continue
                if isinstance(frame,dict) and frame.get("action")=="rpc_reply": rpc.on_reply(frame)
        pump=asyncio.create_task(route_replies())
        try:
            result=await rpc.call("state.send_message",{"message":message},timeout=30)
            return result
        finally:
            rpc.close(); pump.cancel(); await asyncio.gather(pump,return_exceptions=True)
            writer.close()
            try: await writer.wait_closed()
            except (OSError,RuntimeError): pass
    result=asyncio.run(submit())
    accepted=isinstance(result,dict) and result.get("accepted") is True
    reason=result.get("reason","") if isinstance(result,dict) else "invalid_response"
    print(json.dumps({"accepted":accepted,"reason":reason if isinstance(reason,str) else "invalid_response"}))
except Exception:
    print(json.dumps({"accepted":False,"reason":"state_rpc_unavailable"}))
"""
    result = runner.python(
        endpoint,
        source,
        [str(endpoint.workspace), endpoint.agent],
        input_bytes=json.dumps({"message": message}, ensure_ascii=False).encode(
            "utf-8"
        ),
        timeout=45,
    )
    value = _json_object(_require_success(result, endpoint.label + ".human_input"))
    if value.get("accepted") is not True:
        raise AcceptanceError(
            "human_input_not_accepted", "the endpoint did not accept the human turn"
        )
    return {"accepted": True, "source": "state.send_message/STATE_RPC"}


def _wait_for_outbound_grant(
    runner: Any,
    endpoint: Endpoint,
    *,
    marker: str,
    purpose: str,
    recipient: str,
    deadline_seconds: int,
) -> dict[str, Any]:
    expected_hash = hashlib.sha256(purpose.encode("utf-8")).hexdigest()
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        ledger = _inspect_ledger(runner, endpoint, marker=marker)
        rows = ledger.get("outbound_grants")
        if isinstance(rows, list):
            matches = [
                row
                for row in rows
                if isinstance(row, dict)
                and row.get("recipient") == recipient
                and row.get("purpose_sha256") == expected_hash
                and isinstance(row.get("id"), str)
                and ID_RE.fullmatch(row["id"])
            ]
            if len(matches) > 1:
                raise AcceptanceError(
                    "human_grant_ambiguous",
                    "the human request created multiple contact grants",
                )
            if len(matches) == 1 and matches[0].get("state") in {"sent", "completed"}:
                return matches[0]
        time.sleep(0.25)
    raise AcceptanceError(
        "sender_hub_tool_missing",
        "the sender model did not consume the human contact grant through its Hub tool before the deadline",
    )


def _inspect_model_and_tool_trace(
    runner: Any,
    endpoint: Endpoint,
    *,
    marker: str,
    relative_path: str,
    expected_content: str,
) -> dict[str, Any]:
    source = r"""import hashlib,json,os,stat,sys
from pathlib import Path
workspace=Path(sys.argv[1]).resolve(strict=True)
marker=sys.argv[2]; rel=sys.argv[3]; expected=sys.argv[4]
encoded=str(workspace).replace("/","_").replace("\\","_").lstrip("_")
conversations=Path.home()/".kollab"/"projects"/encoded/"conversations"
provider_models=set(); model_turn=False; create_calls={}; read_calls={}; tool_results={}
def safe_read(path,limit):
    return trace_safe_read(path,limit)
def recent_paths(directory,pattern,count,limit):
    return trace_recent_paths(directory,pattern,count,limit)
for path in recent_paths(conversations,"*.jsonl",30,16*1024*1024):
    try: raw=safe_read(path,16*1024*1024)
    except OSError: continue
    for line in raw.splitlines():
        try: item=json.loads(line)
        except (ValueError,UnicodeError): continue
        content=item.get("content","")
        if item.get("type")=="system" and item.get("subtype")=="tool_result":
            tool_id=item.get("toolUseID") or item.get("tool_use_id")
            if isinstance(tool_id,str) and tool_id and isinstance(content,str):
                tool_results[tool_id]=content
rawdir=conversations/"raw"
if rawdir.is_dir() and not rawdir.is_symlink():
    for path in recent_paths(rawdir,"*_raw.jsonl",20,16*1024*1024):
        try: raw=safe_read(path,16*1024*1024)
        except OSError: continue
        for line in raw.splitlines():
            try: item=json.loads(line)
            except (ValueError,UnicodeError): continue
            request=item.get("request") or {}; response=item.get("response") or {}
            local=request.get("conversation_local") or []
            contains_marker=any(marker in str(msg.get("content","")) for msg in local if isinstance(msg,dict))
            if contains_marker:
                profile=item.get("profile") or {}
                provider=profile.get("provider",""); model=profile.get("model","")
                if provider and model: provider_models.add((str(provider),str(model)))
                if item.get("error") is None: model_turn=True
                for call in response.get("tool_calls") or []:
                    if not isinstance(call,dict): continue
                    name=str(call.get("name","")).lower()
                    inputs=call.get("input") or {}
                    tool_id=call.get("id")
                    if not isinstance(tool_id,str) or not tool_id or not isinstance(inputs,dict): continue
                    if name=="file_create" and inputs.get("file")==rel and inputs.get("content")==expected:
                        create_calls[tool_id]={"name":"file_create","id":tool_id}
                    if name=="file_read" and inputs.get("file")==rel:
                        read_calls[tool_id]={"name":"file_read","id":tool_id}
create_results=[]
for tool_id in create_calls:
    result=tool_results.get(tool_id,"")
    if rel in result and ("Created" in result or "created" in result):
        create_results.append({"id":tool_id,"path_matches":True})
read_results=[]
for tool_id in read_calls:
    result=tool_results.get(tool_id,"")
    if expected in result:
        read_results.append({"id":tool_id,"content_matches":True})
print(json.dumps({
    "model_turn": model_turn,
    "providers": [{"provider": p, "model": m} for p, m in sorted(provider_models)],
    "file_create_calls": list(create_calls.values()),
    "file_create_tool_results": create_results,
    "file_read_calls": list(read_calls.values()),
    "file_read_tool_results": read_results,
    "trace_scan": trace_scan_status(),
}))"""
    source = _TRACE_READER_SOURCE + source
    text = _require_success(
        runner.python(
            endpoint,
            source,
            [str(endpoint.workspace), marker, relative_path, expected_content],
        ),
        endpoint.label + ".model_tool_trace",
    )
    record = _json_object(text)
    return {
        "model_turn": record.get("model_turn") is True,
        "providers": record.get("providers", []),
        "file_create_calls": record.get("file_create_calls", []),
        "file_create_tool_results": record.get("file_create_tool_results", []),
        "file_read_calls": record.get("file_read_calls", []),
        "file_read_tool_results": record.get("file_read_tool_results", []),
        "trace_scan": record.get("trace_scan", {}),
    }


def _inspect_sender_hub_trace(
    runner: Any,
    endpoint: Endpoint,
    *,
    marker: str,
    destination: str,
    grant_id: str,
    purpose_sha256: str,
) -> dict[str, Any]:
    """Prove an actual sender-provider hub_msg call and its normal tool receipt."""
    if not ID_RE.fullmatch(grant_id) or not re.fullmatch(
        r"[0-9a-f]{64}", purpose_sha256
    ):
        raise AcceptanceError(
            "sender_trace_scope_invalid",
            "the sender trace correlation values were invalid",
        )
    source = r"""import hashlib,json,os,re,stat,sys
from pathlib import Path
workspace=Path(sys.argv[1]).resolve(strict=True)
marker=sys.argv[2]; target=sys.argv[3]; grant=sys.argv[4]; expected_hash=sys.argv[5]
encoded=str(workspace).replace("/","_").replace("\\","_").lstrip("_")
conversations=Path.home()/".kollab"/"projects"/encoded/"conversations"
provider_models=set(); model_turn=False; call_ids=set(); native_calls=0; xml_calls=0; result_ids=set()
def safe_read(path,limit):
    return trace_safe_read(path,limit)
def recent_paths(directory,pattern,count,limit):
    return trace_recent_paths(directory,pattern,count,limit)
def arguments(value):
    if isinstance(value,dict): return value
    if isinstance(value,str):
        try:
            parsed=json.loads(value)
            return parsed if isinstance(parsed,dict) else {}
        except (ValueError,TypeError): return {}
    return {}
for path in recent_paths(conversations/"raw","*_raw.jsonl",30,16*1024*1024):
    try: raw=safe_read(path,16*1024*1024)
    except OSError: continue
    for line in raw.splitlines():
        try: item=json.loads(line)
        except (ValueError,UnicodeError): continue
        request=item.get("request") or {}; response=item.get("response") or {}
        local=request.get("conversation_local") or []
        if not any(marker in str(msg.get("content","")) for msg in local if isinstance(msg,dict)): continue
        profile=item.get("profile") or {}; provider=profile.get("provider",""); model=profile.get("model","")
        if provider and model: provider_models.add((str(provider),str(model)))
        if item.get("error") is None: model_turn=True
        for call in response.get("tool_calls") or []:
            if not isinstance(call,dict): continue
            name=str(call.get("name","")).lower().replace("-","_")
            value=arguments(call.get("input",call.get("arguments",{})))
            to=value.get("to",value.get("target","")); content=value.get("message",value.get("content",""))
            thread=value.get("thread_id",value.get("thread",grant))
            if (
                name == "hub_msg"
                and to == target
                and thread == grant
                and isinstance(content, str)
                and hashlib.sha256(content.encode()).hexdigest() == expected_hash
            ):
                native_calls += 1
                call_id=str(call.get("id",""))
                if call_id: call_ids.add(call_id)
        content=response.get("content") or ""
        if isinstance(content,str):
            pattern = r"<hub_msg\s+to=(?:\"([^\"]+)\"|'([^']+)')([^>]*)>(.*?)</hub_msg>"
            for match in re.finditer(pattern,content,re.S):
                to=match.group(1) or match.group(2); attrs=match.group(3); body=match.group(4)
                thread_match = re.search(
                    r"(?:thread_id|thread)=(?:\"([^\"]+)\"|'([^']+)')", attrs
                )
                thread=(thread_match.group(1) or thread_match.group(2)) if thread_match else grant
                if (
                    to == target
                    and thread == grant
                    and hashlib.sha256(body.strip().encode()).hexdigest()
                    == expected_hash
                ):
                    xml_calls += 1
for path in recent_paths(conversations,"*.jsonl",30,16*1024*1024):
    try: raw=safe_read(path,16*1024*1024)
    except OSError: continue
    for line in raw.splitlines():
        try: item=json.loads(line)
        except (ValueError,UnicodeError): continue
        if item.get("type")!="system" or item.get("subtype")!="tool_result": continue
        content=item.get("content","")
        if (
            not isinstance(content, str)
            or grant not in content
            or not (
                "remote task" in content.lower()
                or "remote receipt" in content.lower()
            )
        ):
            continue
        tool_id=str(item.get("toolUseID",""))
        if tool_id and tool_id in call_ids:
            result_ids.add(tool_id)
        elif not call_ids and xml_calls:
            result_ids.add(tool_id or "xml-tool-result")
print(json.dumps({
    "model_turn": model_turn,
    "providers": [
        {"provider": provider, "model": model}
        for provider, model in sorted(provider_models)
    ],
    "matching_hub_msg_calls": native_calls + xml_calls,
    "matching_tool_results": len(result_ids),
    "receipt_id": grant if result_ids else "",
    "trace_scan": trace_scan_status(),
}))"""
    source = _TRACE_READER_SOURCE + source
    text = _require_success(
        runner.python(
            endpoint,
            source,
            [
                str(endpoint.workspace),
                marker,
                destination,
                grant_id,
                purpose_sha256,
            ],
        ),
        endpoint.label + ".sender_model_tool_trace",
    )
    record = _json_object(text)
    return {
        "model_turn": record.get("model_turn") is True,
        "providers": record.get("providers", []),
        "matching_hub_msg_calls": int(record.get("matching_hub_msg_calls", 0)),
        "matching_tool_results": int(record.get("matching_tool_results", 0)),
        "receipt_id": (
            record.get("receipt_id")
            if ID_RE.fullmatch(str(record.get("receipt_id", "")))
            else ""
        ),
        "trace_scan": record.get("trace_scan", {}),
    }


def _wait_for_sender_hub_trace(
    runner: Any,
    endpoint: Endpoint,
    *,
    marker: str,
    destination: str,
    grant_id: str,
    purpose_sha256: str,
    deadline_seconds: int,
) -> dict[str, Any]:
    """Wait for the normal provider call and its exact Hub tool receipt to persist."""
    deadline = time.monotonic() + deadline_seconds
    last_trace: dict[str, Any] = {}
    while time.monotonic() < deadline:
        trace = _inspect_sender_hub_trace(
            runner,
            endpoint,
            marker=marker,
            destination=destination,
            grant_id=grant_id,
            purpose_sha256=purpose_sha256,
        )
        last_trace = trace
        calls = trace.get("matching_hub_msg_calls", 0)
        receipts = trace.get("matching_tool_results", 0)
        if not isinstance(calls, int) or not isinstance(receipts, int):
            raise AcceptanceError(
                "question_answer_initial_hub_call_invalid",
                "the sender Hub tool trace was malformed",
            )
        if calls > 1 or receipts > 1:
            raise AcceptanceError(
                "question_answer_initial_hub_call_ambiguous",
                "the sender model produced duplicate Hub task calls or receipts",
            )
        receipt_id = trace.get("receipt_id")
        if receipt_id and receipt_id != grant_id:
            raise AcceptanceError(
                "question_answer_initial_hub_call_mismatch",
                "the sender Hub receipt did not match the authorized task ID",
            )
        if calls == 1 and receipts == 1:
            if (
                trace.get("model_turn") is not True
                or not trace.get("providers")
                or receipt_id != grant_id
            ):
                raise AcceptanceError(
                    "question_answer_initial_hub_call_invalid",
                    "the sender Hub task call lacked a successful provider receipt",
                )
            return trace
        time.sleep(0.25)
    if not _trace_scan_complete(last_trace):
        raise AcceptanceError(
            "trace_evidence_insufficient",
            "the bounded sender trace window could not prove the required Hub call and receipt",
        )
    raise AcceptanceError(
        "question_answer_initial_hub_call_deadline",
        "the sender provider Hub call and matching tool receipt were not recorded before the deadline",
    )


def _inspect_question_answer_trace(
    runner: Any,
    endpoint: Endpoint,
    *,
    side: str,
    marker: str,
    purpose: str,
    task_id: str,
    question_id: str,
    question_text: str,
    answer_event_id: str,
    answer_text: str,
    human_initial_prompt: str,
    human_answer_prompt: str,
    relative_path: str,
    peer_address: str,
    question_hud: str,
    answer_hud: str,
) -> dict[str, Any]:
    """Verify both provider wire events and native question/answer tool receipts."""
    if (
        side not in {"sender", "receiver"}
        or not marker.startswith("KOLLAB_RELAY_QUESTION_")
        or not purpose
        or len(purpose.encode("utf-8")) > 8192
        or not ID_RE.fullmatch(task_id)
        or not ID_RE.fullmatch(question_id)
        or not ID_RE.fullmatch(answer_event_id)
        or len(question_text.encode("utf-8")) > 512
        or not answer_text
        or len(answer_text.encode("utf-8")) > 512
        or len(human_initial_prompt.encode("utf-8")) > 4096
        or len(human_answer_prompt.encode("utf-8")) > 4096
        or not relative_path
        or Path(relative_path).is_absolute()
        or ".." in Path(relative_path).parts
        or not RELAY_ADDRESS_RE.fullmatch(peer_address)
        or len(question_hud.encode("utf-8")) > 4096
        or len(answer_hud.encode("utf-8")) > 4096
    ):
        raise AcceptanceError(
            "question_answer_trace_scope_invalid",
            "the correlated question-answer trace scope was invalid",
        )
    source = r"""import json
import os
import re
import stat
import sys
from pathlib import Path

workspace = Path(sys.argv[1]).resolve(strict=True)
(
    side,
    marker,
    purpose,
    task_id,
    question_id,
    question_text,
    answer_event_id,
    answer_text,
    human_initial_prompt,
    human_answer_prompt,
    relative_path,
    peer_address,
    question_hud,
    answer_hud,
    agent,
) = sys.argv[2:17]
encoded = str(workspace).replace("/", "_").replace("\\", "_").lstrip("_")
conversations = Path.home() / ".kollab" / "projects" / encoded / "conversations"
tool_results = {}
providers = set()
question_calls = []
question_receipts = []
answer_calls = []
answer_receipts = []
file_create_calls = []
file_create_results = []
file_read_calls = []
file_read_results = []
file_order = []
purpose_wire_requests = 0
initial_sender_wire_requests = 0
question_wire_requests = 0
answer_wire_requests = 0
unexpected_tool_calls = 0
provider_turns = 0


def safe_read(path, limit):
    return trace_safe_read(path, limit)


def recent_paths(directory, pattern, count, limit):
    return trace_recent_paths(directory, pattern, count, limit)


def user_texts(value):
    found = []
    pending = [(value, False, 0)]
    visited = 0
    while pending and visited < 50000:
        current, under_user, depth = pending.pop()
        visited += 1
        if depth > 32:
            continue
        if isinstance(current, dict):
            is_user = under_user or current.get("role") == "user"
            for key, child in current.items():
                if key != "role":
                    pending.append((child, is_user, depth + 1))
        elif isinstance(current, list):
            pending.extend((child, under_user, depth + 1) for child in current)
        elif under_user and isinstance(current, str) and len(current) <= 131072:
            found.append(current)
    return found


def arguments(call):
    value = call.get("input", call.get("arguments", {}))
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


for path in recent_paths(conversations, "*.jsonl", 40, 16 * 1024 * 1024):
    try:
        raw = safe_read(path, 16 * 1024 * 1024)
    except OSError:
        continue
    for line in raw.splitlines():
        try:
            item = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if item.get("type") != "system" or item.get("subtype") != "tool_result":
            continue
        tool_id = item.get("toolUseID") or item.get("tool_use_id")
        content = item.get("content", "")
        if isinstance(tool_id, str) and tool_id and isinstance(content, str):
            tool_results.setdefault(tool_id, []).append(content[:131072])


report = {
    "side": side,
    "providers": [],
    "provider_turns": 0,
    "sender_initial_wire_requests": 0,
    "receiver_initial_wire_requests": 0,
    "sender_question_wire_requests": 0,
    "sender_answer_wire_requests": 0,
    "receiver_answer_wire_requests": 0,
    "question_call_count": 0,
    "question_receipt_count": 0,
    "question_receipt_id": "",
    "question_receipt_state": "",
    "answer_call_count": 0,
    "answer_receipt_count": 0,
    "answer_receipt_id": "",
    "answer_receipt_state": "",
    "file_create_call_count": 0,
    "file_create_result_count": 0,
    "file_read_call_count": 0,
    "file_read_result_count": 0,
    "file_tools_in_order": False,
    "unexpected_tool_call_count": 0,
}

raw_records = []
for path in recent_paths(conversations / "raw", "*_raw.jsonl", 40, 16 * 1024 * 1024):
    try:
        raw = safe_read(path, 16 * 1024 * 1024)
    except OSError:
        continue
    for line_number, line in enumerate(raw.splitlines()):
        try:
            item = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        raw_records.append((path.name, line_number, item))

for record_index, (_name, _line_number, item) in enumerate(raw_records):
    request = item.get("request") or {}
    response = item.get("response") or {}
    wire_text = "\n".join(user_texts(request.get("wire_request")))
    local_text = "\n".join(user_texts(request.get("conversation_local")))
    relevant = (
        marker in wire_text
        or marker in local_text
        or question_hud in wire_text
        or question_hud in local_text
        or answer_hud in wire_text
        or answer_hud in local_text
        or human_initial_prompt in wire_text
        or human_initial_prompt in local_text
        or human_answer_prompt in wire_text
        or human_answer_prompt in local_text
    )
    if side == "sender" and human_initial_prompt in wire_text:
        initial_sender_wire_requests += 1
    if side == "sender" and question_hud in wire_text:
        question_wire_requests += 1
    answer_wire = False
    if side == "sender":
        answer_wire = question_hud in wire_text and human_answer_prompt in wire_text
        if answer_wire:
            answer_wire_requests += 1
    elif side == "receiver":
        answer_wire = answer_hud in wire_text
        if answer_wire:
            answer_wire_requests += 1
    if not relevant:
        continue

    if item.get("error") is None:
        profile = item.get("profile") or {}
        provider = profile.get("provider", "")
        model = profile.get("model", "")
        if provider and model:
            providers.add((str(provider), str(model)))
        if isinstance(response, dict):
            provider_turns += 1

    calls = response.get("tool_calls") or []
    qualified_question_request = False
    for call_index, call in enumerate(calls):
        if not isinstance(call, dict):
            continue
        name = str(call.get("name", "")).lower().replace("-", "_")
        values = arguments(call)
        tool_id = call.get("id")
        if not isinstance(tool_id, str) or not tool_id or not isinstance(values, dict):
            continue
        if name not in {"hub_msg", "file_create", "file_read"}:
            unexpected_tool_calls += 1
        target = values.get("to", values.get("target", ""))
        content = values.get("message", values.get("content", ""))
        call_thread = values.get("thread_id", values.get("thread", ""))
        call_reply = values.get("reply_to", "")
        call_kind = str(values.get("kind", "message") or "message").lower()
        if (
            side == "receiver"
            and purpose in wire_text
            and name == "hub_msg"
            and target == peer_address
            and call_kind == "question"
            and call_thread == task_id
            and call_reply == task_id
            and content == question_text
        ):
            question_calls.append((record_index, call_index, tool_id))
            qualified_question_request = True
        if (
            side == "sender"
            and answer_wire
            and name == "hub_msg"
            and target == peer_address
            and call_kind == "answer"
            and call_thread == task_id
            and call_reply == question_id
            and content == answer_text
        ):
            answer_calls.append((record_index, call_index, tool_id))
        if side == "receiver" and answer_wire and name == "file_create":
            if values.get("file") == relative_path and values.get("content") == answer_text:
                file_create_calls.append((record_index, call_index, tool_id))
        if side == "receiver" and answer_wire and name == "file_read":
            if values.get("file") == relative_path:
                file_read_calls.append((record_index, call_index, tool_id))
    if side == "receiver" and purpose in wire_text and qualified_question_request:
        purpose_wire_requests += 1

def exact_receipt(calls, event_id, state):
    matched = []
    expected = f"remote task {event_id}: {state}; acceptance is not completion"
    for _record_index, _call_index, tool_id in calls:
        results = tool_results.get(tool_id, [])
        if len(results) == 1 and expected in results[0]:
            matched.append(tool_id)
    return matched


question_receipts = exact_receipt(question_calls, question_id, "pending")
answer_receipts = exact_receipt(answer_calls, answer_event_id, "running")
for _record_index, _call_index, tool_id in file_create_calls:
    results = tool_results.get(tool_id, [])
    if len(results) == 1 and relative_path in results[0] and re.search(r"creat", results[0], re.I):
        file_create_results.append(tool_id)
for _record_index, _call_index, tool_id in file_read_calls:
    results = tool_results.get(tool_id, [])
    if len(results) == 1 and answer_text in results[0]:
        file_read_results.append(tool_id)

if len(file_create_calls) == 1 and len(file_read_calls) == 1:
    create_position = file_create_calls[0][:2]
    read_position = file_read_calls[0][:2]
    report["file_tools_in_order"] = create_position < read_position

report.update(
    {
        "providers": [
            {"provider": provider, "model": model}
            for provider, model in sorted(providers)
        ],
        "provider_turns": provider_turns,
        "sender_initial_wire_requests": initial_sender_wire_requests,
        "receiver_initial_wire_requests": purpose_wire_requests,
        "sender_question_wire_requests": question_wire_requests,
        "sender_answer_wire_requests": answer_wire_requests if side == "sender" else 0,
        "receiver_answer_wire_requests": answer_wire_requests if side == "receiver" else 0,
        "question_call_count": len(question_calls),
        "question_receipt_count": len(question_receipts),
        "question_receipt_id": question_id if question_receipts else "",
        "question_receipt_state": "pending" if question_receipts else "",
        "answer_call_count": len(answer_calls),
        "answer_receipt_count": len(answer_receipts),
        "answer_receipt_id": answer_event_id if answer_receipts else "",
        "answer_receipt_state": "running" if answer_receipts else "",
        "file_create_call_count": len(file_create_calls),
        "file_create_result_count": len(file_create_results),
        "file_read_call_count": len(file_read_calls),
        "file_read_result_count": len(file_read_results),
        "unexpected_tool_call_count": unexpected_tool_calls,
    }
)
report["trace_scan"] = trace_scan_status()
print(json.dumps(report))"""
    source = _TRACE_READER_SOURCE + source
    text = _require_success(
        runner.python(
            endpoint,
            source,
            [
                str(endpoint.workspace),
                side,
                marker,
                purpose,
                task_id,
                question_id,
                question_text,
                answer_event_id,
                answer_text,
                human_initial_prompt,
                human_answer_prompt,
                relative_path,
                peer_address,
                question_hud,
                answer_hud,
                endpoint.agent,
            ],
        ),
        endpoint.label + ".question_answer_trace",
    )
    record = _json_object(text)
    int_fields = (
        "provider_turns",
        "sender_initial_wire_requests",
        "receiver_initial_wire_requests",
        "sender_question_wire_requests",
        "sender_answer_wire_requests",
        "receiver_answer_wire_requests",
        "question_call_count",
        "question_receipt_count",
        "answer_call_count",
        "answer_receipt_count",
        "file_create_call_count",
        "file_create_result_count",
        "file_read_call_count",
        "file_read_result_count",
        "unexpected_tool_call_count",
    )
    evidence = {field: record.get(field, 0) for field in int_fields}
    for field in int_fields:
        try:
            evidence[field] = int(evidence[field])
        except (TypeError, ValueError):
            evidence[field] = 0
    evidence.update(
        {
            "side": side,
            "providers": record.get("providers", []),
            "question_receipt_id": record.get("question_receipt_id", ""),
            "question_receipt_state": record.get("question_receipt_state", ""),
            "answer_receipt_id": record.get("answer_receipt_id", ""),
            "answer_receipt_state": record.get("answer_receipt_state", ""),
            "file_tools_in_order": record.get("file_tools_in_order") is True,
            "trace_scan": record.get("trace_scan", {}),
        }
    )
    return evidence


def _inspect_sender_result_consumption(
    runner: Any,
    endpoint: Endpoint,
    *,
    event_id: str,
    task_id: str,
    content_sha256: str,
    local_address: str,
    remote_address: str,
    run_marker: str,
    relative_path: str,
    expected_content: str,
) -> dict[str, Any]:
    """Prove the sender provider received the result and answered from it.

    The remote result body stays on the endpoint. This helper joins its private
    event row to a Hub-originated user message in the provider request, then
    checks that the response from that same provider call reflects the unique
    acceptance marker, relative path, and exact file bytes.
    """
    if (
        not ID_RE.fullmatch(event_id)
        or not ID_RE.fullmatch(task_id)
        or not re.fullmatch(r"[0-9a-f]{64}", content_sha256)
        or not local_address.startswith("relay:")
        or not remote_address.startswith("relay:")
        # The core file task and the question/answer task both end in a result.
        or not run_marker.startswith(
            ("KOLLAB_RELAY_ACCEPTANCE_", "KOLLAB_RELAY_QUESTION_")
        )
        or not relative_path
        or Path(relative_path).is_absolute()
        or ".." in Path(relative_path).parts
        or len(expected_content.encode("utf-8")) > 4096
    ):
        raise AcceptanceError(
            "sender_result_scope_invalid",
            "the sender result correlation values were invalid",
        )
    source = r"""import hashlib
import json
import os
import re
import sqlite3
import stat
import sys
from pathlib import Path
workspace = Path(sys.argv[1]).resolve(strict=True)
(
    event_id,
    task_id,
    expected_hash,
    local_address,
    remote_address,
    agent,
    marker,
    rel,
    expected,
) = sys.argv[2:11]
encoded = str(workspace).replace("/", "_").replace("\\", "_").lstrip("_")
conversations = Path.home() / ".kollab" / "projects" / encoded / "conversations"
report = {
    "sender_event_ledger_match": False,
    "local_request_contains_result": False,
    "provider_wire_request_contains_result": False,
    "provider_request_contains_result": False,
    "assistant_response_reflects_result": False,
    "providers": [],
    "matching_provider_requests": 0,
    "matching_responses": 0,
    "result_message_count": 0,
    "complete_hub_metadata_observed": False,
}


def safe_read(path, limit):
    return trace_safe_read(path, limit)


def recent_paths(directory, pattern, count, limit):
    return trace_recent_paths(directory, pattern, count, limit)


def user_texts(value):
    found = []
    pending = [(value, False, 0)]
    visited = 0
    while pending and visited < 50000:
        current, under_user, depth = pending.pop()
        visited += 1
        if depth > 32:
            continue
        if isinstance(current, dict):
            is_user = under_user or current.get("role") == "user"
            for key, child in current.items():
                if key != "role":
                    pending.append((child, is_user, depth + 1))
        elif isinstance(current, list):
            pending.extend((child, under_user, depth + 1) for child in current)
        elif under_user and isinstance(current, str) and len(current) <= 131072:
            found.append(current)
    return found


def normalize_hud_label(value, fallback="info"):
    cleaned = []
    for char in (value or fallback).strip():
        if char.isalnum() or char in {"_", "-", ".", ":", ">", "/"}:
            cleaned.append(char)
        elif cleaned and cleaned[-1] != "_":
            cleaned.append("_")
    return "".join(cleaned).strip("_") or fallback


def format_hud_entry(section, label, content):
    body = (content or "").strip()
    if not body:
        formatted = "+"
    else:
        lines = body.splitlines()
        formatted = "+ " + lines[0]
        formatted += "".join("\n  " + line for line in lines[1:])
    return (
        f"[{normalize_hud_label(section, fallback='state')}:{normalize_hud_label(label)}]"
        f"\n{formatted}"
    )


def has_exact_event_context(text):
    for context_match in re.finditer(
        r"\[relay event context: ([^\]\r\n]+)\]", text
    ):
        pairs = re.findall(r"([a-z_]+)=([^\s\]]+)", context_match.group(1))
        fields = dict(pairs)
        if len(fields) != len(pairs):
            continue
        if (
            fields.get("kind") == "result"
            and fields.get("event_id") == event_id
            and fields.get("thread_id") == task_id
            and fields.get("reply_to") == event_id
            and fields.get("parent_reply_to") == task_id
            and fields.get("peer") == remote_address
        ):
            return True
    return False


def contains_exact_result_envelope(text):
    # Hub messages are queued as Agent HUD entries before the next provider
    # turn. Match the formatter's exact line prefixes instead of stripping
    # arbitrary indentation, which could hide a changed or malformed body.
    formatted_hub_message = format_hud_entry(
        "hub",
        f"{remote_address}->{agent}",
        f"{header}\n{injected}\n{event_context}",
    )
    return formatted_hub_message in text and has_exact_event_context(text)


try:
    digest = hashlib.sha256(str(workspace).encode()).hexdigest()
    root = Path.home() / ".kollab" / "network" / digest
    root_st = root.lstat()
    dbpath = root / "conversations.sqlite3"
    db_st = dbpath.lstat()
    if not (
        stat.S_ISDIR(root_st.st_mode)
        and root_st.st_uid == os.getuid()
        and stat.S_IMODE(root_st.st_mode) & 0o077 == 0
        and stat.S_ISREG(db_st.st_mode)
        and db_st.st_uid == os.getuid()
        and stat.S_IMODE(db_st.st_mode) & 0o077 == 0
        and db_st.st_nlink == 1
    ):
        raise OSError("private ledger unavailable")
    db = sqlite3.connect(dbpath.as_uri() + "?mode=ro", uri=True, timeout=2)
    row = db.execute(
        "SELECT id,state,payload FROM conversation_events WHERE id=?", (event_id,)
    ).fetchone()
    db.close()
    if row is not None:
        payload = json.loads(row[2])
        body = payload.get("content")
        report["sender_event_ledger_match"] = bool(
            row[0] == event_id
            and row[1] in {"received", "delivered"}
            and payload.get("id") == event_id
            and payload.get("kind") == "result"
            and payload.get("thread_id") == task_id
            and payload.get("reply_to") == task_id
            and payload.get("from") == remote_address
            and payload.get("to") == local_address
            and isinstance(body, str)
            and len(body.encode("utf-8")) <= 32768
            and hashlib.sha256(body.encode("utf-8")).hexdigest() == expected_hash
            and marker in body
            and rel in body
            and expected in body
        )
        if report["sender_event_ledger_match"]:
            injected = f"[relay result] {body}"
            header = (
                f"[hub channel: {remote_address} -> {agent} "
                f"[thread:{task_id[:8]}] [reply-to:{event_id[:8]}]]"
            )
            event_context = (
                f"[relay event context: kind=result event_id={event_id} "
                f"thread_id={task_id} reply_to={event_id} "
                f"parent_reply_to={task_id} peer={remote_address}]"
            )
            metadata_keys = {
                "hub_message_id": event_id,
                "hub_thread_id": task_id,
                "hub_reply_to": event_id,
                "hub_from": remote_address,
            }
            provider_models = set()
            successful_responses = 0
            matching_requests = 0
            local_requests = 0
            wire_requests = 0
            matching_message_count = 0
            complete_metadata_observed = False
            for path in recent_paths(
                conversations / "raw", "*_raw.jsonl", 30, 16 * 1024 * 1024
            ):
                try:
                    raw = safe_read(path, 16 * 1024 * 1024)
                except OSError:
                    continue
                for line in raw.splitlines():
                    try:
                        item = json.loads(line)
                    except (ValueError, UnicodeError):
                        continue
                    request = item.get("request") or {}
                    response = item.get("response") or {}
                    local = request.get("conversation_local") or []
                    matches = []
                    local_meta_ok = True
                    complete_message_metadata = False
                    for msg in local:
                        if (
                            not isinstance(msg, dict)
                            or msg.get("role") != "user"
                            or not isinstance(msg.get("content"), str)
                        ):
                            continue
                        content = msg["content"]
                        if not contains_exact_result_envelope(content):
                            continue
                        meta = (
                            msg.get("metadata")
                            if isinstance(msg.get("metadata"), dict)
                            else {}
                        )
                        message_metadata_seen = set()
                        for key, value in metadata_keys.items():
                            actual = meta.get(key, msg.get(key))
                            if actual is not None:
                                message_metadata_seen.add(key)
                                if actual != value:
                                    local_meta_ok = False
                        if message_metadata_seen == set(metadata_keys):
                            complete_message_metadata = True
                        sources = meta.get(
                            "agent_hud_sources", msg.get("agent_hud_sources", [])
                        )
                        agent_hud = meta.get(
                            "agent_hud", msg.get("agent_hud", False)
                        )
                        if (
                            agent_hud is not True
                            or not isinstance(sources, list)
                            or "hub" not in sources
                        ):
                            local_meta_ok = False
                        matches.append(msg)
                    if not matches or not local_meta_ok:
                        continue
                    local_requests += 1
                    wire_texts = user_texts(request.get("wire_request"))
                    wire_matches = [
                        text for text in wire_texts
                        if contains_exact_result_envelope(text)
                    ]
                    if len(wire_matches) != 1:
                        continue
                    wire_requests += 1
                    matching_requests += 1
                    matching_message_count = max(
                        matching_message_count, len(matches)
                    )
                    if complete_message_metadata:
                        complete_metadata_observed = True
                    profile = item.get("profile") or {}
                    provider = profile.get("provider", "")
                    model = profile.get("model", "")
                    if provider and model:
                        provider_models.add((str(provider), str(model)))
                    answer = response.get("content")
                    if (
                        item.get("error") is None
                        and isinstance(answer, str)
                        and marker in answer
                        and rel in answer
                        and expected in answer
                    ):
                        successful_responses += 1
            report["provider_request_contains_result"] = (
                matching_requests > 0 and matching_message_count == 1
            )
            report["local_request_contains_result"] = local_requests > 0
            report["provider_wire_request_contains_result"] = wire_requests > 0
            report["assistant_response_reflects_result"] = successful_responses > 0
            report["providers"] = [
                {"provider": provider, "model": model}
                for provider, model in sorted(provider_models)
            ]
            report["matching_provider_requests"] = matching_requests
            report["matching_responses"] = successful_responses
            report["result_message_count"] = matching_message_count
            report["complete_hub_metadata_observed"] = complete_metadata_observed
except (OSError, sqlite3.Error, ValueError, TypeError, UnicodeError):
    pass
print(json.dumps(report))"""
    source = _TRACE_READER_SOURCE + source
    text = _require_success(
        runner.python(
            endpoint,
            source,
            [
                str(endpoint.workspace),
                event_id,
                task_id,
                content_sha256,
                local_address,
                remote_address,
                endpoint.agent,
                run_marker,
                relative_path,
                expected_content,
            ],
        ),
        endpoint.label + ".sender_result_consumption",
    )
    record = _json_object(text)
    return {
        "sender_event_ledger_match": record.get("sender_event_ledger_match") is True,
        "local_request_contains_result": record.get("local_request_contains_result")
        is True,
        "provider_wire_request_contains_result": record.get(
            "provider_wire_request_contains_result"
        )
        is True,
        "provider_request_contains_result": record.get(
            "provider_request_contains_result"
        )
        is True,
        "assistant_response_reflects_result": record.get(
            "assistant_response_reflects_result"
        )
        is True,
        "providers": record.get("providers", []),
        "matching_provider_requests": int(record.get("matching_provider_requests", 0)),
        "matching_responses": int(record.get("matching_responses", 0)),
        "result_message_count": int(record.get("result_message_count", 0)),
        "complete_hub_metadata_observed": record.get("complete_hub_metadata_observed")
        is True,
        "trace_scan": record.get("trace_scan", {}),
        "event_id": event_id,
        "task_id": task_id,
        "content_sha256": content_sha256,
    }


def _wait_for_sender_result_consumption(
    runner: Any,
    endpoint: Endpoint,
    *,
    deadline_seconds: int,
    **scope: Any,
) -> dict[str, Any]:
    deadline = time.monotonic() + min(max(deadline_seconds, 1), 180)
    evidence: dict[str, Any] = {}
    while time.monotonic() < deadline:
        evidence = _inspect_sender_result_consumption(runner, endpoint, **scope)
        if (
            evidence.get("sender_event_ledger_match") is True
            and evidence.get("local_request_contains_result") is True
            and evidence.get("provider_wire_request_contains_result") is True
            and evidence.get("provider_request_contains_result") is True
            and evidence.get("assistant_response_reflects_result") is True
            and evidence.get("providers")
        ):
            return evidence
        time.sleep(0.5)
    if not _trace_scan_complete(evidence):
        raise AcceptanceError(
            "trace_evidence_insufficient",
            "the bounded sender trace window could not prove result consumption",
        )
    raise AcceptanceError(
        "sender_result_not_consumed",
        "the sender provider did not consume and answer from the correlated result",
    )


def _correlated_reply(
    ledger: dict[str, Any], *, task_id: str, local_address: str, remote_address: str
) -> dict[str, Any]:
    if not ID_RE.fullmatch(task_id):
        raise AcceptanceError(
            "task_correlation_mismatch", "the task identifier was invalid"
        )
    rows = ledger.get("matches")
    if not isinstance(rows, list):
        raise AcceptanceError(
            "correlated_reply_missing",
            "no unique reply correlated to the accepted task was received",
        )
    matches = [
        row
        for row in rows
        if isinstance(row, dict)
        and row.get("reply_to") == task_id
        and row.get("from") == remote_address
        and row.get("to") == local_address
        and row.get("thread_id") == task_id
        and row.get("state") == "completed"
        and isinstance(row.get("id"), str)
        and ID_RE.fullmatch(row["id"])
        and isinstance(row.get("content_sha256"), str)
        and re.fullmatch(r"[0-9a-f]{64}", row["content_sha256"])
    ]
    if len(matches) != 1:
        raise AcceptanceError(
            "correlated_reply_missing",
            "no unique reply correlated to the accepted task was received",
        )
    match = matches[0]
    return {
        "message_id": match["id"],
        "reply_to": task_id,
        "state": match["state"],
        "content_sha256": match["content_sha256"],
    }


def _run_file_exchange(
    *,
    runner: Any,
    sender: Endpoint,
    receiver: Endpoint,
    recipient_address: str,
    sender_address: str,
    run_marker: str,
    relative_path: str,
    expected_content: str,
    expected_workspace: str,
    deadline_seconds: int,
) -> dict[str, Any]:
    """Exercise human input → both providers/tools → exact file → correlated result."""
    if _file_exists(runner, receiver, relative_path):
        raise AcceptanceError(
            "target_exists", "the unique test file already exists; no request was sent"
        )
    purpose = _test_payload(run_marker, relative_path, expected_content)
    human_text = f"Please ask {recipient_address} to {purpose}"
    human_evidence = _submit_human_input(runner, sender, human_text)
    grant = _wait_for_outbound_grant(
        runner,
        sender,
        marker=run_marker,
        purpose=purpose,
        recipient=recipient_address,
        deadline_seconds=min(deadline_seconds, 180),
    )
    task_id = grant["id"]
    receipt = _wait_for_task(
        runner,
        sender,
        recipient_address,
        task_id,
        deadline_seconds=deadline_seconds,
    )
    sender_trace = _inspect_sender_hub_trace(
        runner,
        sender,
        marker=run_marker,
        destination=recipient_address,
        grant_id=task_id,
        purpose_sha256=grant["purpose_sha256"],
    )
    if (
        not sender_trace["model_turn"]
        or not sender_trace["providers"]
        or sender_trace["matching_hub_msg_calls"] < 1
        or sender_trace["matching_tool_results"] < 1
        or sender_trace["receipt_id"] != task_id
    ):
        if not _trace_scan_complete(sender_trace):
            raise AcceptanceError(
                "trace_evidence_insufficient",
                "the bounded sender trace window could not prove the Hub call and receipt",
            )
        raise AcceptanceError(
            "sender_model_tool_trace_missing",
            "the sender provider and normal Hub tool did not produce a matching task receipt",
        )
    if receipt.get("state") != "completed":
        raise AcceptanceError(
            "file_task_incomplete",
            "the remote agent did not complete the file-create request",
        )
    file_evidence = _check_remote_file(
        runner,
        receiver,
        relative_path,
        expected_content.encode("utf-8"),
        expected_workspace=expected_workspace,
    )
    receiver_trace = _inspect_model_and_tool_trace(
        runner,
        receiver,
        marker=run_marker,
        relative_path=relative_path,
        expected_content=expected_content,
    )
    if (
        not receiver_trace["model_turn"]
        or not receiver_trace["providers"]
        or not receiver_trace["file_create_calls"]
        or not receiver_trace["file_create_tool_results"]
        or not receiver_trace["file_read_calls"]
        or not receiver_trace["file_read_tool_results"]
    ):
        if not _trace_scan_complete(receiver_trace):
            raise AcceptanceError(
                "trace_evidence_insufficient",
                "the bounded receiver trace window could not prove file tool execution",
            )
        raise AcceptanceError(
            "receiver_model_tool_trace_missing",
            "the receiver provider and normal file tool did not produce a matching tool result",
        )
    receiver_ledger = _inspect_ledger(runner, receiver, task_id=task_id)
    sender_ledger = _inspect_ledger(runner, sender, task_id=task_id)
    purpose_hash = hashlib.sha256(purpose.encode("utf-8")).hexdigest()
    receiver_tasks = [
        row
        for row in receiver_ledger.get("tasks", [])
        if isinstance(row, dict) and row.get("id") == task_id
    ]
    if (
        len(receiver_tasks) != 1
        or receiver_tasks[0].get("state") != "completed"
        or receiver_tasks[0].get("from") != sender_address
        or receiver_tasks[0].get("to") != recipient_address
        or receiver_tasks[0].get("kind") != "message"
        or receiver_tasks[0].get("thread_id") != task_id
        or receiver_tasks[0].get("content_sha256") != purpose_hash
    ):
        raise AcceptanceError(
            "receiver_request_correlation_missing",
            "the receiver ledger did not contain the exact accepted human-authorized request",
        )
    sender_grants = [
        row
        for row in sender_ledger.get("outbound_grants", [])
        if isinstance(row, dict) and row.get("id") == task_id
    ]
    if (
        len(sender_grants) != 1
        or sender_grants[0].get("recipient") != recipient_address
        or sender_grants[0].get("purpose_sha256") != purpose_hash
        or sender_grants[0].get("state") not in {"sent", "completed"}
    ):
        raise AcceptanceError(
            "sender_request_correlation_missing",
            "the sender ledger did not bind the Hub tool request to its human authorization",
        )
    correlated = _correlated_exchange(
        receiver_ledger,
        sender_ledger,
        task_id=task_id,
        local_address=sender_address,
        remote_address=recipient_address,
    )
    sender_result = _wait_for_sender_result_consumption(
        runner,
        sender,
        deadline_seconds=deadline_seconds,
        event_id=correlated["message_id"],
        task_id=task_id,
        content_sha256=correlated["content_sha256"],
        local_address=sender_address,
        remote_address=recipient_address,
        run_marker=run_marker,
        relative_path=relative_path,
        expected_content=expected_content,
    )
    if (
        sender_result.get("sender_event_ledger_match") is not True
        or sender_result.get("provider_request_contains_result") is not True
        or sender_result.get("assistant_response_reflects_result") is not True
        or not sender_result.get("providers")
    ):
        raise AcceptanceError(
            "sender_result_not_consumed",
            "the sender provider did not consume and answer from the correlated result",
        )
    return {
        "passed": True,
        "human_input": human_evidence,
        "task_id": task_id,
        "target_path": relative_path,
        "request_sha256": hashlib.sha256(purpose.encode("utf-8")).hexdigest(),
        "request_correlation": {
            "sender_grant_id": sender_grants[0]["id"],
            "sender_grant_state": sender_grants[0]["state"],
            "receiver_task_id": receiver_tasks[0]["id"],
            "receiver_task_state": receiver_tasks[0]["state"],
            "content_sha256": receiver_tasks[0]["content_sha256"],
        },
        "remote_state": receipt.get("state"),
        "remote_file": file_evidence,
        "sender_model_hub_trace": sender_trace,
        "receiver_model_file_trace": receiver_trace,
        "correlated_reply": correlated,
        "sender_model_result_consumption": sender_result,
    }


def _run_question_answer_exchange(
    *,
    runner: Any,
    sender: Endpoint,
    receiver: Endpoint,
    recipient_address: str,
    sender_address: str,
    run_marker: str,
    relative_path: str,
    answer_content: str,
    expected_workspace: str,
    deadline_seconds: int,
    cleanup_scope: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Prove one human answer resumes the original task and completes its file flow."""
    if (
        not run_marker.startswith("KOLLAB_RELAY_QUESTION_")
        or not RELAY_ADDRESS_RE.fullmatch(recipient_address)
        or not RELAY_ADDRESS_RE.fullmatch(sender_address)
        or not relative_path
        or Path(relative_path).is_absolute()
        or ".." in Path(relative_path).parts
        or not answer_content
        or len(answer_content.encode("utf-8")) > 512
        or "\n" in answer_content
        or "\r" in answer_content
    ):
        raise AcceptanceError(
            "question_answer_scope_invalid",
            "the question-answer task scope was invalid",
        )
    if _file_exists(runner, receiver, relative_path):
        raise AcceptanceError(
            "target_exists", "the unique question-answer file already exists"
        )

    question_text = (
        f"What exact UTF-8 text should I write to {relative_path} for run {run_marker}?"
    )
    purpose = _question_answer_payload(run_marker, relative_path, question_text)
    purpose_hash = hashlib.sha256(purpose.encode("utf-8")).hexdigest()
    human_request = f"Please ask {recipient_address} to {purpose}"
    human_request_evidence = _submit_human_input(runner, sender, human_request)
    grant = _wait_for_outbound_grant(
        runner,
        sender,
        marker=run_marker,
        purpose=purpose,
        recipient=recipient_address,
        deadline_seconds=min(deadline_seconds, 180),
    )
    task_id = grant["id"]
    if cleanup_scope is not None:
        cleanup_scope.update(
            {"task_id": task_id, "recipient_address": recipient_address}
        )
    sender_initial_trace = _wait_for_sender_hub_trace(
        runner,
        sender,
        marker=run_marker,
        destination=recipient_address,
        grant_id=task_id,
        purpose_sha256=purpose_hash,
        deadline_seconds=min(deadline_seconds, 180),
    )

    pending = _wait_for_pending_question(
        runner,
        sender,
        receiver,
        task_id=task_id,
        sender_address=sender_address,
        receiver_address=recipient_address,
        purpose_sha256=purpose_hash,
        deadline_seconds=deadline_seconds,
    )
    question_id = pending["question_id"]
    question_hash = hashlib.sha256(question_text.encode("utf-8")).hexdigest()
    if pending.get("question_sha256") != question_hash:
        raise AcceptanceError(
            "question_body_mismatch",
            "the receiver did not send the exact requested human clarification",
        )
    question_hud = _relay_event_hud_entry(
        peer_address=recipient_address,
        agent=sender.agent,
        task_id=task_id,
        event_id=question_id,
        reply_to=question_id,
        kind="question",
        visible_content=f"[relay question] {question_text}",
    )
    question_consumption = _wait_for_sender_question_consumption(
        runner,
        sender,
        receiver,
        task_id=task_id,
        question_id=question_id,
        sender_address=sender_address,
        receiver_address=recipient_address,
        question_hud=question_hud,
        deadline_seconds=deadline_seconds,
    )
    if _file_exists(runner, receiver, relative_path):
        raise AcceptanceError(
            "file_created_before_answer",
            "the receiver created the file before receiving the human answer",
        )

    human_answer_prompt = (
        f"Please answer pending relay question {question_id} in original task "
        f"{task_id} from {recipient_address} with this exact content: {answer_content}"
    )
    human_answer_evidence = _submit_human_input(
        runner,
        sender,
        human_answer_prompt,
        relay_answer={
            "peer": recipient_address,
            "task_id": task_id,
            "question_id": question_id,
            "answer": answer_content,
        },
    )
    _wait_for_resumed_task(
        runner,
        receiver,
        task_id,
        deadline_seconds=deadline_seconds,
    )
    receipt = _wait_for_task(
        runner,
        sender,
        recipient_address,
        task_id,
        deadline_seconds=deadline_seconds,
    )
    if receipt.get("state") != "completed":
        raise AcceptanceError(
            "question_answer_task_incomplete",
            "the original task did not complete after the human answer",
        )

    answer_hash = hashlib.sha256(answer_content.encode("utf-8")).hexdigest()
    receiver_ledger = _inspect_ledger(runner, receiver, task_id=task_id)
    sender_ledger = _inspect_ledger(runner, sender, task_id=task_id)

    def events(ledger: dict[str, Any], kind: str) -> list[dict[str, Any]]:
        rows = ledger.get("events")
        if not isinstance(rows, list):
            return []
        return [
            row
            for row in rows
            if isinstance(row, dict)
            and row.get("kind") == kind
            and row.get("thread_id") == task_id
        ]

    receiver_questions = events(receiver_ledger, "question")
    sender_questions = events(sender_ledger, "question")
    sender_answers = events(sender_ledger, "answer")
    receiver_answers = events(receiver_ledger, "answer")
    receiver_tasks = [
        row
        for row in receiver_ledger.get("tasks", [])
        if isinstance(row, dict) and row.get("id") == task_id
    ]
    sender_grants = [
        row
        for row in sender_ledger.get("outbound_grants", [])
        if isinstance(row, dict)
        and row.get("recipient") == recipient_address
        and row.get("purpose_sha256") == purpose_hash
    ]
    receiver_question_deliveries = [
        row
        for row in receiver_ledger.get("outbound_queue", [])
        if isinstance(row, dict) and row.get("id") == question_id
    ]
    sender_answer_id = sender_answers[0].get("id") if len(sender_answers) == 1 else ""
    sender_answer_deliveries = [
        row
        for row in sender_ledger.get("outbound_queue", [])
        if isinstance(row, dict) and row.get("id") == sender_answer_id
    ]
    if (
        len(receiver_tasks) != 1
        or receiver_tasks[0].get("state") != "completed"
        or receiver_tasks[0].get("from") != sender_address
        or receiver_tasks[0].get("to") != recipient_address
        or receiver_tasks[0].get("kind") != "message"
        or receiver_tasks[0].get("thread_id") != task_id
        or receiver_tasks[0].get("content_sha256") != purpose_hash
        or len(sender_grants) != 1
        or sender_grants[0].get("id") != task_id
        or sender_grants[0].get("state") not in {"sent", "completed"}
        or len(receiver_questions) != 1
        or len(sender_questions) != 1
        or receiver_questions[0].get("id") != question_id
        or sender_questions[0].get("id") != question_id
        or receiver_questions[0].get("reply_to") != task_id
        or sender_questions[0].get("reply_to") != task_id
        # Answer admission marks the question answered at both endpoints.
        or receiver_questions[0].get("state") != "answered"
        or sender_questions[0].get("state") != "answered"
        or len(receiver_question_deliveries) != 1
        or receiver_question_deliveries[0].get("state") != "delivered"
        or receiver_question_deliveries[0].get("peer") != _relay_key(sender_address)
        or receiver_question_deliveries[0].get("thread") != task_id
        or receiver_question_deliveries[0].get("kind") != "question"
        or receiver_questions[0].get("content_sha256") != question_hash
        or sender_questions[0].get("content_sha256") != question_hash
        or len(sender_answers) != 1
        or len(receiver_answers) != 1
        or not isinstance(sender_answers[0].get("id"), str)
        or not ID_RE.fullmatch(sender_answers[0]["id"])
        or sender_answers[0].get("id") != receiver_answers[0].get("id")
        or sender_answers[0].get("state") != "delivered"
        or receiver_answers[0].get("state") != "consumed"
        or sender_answers[0].get("reply_to") != question_id
        or receiver_answers[0].get("reply_to") != question_id
        or sender_answers[0].get("content_sha256") != answer_hash
        or receiver_answers[0].get("content_sha256") != answer_hash
        or sender_answers[0].get("from") != sender_address
        or sender_answers[0].get("to") != recipient_address
        or receiver_answers[0].get("from") != sender_address
        or receiver_answers[0].get("to") != recipient_address
        or len(sender_answer_deliveries) != 1
    ):
        raise AcceptanceError(
            "question_answer_ledger_correlation_missing",
            "the same task, question, and consumed answer were not correlated at both endpoints",
        )
    answer_event_id = sender_answers[0]["id"]
    if (
        sender_answer_deliveries[0].get("state") != "delivered"
        or sender_answer_deliveries[0].get("peer") != _relay_key(recipient_address)
        or sender_answer_deliveries[0].get("thread") != task_id
        or sender_answer_deliveries[0].get("kind") != "answer"
    ):
        raise AcceptanceError(
            "question_answer_ledger_correlation_missing",
            "the sender answer delivery receipt did not match the consumed answer event",
        )

    answer_hud = _relay_event_hud_entry(
        peer_address=sender_address,
        agent=receiver.agent,
        task_id=task_id,
        event_id=answer_event_id,
        reply_to=question_id,
        kind="answer",
        visible_content=answer_content,
    )
    sender_qa_trace = _inspect_question_answer_trace(
        runner,
        sender,
        side="sender",
        marker=run_marker,
        purpose=purpose,
        task_id=task_id,
        question_id=question_id,
        question_text=question_text,
        answer_event_id=answer_event_id,
        answer_text=answer_content,
        human_initial_prompt=human_request,
        human_answer_prompt=human_answer_prompt,
        relative_path=relative_path,
        peer_address=recipient_address,
        question_hud=question_hud,
        answer_hud=answer_hud,
    )
    receiver_qa_trace = _inspect_question_answer_trace(
        runner,
        receiver,
        side="receiver",
        marker=run_marker,
        purpose=purpose,
        task_id=task_id,
        question_id=question_id,
        question_text=question_text,
        answer_event_id=answer_event_id,
        answer_text=answer_content,
        human_initial_prompt=human_request,
        human_answer_prompt=human_answer_prompt,
        relative_path=relative_path,
        peer_address=sender_address,
        question_hud=question_hud,
        answer_hud=answer_hud,
    )
    s_trace, r_trace = sender_qa_trace, receiver_qa_trace
    qa_requirements = {
        "sender_initial_wire": s_trace.get("sender_initial_wire_requests", 0) >= 1,
        "sender_question_wire": s_trace.get("sender_question_wire_requests", 0) >= 1,
        "sender_answer_wire": s_trace.get("sender_answer_wire_requests", 0) >= 1,
        "answer_call_count": s_trace.get("answer_call_count") == 1,
        "answer_receipt_count": s_trace.get("answer_receipt_count") == 1,
        "answer_receipt_id": s_trace.get("answer_receipt_id") == answer_event_id,
        "answer_receipt_state": s_trace.get("answer_receipt_state") == "running",
        "sender_unexpected_tools": s_trace.get("unexpected_tool_call_count") == 0,
        "sender_providers": bool(s_trace.get("providers")),
        "receiver_initial_wire": r_trace.get("receiver_initial_wire_requests", 0) >= 1,
        "receiver_answer_wire": r_trace.get("receiver_answer_wire_requests", 0) >= 1,
        "question_call_count": r_trace.get("question_call_count") == 1,
        "question_receipt_count": r_trace.get("question_receipt_count") == 1,
        "question_receipt_id": r_trace.get("question_receipt_id") == question_id,
        "question_receipt_state": r_trace.get("question_receipt_state") == "pending",
        "file_create_call_count": r_trace.get("file_create_call_count") == 1,
        "file_create_result_count": r_trace.get("file_create_result_count") == 1,
        "file_read_call_count": r_trace.get("file_read_call_count") == 1,
        "file_read_result_count": r_trace.get("file_read_result_count") == 1,
        "file_tools_in_order": r_trace.get("file_tools_in_order") is True,
        "receiver_unexpected_tools": r_trace.get("unexpected_tool_call_count") == 0,
        "receiver_providers": bool(r_trace.get("providers")),
    }
    missing = sorted(name for name, ok in qa_requirements.items() if not ok)
    if missing:
        if not _trace_scan_complete(sender_qa_trace) or not _trace_scan_complete(
            receiver_qa_trace
        ):
            raise AcceptanceError(
                "trace_evidence_insufficient",
                "the bounded provider trace windows could not prove the question-answer turns",
            )
        # Requirement names only; no trace content or identifiers.
        raise AcceptanceError(
            "question_answer_provider_trace_missing",
            "the provider wires, native question-answer receipts, or exact file tool "
            "trace were incomplete: " + ", ".join(missing),
        )

    file_trace = _inspect_model_and_tool_trace(
        runner,
        receiver,
        marker=run_marker,
        relative_path=relative_path,
        expected_content=answer_content,
    )
    if (
        not file_trace.get("model_turn")
        or not file_trace.get("providers")
        or len(file_trace.get("file_create_calls", [])) != 1
        or len(file_trace.get("file_create_tool_results", [])) != 1
        or len(file_trace.get("file_read_calls", [])) != 1
        or len(file_trace.get("file_read_tool_results", [])) != 1
    ):
        if not _trace_scan_complete(file_trace):
            raise AcceptanceError(
                "trace_evidence_insufficient",
                "the bounded receiver trace window could not prove the exact file tool calls",
            )
        raise AcceptanceError(
            "question_answer_file_tool_trace_missing",
            "the normal file tools did not create and read the exact human answer once",
        )
    file_evidence = _check_remote_file(
        runner,
        receiver,
        relative_path,
        answer_content.encode("utf-8"),
        expected_workspace=expected_workspace,
    )
    correlated = _correlated_exchange(
        receiver_ledger,
        sender_ledger,
        task_id=task_id,
        local_address=sender_address,
        remote_address=recipient_address,
    )
    sender_result = _wait_for_sender_result_consumption(
        runner,
        sender,
        deadline_seconds=deadline_seconds,
        event_id=correlated["message_id"],
        task_id=task_id,
        content_sha256=correlated["content_sha256"],
        local_address=sender_address,
        remote_address=recipient_address,
        run_marker=run_marker,
        relative_path=relative_path,
        expected_content=answer_content,
    )
    if (
        sender_result.get("sender_event_ledger_match") is not True
        or sender_result.get("provider_wire_request_contains_result") is not True
        or sender_result.get("assistant_response_reflects_result") is not True
        or not sender_result.get("providers")
    ):
        raise AcceptanceError(
            "question_answer_result_not_consumed",
            "the sender provider did not consume and answer from the correlated final result",
        )
    return {
        "passed": True,
        "scope": "one human question and answer in the same authorized task",
        "task_id": task_id,
        "task_state_before_answer": pending["task_state_before_answer"],
        "task_state_after_answer": receiver_tasks[0]["state"],
        "question_id": question_id,
        "question_sha256": question_hash,
        "question_receiver_state": receiver_questions[0]["state"],
        "question_sender_state": sender_questions[0]["state"],
        "question_receiver_outbound_state": receiver_question_deliveries[0]["state"],
        "answer_event_id": answer_event_id,
        "answer_sha256": answer_hash,
        "answer_sender_state": sender_answers[0]["state"],
        "answer_receiver_state": receiver_answers[0]["state"],
        "human_input": human_request_evidence,
        "human_answer_input": human_answer_evidence,
        "sender_initial_hub_trace": sender_initial_trace,
        "sender_question_consumption": question_consumption,
        "sender_question_answer_trace": sender_qa_trace,
        "receiver_question_answer_trace": receiver_qa_trace,
        "receiver_file_trace": file_trace,
        "remote_file": file_evidence,
        "correlated_reply": correlated,
        "sender_model_result_consumption": sender_result,
    }


def _parse_send_id(output: str) -> str:
    value = _json_object(output)
    receipt = value.get("receipt")
    if (
        isinstance(receipt, dict)
        and isinstance(receipt.get("id"), str)
        and ID_RE.fullmatch(receipt["id"])
    ):
        return receipt["id"]
    match = re.search(r"remote receipt:\s*(\{.+\})", _clean_text(output))
    if match:
        try:
            receipt = json.loads(match.group(1))
            if isinstance(receipt.get("id"), str) and ID_RE.fullmatch(receipt["id"]):
                return receipt["id"]
        except (ValueError, TypeError):
            pass
    raise AcceptanceError(
        "task_id_unavailable", "Kollab did not return the remote task identifier"
    )


def _read_receipt(output: str) -> dict[str, Any]:
    return _json_object(output)


def _authenticated_rejection(
    response: Any, *, request_id: str, expected_reason: str
) -> dict[str, Any] | None:
    """Accept only the bounded receiver decision returned inside secure TLS."""
    if (
        not ID_RE.fullmatch(request_id)
        or expected_reason
        not in {
            "not_authorized",
            "wrong_workspace",
            "wrong_recipient",
            "expired",
            "replay",
            "recipient_unavailable",
        }
        or not isinstance(response, dict)
        or set(response) != {"id", "state", "duplicate", "reason"}
        or response.get("id") != request_id
        or response.get("state") != "rejected"
        or response.get("duplicate") is not False
        or response.get("reason") != expected_reason
    ):
        return None
    return {
        "id": request_id,
        "state": "rejected",
        "reason": expected_reason,
        "authenticated_transport": "mutual TLS SecureConversationTransport",
    }


def _test_payload(run_id: str, relative_path: str, exact_content: str) -> str:
    return (
        f"For acceptance run {run_id}, use the normal Kollab model and tool pipeline. "
        f"Create exactly one new file at the relative path {relative_path} in your current workspace. "
        "Use the native file_create tool for the write, then use the native file_read tool "
        "on the same relative path to verify the contents. These are the only tools to use. "
        "Do not use terminal or shell commands, do not use an overwrite operation, and do not modify another file. "
        "The complete file content is exactly the UTF-8 text between these tags; the tags are not part of the file:\n"
        f"<exact-content>{exact_content}</exact-content>\n"
        "Do not add a newline. After file_read returns, reply with the run marker "
        f"{run_id} and confirm the path and exact bytes."
    )


def _question_answer_payload(
    run_marker: str, relative_path: str, question_text: str
) -> str:
    return (
        f"For acceptance run {run_marker}, first ask the human sender one clarification "
        "before using file tools. Use the normal native hub_msg tool with kind='question' "
        f"and ask this exact question: {question_text} Set thread_id and reply_to to the "
        "exact incoming task ID. Do not guess or perform any file operation until the "
        "actual human answer arrives on this same task as kind='answer'. Then create one "
        f"new file at relative path {relative_path} using the native file_create tool; "
        "the complete UTF-8 file content must be exactly the answer event body, with no "
        "added newline. Use the native file_read tool on that same path and confirm the "
        "exact bytes. The only tools allowed are hub_msg for the question, then "
        "file_create and file_read after the answer. Do not use terminal or shell "
        "commands, overwrite operations, or any other files. After file_read, reply "
        f"with marker {run_marker} and confirm the path and exact bytes."
    )


def _relay_event_hud_entry(
    *,
    peer_address: str,
    agent: str,
    task_id: str,
    event_id: str,
    reply_to: str,
    kind: str,
    visible_content: str,
) -> str:
    """Build one exact, model-visible Hub HUD event entry for trace matching."""
    if (
        not RELAY_ADDRESS_RE.fullmatch(peer_address)
        or not AGENT_ID_RE.fullmatch(agent)
        or not ID_RE.fullmatch(task_id)
        or not ID_RE.fullmatch(event_id)
        or not ID_RE.fullmatch(reply_to)
        or kind not in {"question", "answer"}
        or not visible_content
        or len(visible_content.encode("utf-8")) > 32768
    ):
        raise AcceptanceError(
            "question_answer_trace_scope_invalid",
            "the correlated question-answer trace scope was invalid",
        )
    header = (
        f"[hub channel: {peer_address} -> {agent} "
        f"[thread:{task_id[:8]}] [reply-to:{reply_to[:8]}]]"
    )
    context = (
        f"[relay event context: kind={kind} event_id={event_id} "
        f"thread_id={task_id} reply_to={reply_to} "
        f"parent_reply_to={task_id} peer={peer_address}]"
    )
    label = f"{peer_address}->{agent}"
    body = f"{header}\n{visible_content}\n{context}".strip()
    lines = body.splitlines()
    if not lines:
        raise AcceptanceError(
            "question_answer_trace_scope_invalid",
            "the correlated question-answer trace scope was invalid",
        )
    formatted = "+ " + lines[0]
    formatted += "".join("\n  " + line for line in lines[1:])
    return f"[hub:{label}]\n{formatted}"


def _correlated_exchange(
    receiver_ledger: dict[str, Any],
    sender_ledger: dict[str, Any],
    *,
    task_id: str,
    local_address: str,
    remote_address: str,
) -> dict[str, Any]:
    """Match receiver-delivered result to the sender's received event by ID and bytes."""
    if not ID_RE.fullmatch(task_id):
        raise AcceptanceError(
            "task_correlation_mismatch", "the task identifier was invalid"
        )

    def select(
        ledger: dict[str, Any], accepted_states: set[str]
    ) -> list[dict[str, Any]]:
        rows = ledger.get("matches")
        if not isinstance(rows, list):
            return []
        return [
            row
            for row in rows
            if isinstance(row, dict)
            and row.get("kind") == "result"
            and row.get("reply_to") == task_id
            and row.get("thread_id") == task_id
            and row.get("from") == remote_address
            and row.get("to") == local_address
            and row.get("state") in accepted_states
            and isinstance(row.get("id"), str)
            and ID_RE.fullmatch(row["id"])
            and isinstance(row.get("content_sha256"), str)
            and re.fullmatch(r"[0-9a-f]{64}", row["content_sha256"])
        ]

    sent = select(receiver_ledger, {"delivered"})
    received = select(sender_ledger, {"received", "delivered"})
    if len(sent) != 1 or len(received) != 1:
        raise AcceptanceError(
            "correlated_reply_missing",
            "the receiver send and sender receipt were not both uniquely correlated",
        )
    outbound, inbound = sent[0], received[0]
    if (
        outbound["id"] != inbound["id"]
        or outbound["content_sha256"] != inbound["content_sha256"]
    ):
        raise AcceptanceError(
            "correlated_reply_mismatch",
            "the receiver result and sender receipt did not match by identifier and bytes",
        )
    return {
        "message_id": outbound["id"],
        "reply_to": task_id,
        "thread_id": task_id,
        "receiver_outbound_state": outbound["state"],
        "sender_inbound_state": inbound["state"],
        "content_sha256": outbound["content_sha256"],
    }


def _wait_for_task(
    runner: Any,
    sender: Endpoint,
    remote_address: str,
    task_id: str,
    *,
    deadline_seconds: int,
) -> dict[str, Any]:
    if not ID_RE.fullmatch(task_id):
        raise AcceptanceError(
            "task_correlation_mismatch", "the task identifier was invalid"
        )
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        text = _run_text(
            runner,
            sender,
            "task_status",
            _endpoint_command(
                sender, sender.agent, "--connect", "task", remote_address, task_id
            ),
            timeout=30,
        )
        receipt = _read_receipt(text)
        if receipt.get("id") != task_id:
            raise AcceptanceError(
                "task_correlation_mismatch",
                "task status returned a different message identifier",
            )
        state = receipt.get("state")
        if not isinstance(state, str):
            raise AcceptanceError(
                "task_status_invalid", "the remote task returned an unknown state"
            )
        if state in TERMINAL_STATES:
            return receipt
        # reply_pending: the work finished and the result is still being sent.
        if state not in {"queued", "running", "reply_pending"}:
            raise AcceptanceError(
                "task_status_invalid", "the remote task returned an unknown state"
            )
        time.sleep(0.5)
    raise AcceptanceError(
        "task_deadline", "remote task did not finish before the acceptance deadline"
    )


def _inspect_sender_question_consumption(
    runner: Any,
    endpoint: Endpoint,
    *,
    task_id: str,
    question_id: str,
    question_hud: str,
) -> dict[str, Any]:
    """Read only the bounded provider trace for one delivered question event."""
    if (
        not ID_RE.fullmatch(task_id)
        or not ID_RE.fullmatch(question_id)
        or len(question_hud.encode("utf-8")) > 4096
        or f"event_id={question_id}" not in question_hud
        or f"thread_id={task_id}" not in question_hud
        or f"reply_to={question_id}" not in question_hud
    ):
        raise AcceptanceError(
            "question_answer_trace_scope_invalid",
            "the sender question trace scope was invalid",
        )
    source = r"""import json
import os
import stat
import sys
from pathlib import Path

workspace = Path(sys.argv[1]).resolve(strict=True)
task_id, question_id, question_hud = sys.argv[2:5]
encoded = str(workspace).replace("/", "_").replace("\\", "_").lstrip("_")
raw_dir = Path.home() / ".kollab" / "projects" / encoded / "conversations" / "raw"
providers = set()
wire_requests = 0
successful_provider_responses = 0
premature_file_tool_calls = 0
premature_answer_tool_calls = 0
records_seen = 0
scan_truncated = False


def safe_read(path, limit):
    return trace_safe_read(path, limit)


def recent_paths(directory, limit=40):
    return trace_recent_paths(directory, "*_raw.jsonl", limit, 16 * 1024 * 1024)


def user_texts(value):
    found = []
    pending = [(value, False, 0)]
    visited = 0
    while pending and visited < 50000:
        current, under_user, depth = pending.pop()
        visited += 1
        if depth > 32:
            continue
        if isinstance(current, dict):
            is_user = under_user or current.get("role") == "user"
            for key, child in current.items():
                if key != "role":
                    pending.append((child, is_user, depth + 1))
        elif isinstance(current, list):
            pending.extend((child, under_user, depth + 1) for child in current)
        elif under_user and isinstance(current, str) and len(current) <= 131072:
            found.append(current)
    return found


def arguments(call):
    value = call.get("input", call.get("arguments", {}))
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}
    return {}


for path in recent_paths(raw_dir):
    try:
        raw = safe_read(path, 16 * 1024 * 1024)
    except OSError:
        continue
    for line in raw.splitlines():
        records_seen += 1
        if records_seen > 20000:
            scan_truncated = True
            break
        try:
            item = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        request = item.get("request") or {}
        wire_text = "\n".join(user_texts(request.get("wire_request")))
        if question_hud not in wire_text:
            continue
        wire_requests += 1
        response = item.get("response") or {}
        if item.get("error") is None and isinstance(response, dict):
            profile = item.get("profile") or {}
            provider = profile.get("provider")
            model = profile.get("model")
            if isinstance(provider, str) and provider and isinstance(model, str) and model:
                providers.add((provider, model))
                successful_provider_responses += 1
        calls = response.get("tool_calls") or []
        if not isinstance(calls, list) or len(calls) > 64:
            scan_truncated = True
            continue
        for call in calls:
            if not isinstance(call, dict):
                continue
            name = str(call.get("name", "")).lower().replace("-", "_")
            values = arguments(call)
            kind = str(values.get("kind", "") or "").lower()
            if name in {"file_create", "file_read"}:
                premature_file_tool_calls += 1
            elif (
                name == "hub_msg"
                and kind == "answer"
                and values.get("thread_id") == task_id
            ):
                premature_answer_tool_calls += 1
    if scan_truncated:
        break

print(json.dumps({
    "wire_requests": wire_requests,
    "successful_provider_responses": successful_provider_responses,
    "premature_file_tool_calls": premature_file_tool_calls,
    "premature_answer_tool_calls": premature_answer_tool_calls,
    "providers": [
        {"provider": provider, "model": model}
        for provider, model in sorted(providers)
    ],
    "records_seen": records_seen,
    "scan_truncated": scan_truncated,
    "trace_scan": trace_scan_status(),
}))"""
    source = _TRACE_READER_SOURCE + source
    text = _require_success(
        runner.python(
            endpoint,
            source,
            [str(endpoint.workspace), task_id, question_id, question_hud],
        ),
        endpoint.label + ".sender_question_consumption",
    )
    record = _json_object(text)
    evidence = {
        "wire_requests": record.get("wire_requests", 0),
        "successful_provider_responses": record.get("successful_provider_responses", 0),
        "premature_file_tool_calls": record.get("premature_file_tool_calls", 0),
        "premature_answer_tool_calls": record.get("premature_answer_tool_calls", 0),
        "providers": record.get("providers", []),
        "records_seen": record.get("records_seen", 0),
        "scan_truncated": record.get("scan_truncated") is True,
        "trace_scan": record.get("trace_scan", {}),
    }
    for field in (
        "wire_requests",
        "successful_provider_responses",
        "premature_file_tool_calls",
        "premature_answer_tool_calls",
        "records_seen",
    ):
        try:
            evidence[field] = int(evidence[field])
        except (TypeError, ValueError):
            evidence[field] = 0
    return evidence


def _wait_for_sender_question_consumption(
    runner: Any,
    sender: Endpoint,
    receiver: Endpoint,
    *,
    task_id: str,
    question_id: str,
    sender_address: str,
    receiver_address: str,
    question_hud: str,
    deadline_seconds: int,
) -> dict[str, Any]:
    """Wait for the sender model to receive and answer successfully about the question."""
    sender_key = _relay_key(sender_address)
    if not RELAY_ADDRESS_RE.fullmatch(receiver_address):
        raise AcceptanceError("relay_address_invalid", "a relay address was invalid")
    deadline = time.monotonic() + deadline_seconds
    last_trace: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        receiver_ledger = _inspect_ledger(runner, receiver, task_id=task_id)
        sender_ledger = _inspect_ledger(runner, sender, task_id=task_id)
        tasks = [
            row
            for row in receiver_ledger.get("tasks", [])
            if isinstance(row, dict) and row.get("id") == task_id
        ]
        receiver_questions = [
            row
            for row in receiver_ledger.get("events", [])
            if isinstance(row, dict)
            and row.get("kind") == "question"
            and row.get("thread_id") == task_id
        ]
        sender_questions = [
            row
            for row in sender_ledger.get("events", [])
            if isinstance(row, dict)
            and row.get("kind") == "question"
            and row.get("thread_id") == task_id
        ]
        sender_answers = [
            row
            for row in sender_ledger.get("events", [])
            if isinstance(row, dict)
            and row.get("kind") == "answer"
            and row.get("thread_id") == task_id
        ]
        receiver_answers = [
            row
            for row in receiver_ledger.get("events", [])
            if isinstance(row, dict)
            and row.get("kind") == "answer"
            and row.get("thread_id") == task_id
        ]
        if (
            len(tasks) > 1
            or len(receiver_questions) > 1
            or len(sender_questions) > 1
            or sender_answers
            or receiver_answers
        ):
            raise AcceptanceError(
                "question_answer_event_ambiguous",
                "the original task produced duplicate or premature question-answer events",
            )
        if tasks and tasks[0].get("state") in TERMINAL_STATES:
            raise AcceptanceError(
                "question_answer_task_ended",
                "the receiver task ended before its human question was answered",
            )
        if tasks and tasks[0].get("state") != "waiting_answer":
            raise AcceptanceError(
                "question_answer_task_state_invalid",
                "the original receiver task left waiting_answer before the human answered",
            )
        question_state = None
        if len(receiver_questions) == 1 and len(sender_questions) == 1:
            sent, received = receiver_questions[0], sender_questions[0]
            if (
                sent.get("id") != question_id
                or received.get("id") != question_id
                or sent.get("reply_to") != task_id
                or received.get("reply_to") != task_id
                or sent.get("from") != receiver_address
                or sent.get("to") != sender_address
                or received.get("from") != receiver_address
                or received.get("to") != sender_address
                or sent.get("content_sha256") != received.get("content_sha256")
                or sent.get("state") != "pending"
                or received.get("state") != "pending"
            ):
                raise AcceptanceError(
                    "question_answer_event_mismatch",
                    "the pending question changed while the sender model was processing it",
                )
            outbound_rows = [
                row
                for row in receiver_ledger.get("outbound_queue", [])
                if isinstance(row, dict) and row.get("id") == question_id
            ]
            if len(outbound_rows) > 1:
                raise AcceptanceError(
                    "question_answer_event_ambiguous",
                    "the receiver ledger contained duplicate question delivery receipts",
                )
            if not outbound_rows or outbound_rows[0].get("state") == "queued":
                time.sleep(0.25)
                continue
            outbound = outbound_rows[0]
            if (
                sent.get("id") != question_id
                or received.get("id") != question_id
                or sent.get("reply_to") != task_id
                or received.get("reply_to") != task_id
                or sent.get("from") != receiver_address
                or sent.get("to") != sender_address
                or received.get("from") != receiver_address
                or received.get("to") != sender_address
                or sent.get("content_sha256") != received.get("content_sha256")
                or outbound.get("id") != question_id
                or outbound.get("state") != "delivered"
                or outbound.get("kind") != "question"
                or outbound.get("thread") != task_id
                or outbound.get("peer") != sender_key
            ):
                raise AcceptanceError(
                    "question_answer_event_mismatch",
                    "the pending question changed while the sender model was processing it",
                )
            if sent.get("state") != "pending" or received.get("state") != "pending":
                raise AcceptanceError(
                    "question_answer_event_state_invalid",
                    "the correlated question was no longer pending at both endpoints",
                )
            question_state = {
                "receiver_state": sent["state"],
                "sender_state": received["state"],
                "receiver_outbound_state": outbound["state"],
            }

        trace = _inspect_sender_question_consumption(
            runner,
            sender,
            task_id=task_id,
            question_id=question_id,
            question_hud=question_hud,
        )
        last_trace = trace
        if trace.get("scan_truncated") is True:
            raise AcceptanceError(
                "question_answer_trace_incomplete",
                "the bounded sender question trace exceeded its scan limit",
            )
        if trace.get("premature_file_tool_calls", 0) or trace.get(
            "premature_answer_tool_calls", 0
        ):
            raise AcceptanceError(
                "question_answer_tool_used_before_human",
                "the sender or receiver used a file or answer tool before the human answered",
            )
        if (
            len(tasks) == 1
            and question_state is not None
            and trace.get("wire_requests", 0) >= 1
            and trace.get("successful_provider_responses", 0) >= 1
            and trace.get("providers")
        ):
            return {
                "task_id": task_id,
                "question_id": question_id,
                "task_state": tasks[0]["state"],
                **question_state,
                **trace,
            }
        time.sleep(0.25)
    if last_trace is not None and not _trace_scan_complete(last_trace):
        raise AcceptanceError(
            "trace_evidence_insufficient",
            "the bounded sender trace window could not prove question consumption",
        )
    raise AcceptanceError(
        "question_answer_sender_deadline",
        "the sender provider did not consume the pending question before the deadline",
    )


def _wait_for_pending_question(
    runner: Any,
    sender: Endpoint,
    receiver: Endpoint,
    *,
    task_id: str,
    sender_address: str,
    receiver_address: str,
    purpose_sha256: str,
    deadline_seconds: int,
) -> dict[str, Any]:
    """Wait for one delivered question while the original receiver task waits."""
    if (
        not ID_RE.fullmatch(task_id)
        or not RELAY_ADDRESS_RE.fullmatch(sender_address)
        or not RELAY_ADDRESS_RE.fullmatch(receiver_address)
        or not re.fullmatch(r"[0-9a-f]{64}", purpose_sha256)
    ):
        raise AcceptanceError(
            "question_answer_scope_invalid",
            "the question-answer task scope was invalid",
        )
    sender_key = _relay_key(sender_address)
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        receiver_ledger = _inspect_ledger(runner, receiver, task_id=task_id)
        sender_ledger = _inspect_ledger(runner, sender, task_id=task_id)
        receiver_tasks = [
            row
            for row in receiver_ledger.get("tasks", [])
            if isinstance(row, dict) and row.get("id") == task_id
        ]
        receiver_questions = [
            row
            for row in receiver_ledger.get("events", [])
            if isinstance(row, dict)
            and row.get("kind") == "question"
            and row.get("thread_id") == task_id
            and row.get("reply_to") == task_id
            and row.get("from") == receiver_address
            and row.get("to") == sender_address
        ]
        sender_questions = [
            row
            for row in sender_ledger.get("events", [])
            if isinstance(row, dict)
            and row.get("kind") == "question"
            and row.get("thread_id") == task_id
            and row.get("reply_to") == task_id
            and row.get("from") == receiver_address
            and row.get("to") == sender_address
        ]
        if len(receiver_questions) > 1 or len(sender_questions) > 1:
            raise AcceptanceError(
                "question_event_ambiguous",
                "the task produced more than one correlated question event",
            )
        if receiver_tasks and receiver_tasks[0].get("state") in TERMINAL_STATES:
            raise AcceptanceError(
                "question_answer_task_ended",
                "the receiver task ended before its human question was answered",
            )
        if (
            len(receiver_tasks) == 1
            and receiver_tasks[0].get("state") == "waiting_answer"
            and len(receiver_questions) == 1
            and len(sender_questions) == 1
        ):
            outgoing, incoming = receiver_questions[0], sender_questions[0]
            outbound_rows = [
                row
                for row in receiver_ledger.get("outbound_queue", [])
                if isinstance(row, dict) and row.get("id") == outgoing.get("id")
            ]
            if len(outbound_rows) > 1:
                raise AcceptanceError(
                    "question_outbound_receipt_ambiguous",
                    "the receiver ledger contained duplicate outbound question receipts",
                )
            if not outbound_rows or outbound_rows[0].get("state") == "queued":
                time.sleep(0.25)
                continue
            outbound = outbound_rows[0]
            if outbound.get("state") != "delivered":
                raise AcceptanceError(
                    "question_outbound_receipt_failed",
                    "the receiver did not deliver the correlated question event",
                )
            valid = (
                isinstance(outgoing.get("id"), str)
                and ID_RE.fullmatch(outgoing["id"])
                and outgoing.get("id") == incoming.get("id")
                and outgoing.get("state") == "pending"
                and incoming.get("state") == "pending"
                and outbound.get("id") == outgoing.get("id")
                and outbound.get("state") == "delivered"
                and outbound.get("kind") == "question"
                and outbound.get("thread") == task_id
                and outbound.get("peer") == sender_key
                and outgoing.get("content_sha256") == incoming.get("content_sha256")
                and isinstance(outgoing.get("content_sha256"), str)
                and re.fullmatch(r"[0-9a-f]{64}", outgoing["content_sha256"])
                and receiver_tasks[0].get("from") == sender_address
                and receiver_tasks[0].get("to") == receiver_address
                and receiver_tasks[0].get("kind") == "message"
                and receiver_tasks[0].get("thread_id") == task_id
                and receiver_tasks[0].get("content_sha256") == purpose_sha256
            )
            if not valid:
                raise AcceptanceError(
                    "question_event_correlation_invalid",
                    "the question event did not match the waiting authorized task at both endpoints",
                )
            return {
                "task_id": task_id,
                "task_state_before_answer": receiver_tasks[0]["state"],
                "question_id": outgoing["id"],
                "question_state_receiver": outgoing["state"],
                "question_state_sender": incoming["state"],
                "question_outbound_state_receiver": outbound["state"],
                "question_sha256": outgoing["content_sha256"],
                "task_sha256": receiver_tasks[0]["content_sha256"],
            }
        time.sleep(0.25)
    raise AcceptanceError(
        "question_answer_deadline",
        "the original receiver task did not reach one correlated pending question before the deadline",
    )


def _wait_for_resumed_task(
    runner: Any,
    receiver: Endpoint,
    task_id: str,
    *,
    deadline_seconds: int,
) -> dict[str, Any]:
    """Wait for the same task to resume before using terminal task status."""
    if not ID_RE.fullmatch(task_id):
        raise AcceptanceError(
            "task_correlation_mismatch", "the task identifier was invalid"
        )
    deadline = time.monotonic() + deadline_seconds
    while time.monotonic() < deadline:
        ledger = _inspect_ledger(runner, receiver, task_id=task_id)
        tasks = [
            row
            for row in ledger.get("tasks", [])
            if isinstance(row, dict) and row.get("id") == task_id
        ]
        if len(tasks) > 1:
            raise AcceptanceError(
                "question_answer_task_ambiguous",
                "the receiver ledger contained duplicate original task records",
            )
        if tasks:
            state = tasks[0].get("state")
            if state in {"running", "completed"}:
                return tasks[0]
            if state in TERMINAL_STATES:
                raise AcceptanceError(
                    "question_answer_task_ended",
                    "the original task ended before it resumed after the answer",
                )
            if state not in {"waiting_answer", "queued"}:
                raise AcceptanceError(
                    "question_answer_task_state_invalid",
                    "the original task returned an unknown post-answer state",
                )
        time.sleep(0.25)
    raise AcceptanceError(
        "question_answer_resume_deadline",
        "the original receiver task did not resume after the human answer",
    )


def _wait_until_running(
    runner: Any,
    sender: Endpoint,
    remote_address: str,
    task_id: str,
    *,
    timeout_seconds: int = 20,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        text = _run_text(
            runner,
            sender,
            "task_status_before_cancel",
            _endpoint_command(
                sender, sender.agent, "--connect", "task", remote_address, task_id
            ),
            timeout=30,
        )
        receipt = _read_receipt(text)
        if receipt.get("id") != task_id:
            raise AcceptanceError(
                "task_correlation_mismatch",
                "task status returned a different message identifier",
            )
        state = receipt.get("state")
        if not isinstance(state, str):
            raise AcceptanceError(
                "task_status_invalid", "the remote task returned an unknown state"
            )
        if state == "running":
            return receipt
        if state in TERMINAL_STATES or state == "reply_pending":
            raise AcceptanceError(
                "cancel_window_missed",
                "the remote task finished before cancellation could be requested",
            )
        if state not in {"queued", "running"}:
            raise AcceptanceError(
                "task_status_invalid", "the remote task returned an unknown state"
            )
        time.sleep(0.25)
    raise AcceptanceError(
        "cancel_not_running",
        "the remote task did not reach running state before the cancellation deadline",
    )


def _remote_write_invitation(
    runner: Any, endpoint: Endpoint, run_id: str, secret: bytes
) -> tuple[str, int, int]:
    remote_path = f"/tmp/kollab-relay-acceptance-{run_id}.invite"
    source = r"""import json,os,stat,sys
path=sys.argv[1]
data=sys.stdin.buffer.read(4097)
if not data or len(data)>4096: raise SystemExit(3)
fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|getattr(os,"O_NOFOLLOW",0),0o600)
with os.fdopen(fd,"wb") as f:
    f.write(data); f.flush(); os.fsync(f.fileno())
st=os.lstat(path)
print(json.dumps({"path":path,"device":st.st_dev,"inode":st.st_ino,"uid":st.st_uid,"mode":stat.S_IMODE(st.st_mode)}))"""
    text = _require_success(
        runner.python(endpoint, source, [remote_path], input_bytes=secret),
        "transfer_invitation",
    )
    record = _json_object(text)
    if (
        record.get("path") != remote_path
        or record.get("uid") is None
        or record.get("mode") != 0o600
    ):
        raise AcceptanceError(
            "invitation_transfer_failed",
            "the remote private invitation copy was not verified",
        )
    return remote_path, int(record["device"]), int(record["inode"])


def _cleanup_remote_invitation(
    runner: Any,
    endpoint: Endpoint,
    path: str,
    device: int,
    inode: int,
) -> bool:
    source = r"""import json,os,stat,sys
p=sys.argv[1]; dev=int(sys.argv[2]); ino=int(sys.argv[3])
try: st=os.lstat(p)
except FileNotFoundError:
    print(json.dumps({"removed":True,"already_absent":True})); raise SystemExit(0)
if (
    not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid()
    or stat.S_IMODE(st.st_mode) != 0o600 or st.st_dev != dev or st.st_ino != ino
):
    print(json.dumps({"removed":False,"reason":"identity_changed"})); raise SystemExit(0)
os.unlink(p)
print(json.dumps({"removed":True,"already_absent":False}))"""
    try:
        text = _require_success(
            runner.python(endpoint, source, [path, str(device), str(inode)]),
            "cleanup_invitation",
        )
        return _json_object(text).get("removed") is True
    except AcceptanceError:
        return False


def _artifact_directory(value: str, run_id: str, endpoints: Sequence[Endpoint]) -> Path:
    base = Path(value).expanduser()
    if not base.is_absolute():
        raise AcceptanceError(
            "artifact_path_not_absolute", "artifact directory must be an absolute path"
        )
    try:
        if base.is_symlink():
            raise AcceptanceError(
                "artifact_path_unsafe", "artifact directory must not be a symlink"
            )
        base.mkdir(parents=True, mode=0o700, exist_ok=True)
        info = base.lstat()
        base = base.resolve(strict=True)
    except OSError as exc:
        raise AcceptanceError(
            "artifact_path_unavailable", "artifact directory could not be verified"
        ) from exc
    for endpoint in endpoints:
        if base == endpoint.workspace or endpoint.workspace in base.parents:
            raise AcceptanceError(
                "artifact_inside_workspace",
                "evidence artifacts must stay outside the test workspaces",
            )
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
        or base.is_symlink()
    ):
        raise AcceptanceError(
            "artifact_path_unsafe", "artifact directory must be user-owned and private"
        )
    run_dir = base / f"relay-agent-conversation-{run_id}"
    try:
        run_dir.mkdir(mode=0o700, exist_ok=False)
    except FileExistsError as exc:
        raise AcceptanceError(
            "artifact_collision", "the acceptance artifact directory already exists"
        ) from exc
    return run_dir


def _write_manifest(directory: Path, manifest: dict[str, Any]) -> Path:
    path = directory / "manifest.json"
    data = (json.dumps(manifest, indent=2, sort_keys=True) + "\n").encode("utf-8")
    try:
        descriptor = os.open(
            path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except OSError as exc:
        raise AcceptanceError(
            "manifest_write_failed", "acceptance evidence manifest could not be written"
        ) from exc
    return path


def _find_agent_address(
    runner: Any,
    endpoint: Endpoint,
    peer_key: str,
    workspace_id: str,
    agent_name: str,
    agent_id: str,
) -> str:
    text = _run_text(
        runner,
        endpoint,
        endpoint.label + ".directory",
        _endpoint_command(endpoint, endpoint.agent, "--connect", "agents", peer_key),
    )
    return _address_for_name(
        _json_rows(text, "agents"),
        agent_name,
        expected_key=peer_key,
        expected_workspace_id=workspace_id,
        expected_agent_id=agent_id,
    )


def _addressing_evidence(
    *, sender_address: str, receiver_address: str
) -> dict[str, str]:
    if not RELAY_ADDRESS_RE.fullmatch(sender_address) or not RELAY_ADDRESS_RE.fullmatch(
        receiver_address
    ):
        raise AcceptanceError(
            "addressing_scope_invalid", "the verified endpoint address was invalid"
        )
    return {
        "sender": sender_address,
        "receiver": receiver_address,
        "flow": "local-to-remote",
    }


def _get_receiving_grants(runner: Any, endpoint: Endpoint) -> set[tuple[str, str]]:
    text = _run_text(
        runner,
        endpoint,
        endpoint.label + ".grants",
        _endpoint_command(endpoint, endpoint.agent, "--connect", "grants"),
    )
    value = _json_object(text)
    rows = value.get("receiving", [])
    if not isinstance(rows, list):
        raise AcceptanceError(
            "grants_unavailable", "receiver grant status was incomplete"
        )
    return {
        (row.get("peer"), row.get("agent"))
        for row in rows
        if isinstance(row, dict)
        and isinstance(row.get("peer"), str)
        and isinstance(row.get("agent"), str)
    }


def _relay_approval_keys(
    runner: Any, endpoint: Endpoint, *, expected_workspace_id: str
) -> set[str]:
    """Read only this workspace's private persisted peer approvals."""
    source = r"""import hashlib,json,os,stat,sys
from pathlib import Path
workspace=Path(sys.argv[1]).resolve(strict=True)
expected=sys.argv[2]
root=Path.home()/".kollab"/"network"/hashlib.sha256(str(workspace).encode()).hexdigest()
try:
    root_st=root.lstat(); state_path=root/"state.json"; state_st=state_path.lstat()
    if not (
        stat.S_ISDIR(root_st.st_mode) and root_st.st_uid==os.getuid()
        and stat.S_IMODE(root_st.st_mode)&0o077==0
        and stat.S_ISREG(state_st.st_mode) and state_st.st_uid==os.getuid()
        and stat.S_IMODE(state_st.st_mode)&0o077==0 and state_st.st_nlink==1
        and state_st.st_size<=65536
    ):
        raise SystemExit(4)
    fd=os.open(state_path,os.O_RDONLY|getattr(os,"O_NOFOLLOW",0))
    with os.fdopen(fd,"rb") as stream:
        opened=os.fstat(stream.fileno())
        if (opened.st_dev,opened.st_ino)!=(state_st.st_dev,state_st.st_ino): raise SystemExit(5)
        value=json.loads(stream.read(65537))
except (OSError,ValueError,TypeError):
    raise SystemExit(6)
approvals=value.get("approvals") if isinstance(value,dict) else None
workspace_id=value.get("workspace_id") if isinstance(value,dict) else None
if (
    not isinstance(approvals,list) or len(approvals)>1024
    or any(
        not isinstance(key,str) or len(key)!=64
        or any(c not in "0123456789abcdef" for c in key)
        for key in approvals
    )
    or workspace_id!=expected
):
    raise SystemExit(7)
print(json.dumps({"workspace_id":workspace_id,"approvals":approvals}))"""
    text = _require_success(
        runner.python(
            endpoint,
            source,
            [str(endpoint.workspace), expected_workspace_id],
        ),
        endpoint.label + ".relay_approval_state",
    )
    record = _json_object(text)
    if (
        record.get("workspace_id") != expected_workspace_id
        or not isinstance(record.get("approvals"), list)
        or any(
            not isinstance(key, str) or not re.fullmatch(r"[0-9a-f]{64}", key)
            for key in record["approvals"]
        )
    ):
        raise AcceptanceError(
            "relay_approval_state_unverified",
            "the selected workspace peer approvals could not be verified",
        )
    return set(record["approvals"])


def _withdraw_probe_grant(runner: Any, endpoint: Endpoint, marker: str) -> bool:
    """Withdraw the single local send grant created for a rejected probe."""
    try:
        output = _run_text(
            runner,
            endpoint,
            "find_probe_send_grant",
            _endpoint_command(endpoint, endpoint.agent, "--connect", "grants"),
        )
        sending = _json_object(output).get("sending")
        if not isinstance(sending, list):
            return False
        matches = [
            row
            for row in sending
            if isinstance(row, dict)
            and isinstance(row.get("id"), str)
            and ID_RE.fullmatch(row["id"])
            and isinstance(row.get("purpose"), str)
            and marker in row["purpose"]
        ]
        if len(matches) != 1:
            return False
        grant_id = matches[0]["id"]
        _run_text(
            runner,
            endpoint,
            "withdraw_probe_send_grant",
            _endpoint_command(
                endpoint, endpoint.agent, "--connect", "withdraw", grant_id
            ),
        )
        after = _json_object(
            _run_text(
                runner,
                endpoint,
                "verify_probe_send_grant_withdrawal",
                _endpoint_command(endpoint, endpoint.agent, "--connect", "grants"),
            )
        ).get("sending")
        return isinstance(after, list) and any(
            isinstance(row, dict)
            and row.get("id") == grant_id
            and row.get("state") == "revoked"
            for row in after
        )
    except AcceptanceError:
        return False


def _cancel_if_admitted(
    runner: Any,
    endpoint: Endpoint,
    remote_address: str,
    task_id: str,
) -> bool:
    if not ID_RE.fullmatch(task_id):
        return False
    try:
        output = _run_text(
            runner,
            endpoint,
            "cancel_unexpected_probe_task",
            _endpoint_command(
                endpoint, endpoint.agent, "--connect", "cancel", remote_address, task_id
            ),
        )
        receipt = _json_object(output)
        return receipt.get("id") == task_id and receipt.get("state") == "cancelled"
    except AcceptanceError:
        return False


def _cleanup_question_answer_task(
    runner: Any,
    sender: Endpoint,
    receiver: Endpoint,
    recipient_address: str,
    task_id: str,
) -> dict[str, Any]:
    """Cancel only the exact admitted Q&A task before revoking its human grant."""
    if not ID_RE.fullmatch(task_id):
        return {
            "task_id": task_id if isinstance(task_id, str) else "",
            "receiver_state": "invalid_or_unavailable_id",
            "cancellation_attempted": False,
            "passed": False,
        }
    try:
        before = _inspect_ledger(runner, receiver, task_id=task_id)
    except AcceptanceError:
        return {
            "task_id": task_id,
            "receiver_state": "unavailable",
            "cancellation_attempted": False,
            "passed": False,
        }
    tasks = [
        row
        for row in before.get("tasks", [])
        if isinstance(row, dict) and row.get("id") == task_id
    ]
    if len(tasks) > 1:
        return {
            "task_id": task_id,
            "receiver_state": "ambiguous",
            "cancellation_attempted": False,
            "passed": False,
        }
    if not tasks:
        return {
            "task_id": task_id,
            "receiver_state": "absent",
            "cancellation_attempted": False,
            "passed": True,
        }
    state = tasks[0].get("state")
    active_states = {"queued", "running", "waiting_answer", "reply_pending"}
    if state in active_states:
        cancellation_confirmed = _cancel_if_admitted(
            runner, sender, recipient_address, task_id
        )
        try:
            after = _inspect_ledger(runner, receiver, task_id=task_id)
        except AcceptanceError:
            after = {}
        remaining = [
            row
            for row in after.get("tasks", [])
            if isinstance(row, dict) and row.get("id") == task_id
        ]
        receiver_state = (
            remaining[0].get("state")
            if len(remaining) == 1
            else ("ambiguous" if len(remaining) > 1 else "absent")
        )
        passed = cancellation_confirmed and receiver_state == "cancelled"
        return {
            "task_id": task_id,
            "receiver_state_before": state,
            "receiver_state": receiver_state,
            "cancellation_attempted": True,
            "sender_cancel_receipt_confirmed": cancellation_confirmed,
            "passed": passed,
        }
    return {
        "task_id": task_id,
        "receiver_state": state if isinstance(state, str) else "unknown",
        "cancellation_attempted": False,
        "passed": state in TERMINAL_STATES,
    }


def _cleanup_question_answer_before_grant_revocation(
    runner: Any,
    sender: Endpoint,
    receiver: Endpoint,
    *,
    recipient_address: str,
    task_id: str | None,
    outbound_probe_markers: list[tuple[str, str]],
) -> tuple[dict[str, Any], dict[str, bool]]:
    """Keep the exact task grant live until its admitted task is safely terminal."""
    task_cleanup = (
        _cleanup_question_answer_task(
            runner, sender, receiver, recipient_address, task_id
        )
        if task_id
        else {
            "task_id": "",
            "receiver_state": "no_task_id_observed",
            "cancellation_attempted": False,
            "passed": True,
        }
    )
    grant_cleanup: dict[str, bool] = {}
    for label, marker in outbound_probe_markers:
        if label == "question-answer" and task_cleanup.get("passed") is not True:
            grant_cleanup[label] = False
            continue
        grant_cleanup[label] = _withdraw_probe_grant(runner, sender, marker)
    return task_cleanup, grant_cleanup


def _send(
    runner: Any, endpoint: Endpoint, address: str, content: str, step: str
) -> tuple[str, str]:
    text = _run_text(
        runner,
        endpoint,
        step,
        _endpoint_command(
            endpoint, endpoint.agent, "--connect", "send", address, content
        ),
        timeout=30,
    )
    text = _clean_text(text)
    try:
        return _parse_send_id(text), text
    except AcceptanceError:
        pass
    # Return only a fixed error classification to callers. Raw process output
    # may contain model or workspace text and is never persisted or displayed.
    lowered = text.lower()
    if "no conversation grant" in lowered:
        return "", "rejected:conversation_grant"
    if "another workspace" in lowered or (
        "workspace" in lowered and "target" in lowered
    ):
        return "", "rejected:workspace_scope"
    if "approval" in lowered or "approved" in lowered:
        return "", "rejected:peer_approval"
    raise AcceptanceError("send_failed", f"endpoint step failed: {step}")


def _probe_secure_receiver_guards(
    *,
    runner: Any,
    invitation_path: Path,
    invitation_workspace: Path,
    invitation_identity: tuple[int, int],
    origin: str,
    receiver: Endpoint,
    receiver_key: str,
    receiver_workspace_id: str,
    receiver_workspace_path: str,
    receiver_agent_id: str,
    run_id: str,
    isolated_state: Path,
    requested_checks: Sequence[str],
    deadline_seconds: int,
    cleanup_report: dict[str, Any],
) -> dict[str, Any]:
    """Exercise receiver authorization and semantic replay over the current TLS API."""
    allowed_checks = {"unauthorized", "revoked", "wrong-workspace", "replay"}
    if not requested_checks or not set(requested_checks) <= allowed_checks:
        raise AcceptanceError(
            "secure_probe_scope_invalid", "the secure probe scope was invalid"
        )
    remote_workspace = PurePosixPath(receiver_workspace_path)
    if (
        not remote_workspace.is_absolute()
        or "\x00" in receiver_workspace_path
        or ".." in remote_workspace.parts
        or str(remote_workspace) != receiver_workspace_path
    ):
        raise AcceptanceError(
            "remote_workspace_path_unverified",
            "the remote preflight did not provide a canonical absolute workspace path",
        )
    try:
        repository_root = Path(__file__).resolve().parents[2]
        if str(repository_root) not in sys.path:
            sys.path.insert(0, str(repository_root))
        from plugins.hub.dns.discovery import discover
        from plugins.hub.dns.discovery_store import DiscoveryStore
        from plugins.hub.relay_client import RelayClient
        from plugins.hub.relay_commands import RelayCommands
        from plugins.hub.relay_conversations import RelayAddress
        from plugins.hub.secure_conversation import SecureConversationTransport
    except ImportError as exc:
        raise AcceptanceError(
            "secure_probe_unavailable", "secure probe dependencies are unavailable"
        ) from exc

    try:
        probe_stat = isolated_state.lstat()
    except OSError as exc:
        raise AcceptanceError(
            "secure_probe_state_unavailable",
            "the isolated secure-probe directory was unavailable",
        ) from exc
    if (
        not stat.S_ISDIR(probe_stat.st_mode)
        or probe_stat.st_uid != os.getuid()
        or stat.S_IMODE(probe_stat.st_mode) & 0o077
        or isolated_state.is_symlink()
    ):
        raise AcceptanceError(
            "secure_probe_state_unsafe",
            "the isolated secure-probe directory was unsafe",
        )

    probe_workspace = isolated_state / "workspace"
    probe_workspace.mkdir(mode=0o700, exist_ok=False)
    state_dir = isolated_state / "relay-state"
    client = RelayClient(
        probe_workspace, state_dir=state_dir, label="relay-acceptance-probe"
    )
    probe_key = client.public_key
    peer_approval_created = False
    probe_grant_created = False
    probe_grant_active = False
    close_error = False
    transport = None
    results: dict[str, Any] = {}
    probe_label = "relay-acceptance-probe"
    probe_machine_id = uuid.uuid4().hex
    receiver_address = str(
        RelayAddress(receiver_key, receiver_workspace_id, receiver_agent_id)
    )

    def operator_command(step: str, *parts: str) -> str:
        return _run_text(
            runner,
            receiver,
            step,
            _endpoint_command(receiver, receiver.agent, "--connect", *parts),
        )

    def approval_keys() -> set[str]:
        return _relay_approval_keys(
            runner, receiver, expected_workspace_id=receiver_workspace_id
        )

    def verify_probe_grant_absent() -> bool:
        return (probe_key, receiver.agent) not in _get_receiving_grants(
            runner, receiver
        )

    def no_model_or_tool_trace(marker: str) -> dict[str, Any]:
        trace = _inspect_model_and_tool_trace(
            runner,
            receiver,
            marker=marker,
            relative_path="",
            expected_content="",
        )
        quiet = (
            trace.get("model_turn") is False
            and trace.get("providers") == []
            and trace.get("file_create_calls") == []
            and trace.get("file_create_tool_results") == []
            and trace.get("file_read_calls") == []
            and trace.get("file_read_tool_results") == []
        )
        complete = _trace_scan_complete(trace)
        return {
            "passed": quiet and complete,
            "evidence_complete": complete,
            "model_or_tool_trace": trace,
        }

    try:
        if (
            _check_private_invitation(invitation_path, invitation_workspace)
            != invitation_identity
        ):
            raise AcceptanceError(
                "invitation_file_unsafe",
                "the secure-probe invitation changed after pairing",
            )
        token = (
            _read_invitation_for_transfer(invitation_path, invitation_identity)
            .decode("ascii")
            .strip()
        )
        client.join_invite(token)
        target = RelayAddress(receiver_key, receiver_workspace_id, receiver_agent_id)
        if target.key != receiver_key or not ID_RE.fullmatch(target.workspace_id):
            raise AcceptanceError(
                "secure_probe_target_mismatch",
                "the secure-probe target did not match the selected receiver",
            )
        if not AGENT_ID_RE.fullmatch(target.agent_id) or not AGENT_ID_RE.fullmatch(
            receiver.agent
        ):
            raise AcceptanceError(
                "secure_probe_target_mismatch",
                "the secure-probe receiver identity was incomplete",
            )

        receiver_local_text = _run_text(
            runner,
            receiver,
            "secure_probe_receiver_local_agents",
            _endpoint_command(receiver, receiver.agent, "--connect", "agents", "local"),
        )
        local_rows = _json_rows(receiver_local_text, "agents")
        matching_local_row = _select_secure_probe_receiver(
            local_rows,
            workspace_path=receiver_workspace_path,
            workspace_id=receiver_workspace_id,
            agent_id=receiver_agent_id,
            name=receiver.agent,
        )
        receiver_coordinator = matching_local_row["is_coordinator"]

        receiver_approvals_before = approval_keys()
        if probe_key in receiver_approvals_before:
            raise AcceptanceError(
                "secure_probe_peer_preexists",
                "the isolated probe identity already appears in receiver relay state",
            )
        if not verify_probe_grant_absent():
            raise AcceptanceError(
                "secure_probe_grant_preexists",
                "the isolated probe identity already has receiver conversation authority",
            )

        probe_events = SecureProbeEventRecorder(
            sender=receiver_address,
            recipient="",
        )
        expected_task_id = ""
        probe_address = ""
        transport = None

        async def dispatch_secure(peer: str, method: str, incoming: dict) -> dict:
            if (
                peer != receiver_key
                or method != "message"
                or not isinstance(incoming, dict)
            ):
                raise ValueError("unexpected secure-probe application request")
            return probe_events.accept(incoming)

        async def receive(peer: str, method: str, incoming: dict) -> dict:
            if peer != receiver_key:
                raise ValueError("unexpected secure-probe peer")
            if method == "directory":
                if incoming != {}:
                    raise ValueError("invalid secure-probe directory request")
                return {
                    "agents": [
                        {
                            "machine_id": probe_machine_id,
                            "workspace_id": client.state.workspace_id,
                            "agent_id": probe_label,
                            "name": probe_label,
                            "is_coordinator": False,
                            "state": "ready",
                        }
                    ],
                    "truncated": False,
                }
            if method == "secure_identity":
                if transport is None:
                    raise ValueError("secure-probe transport is not ready")
                return transport.identity_response(peer, incoming)
            if method == "secure_packet":
                if transport is None:
                    raise ValueError("secure-probe transport is not ready")
                return await transport.handle_packet(peer, incoming, dispatch_secure)
            raise ValueError("unexpected secure-probe request")

        peer_approval_created = True
        _run_text(
            runner,
            receiver,
            "approve_secure_probe_peer",
            _endpoint_command(
                receiver, receiver.agent, "--connect", "approve", probe_key
            ),
        )
        if probe_key not in approval_keys():
            raise AcceptanceError(
                "secure_probe_peer_approval_unverified",
                "the temporary receiver peer approval was not verified",
            )

        async def run() -> dict[str, Any]:
            nonlocal transport, probe_address, expected_task_id
            nonlocal probe_grant_created, probe_grant_active

            verified = await discover(origin)
            verified = DiscoveryStore(state_dir / "discovered").accept(verified)
            ws_url = RelayCommands._relay_url(verified)
            if not ws_url:
                raise AcceptanceError(
                    "secure_probe_no_relay",
                    "verified discovery did not advertise a relay endpoint",
                )
            client.set_request_handler(receive)
            await client.connect(verified.origin, ws_url=ws_url)
            peer_deadline = time.monotonic() + 15
            while time.monotonic() < peer_deadline:
                if any(row.get("key") == receiver_key for row in client.peers()):
                    break
                await asyncio.sleep(0.1)
            else:
                raise AcceptanceError(
                    "secure_probe_peer_offline",
                    "the receiver did not appear in the isolated relay session",
                )
            if receiver_key not in client.state.approvals:
                client.approve(receiver_key)
            if client.status().get("state") != "online" or not any(
                row.get("key") == receiver_key and row.get("approved") is True
                for row in client.peers()
            ):
                raise AcceptanceError(
                    "secure_probe_offline",
                    "the isolated secure transport was not mutually approved",
                )
            transport = SecureConversationTransport(client, client._store.key.encode())
            probe_address = str(
                RelayAddress(probe_key, client.state.workspace_id, probe_label)
            )
            probe_events.recipient = probe_address

            def message_payload(
                label: str,
                *,
                content: str | None = None,
                workspace_id: str | None = None,
            ) -> dict[str, Any]:
                event_id = uuid.uuid4().hex
                destination = RelayAddress(
                    receiver_key,
                    workspace_id or receiver_workspace_id,
                    receiver_agent_id,
                )
                marker = f"KOLLAB_RELAY_{label.upper().replace('-', '_')}_{run_id}"
                return {
                    "id": event_id,
                    "thread_id": event_id,
                    "reply_to": "",
                    "from": probe_address,
                    "to": str(destination),
                    "from_identity": probe_label,
                    "from_coordinator": False,
                    "to_identity": receiver.agent,
                    "to_coordinator": receiver_coordinator,
                    "content": content
                    or f"{marker}: reply with the marker only; do not use tools.",
                    "kind": "message",
                    "expires_at": int(time.time()) + 300,
                }

            async def send(payload: dict[str, Any]) -> Any:
                if transport is None:
                    raise AcceptanceError(
                        "secure_probe_transport_unavailable",
                        "the authenticated secure transport was unavailable",
                    )
                return await transport.request(
                    receiver_key, "message", payload, timeout=30
                )

            async def grant_receiver() -> None:
                nonlocal probe_grant_created, probe_grant_active
                if not verify_probe_grant_absent():
                    raise AcceptanceError(
                        "secure_probe_grant_preexists",
                        "the isolated probe identity already has receiver authority",
                    )
                probe_grant_created = True
                operator_command(
                    "allow_secure_probe_conversation",
                    "allow",
                    probe_key,
                    receiver.agent,
                )
                probe_grant_active = (
                    probe_key,
                    receiver.agent,
                ) in _get_receiving_grants(runner, receiver)
                if not probe_grant_active:
                    raise AcceptanceError(
                        "secure_probe_grant_failed",
                        "the temporary receiver grant was not verified",
                    )

            async def prove_rejection(
                label: str,
                payload: dict[str, Any],
                expected_reason: str,
                reached_guard: str,
            ) -> dict[str, Any]:
                response = await send(payload)
                rejection = _authenticated_rejection(
                    response,
                    request_id=payload["id"],
                    expected_reason=expected_reason,
                )
                marker = payload["content"].partition(":")[0]
                ledger = await asyncio.to_thread(
                    _inspect_ledger, runner, receiver, payload["id"], marker
                )
                tasks = ledger.get("tasks")
                trace = await asyncio.to_thread(no_model_or_tool_trace, marker)
                if trace.get("evidence_complete") is not True:
                    raise AcceptanceError(
                        "trace_evidence_insufficient",
                        "the bounded receiver trace window could not prove the rejected request "
                        "caused no model or tool work",
                    )
                if (
                    rejection is None
                    or not isinstance(tasks, list)
                    or tasks
                    or trace.get("passed") is not True
                ):
                    return {
                        "passed": False,
                        "status": "unattributed",
                        "boundary": "mutual-TLS secure application request",
                        "receiver_guard": reached_guard,
                        "authenticated_receiver_rejection": rejection is not None,
                        "receiver_task_admitted": bool(tasks),
                        "receiver_model_and_tool_trace_absent": trace.get("passed")
                        is True,
                    }
                return {
                    "passed": True,
                    "status": "receiver_rejected_at_guard",
                    "boundary": "mutual-TLS secure application request",
                    "receiver_guard": reached_guard,
                    "receiver_rejection": rejection,
                    "receiver_task_admitted": False,
                    "receiver_model_and_tool_trace_absent": True,
                }

            checks: dict[str, Any] = {}
            if "unauthorized" in requested_checks:
                if not verify_probe_grant_absent():
                    raise AcceptanceError(
                        "secure_probe_grant_preexists",
                        "the isolated probe identity already has receiver authority",
                    )
                payload = message_payload("unauthorized")
                checks["unauthorized"] = await prove_rejection(
                    "unauthorized",
                    payload,
                    "not_authorized",
                    "ConversationStore.admit conversation grant check",
                )

            if "wrong-workspace" in requested_checks:
                payload = message_payload(
                    "wrong-workspace", workspace_id=uuid.uuid4().hex
                )
                checks["wrong-workspace"] = await prove_rejection(
                    "wrong-workspace",
                    payload,
                    "wrong_workspace",
                    "receiver validate_message workspace scope",
                )

            if "revoked" in requested_checks:
                await grant_receiver()
                operator_command(
                    "revoke_secure_probe_conversation_grant",
                    "deny",
                    probe_key,
                    receiver.agent,
                )
                probe_grant_active = (
                    probe_key,
                    receiver.agent,
                ) in _get_receiving_grants(runner, receiver)
                if probe_grant_active or probe_key not in approval_keys():
                    raise AcceptanceError(
                        "secure_probe_revoke_unverified",
                        "the temporary conversation grant removal or peer approval was not verified",
                    )
                payload = message_payload("revoked")
                evidence = await prove_rejection(
                    "revoked",
                    payload,
                    "not_authorized",
                    "ConversationStore.admit after temporary grant revocation",
                )
                evidence["temporary_grant_created_and_removed"] = probe_grant_created
                evidence["peer_approval_retained_during_request"] = True
                checks["revoked"] = evidence

            if "replay" in requested_checks:
                await grant_receiver()
                marker = f"KOLLAB_RELAY_REPLAY_{run_id}"
                relative_path = f"relay-replay-{run_id}.txt"
                expected_content = f"KOLLAB_RELAY_REPLAY {run_id}"
                exists = await asyncio.to_thread(
                    _file_exists, runner, receiver, relative_path
                )
                if exists:
                    raise AcceptanceError(
                        "replay_artifact_exists",
                        "the unique replay-probe artifact path already exists",
                    )
                purpose = _test_payload(marker, relative_path, expected_content)
                payload = message_payload("replay", content=purpose)
                expected_task_id = payload["id"]
                probe_events.bind_task(expected_task_id)
                semantic_envelope = json.dumps(
                    payload, sort_keys=True, separators=(",", ":")
                ).encode("utf-8")
                envelope_sha256 = hashlib.sha256(semantic_envelope).hexdigest()
                first = await send(payload)
                first_ok = (
                    isinstance(first, dict)
                    and first.get("id") == expected_task_id
                    and first.get("state") in {"queued", "running", *TERMINAL_STATES}
                    and first.get("duplicate") is False
                )
                duplicate = await send(payload)
                exact_retry_ok = (
                    isinstance(duplicate, dict)
                    and duplicate.get("id") == expected_task_id
                    and duplicate.get("duplicate") is True
                )
                conflicting = dict(payload, content=purpose + " altered replay")
                replay_response = await send(conflicting)
                replay_rejection = _authenticated_rejection(
                    replay_response,
                    request_id=expected_task_id,
                    expected_reason="replay",
                )

                task_deadline = time.monotonic() + deadline_seconds
                ledger: dict[str, Any] = {"tasks": [], "matches": []}
                task_rows: list[dict[str, Any]] = []
                while time.monotonic() < task_deadline:
                    ledger = await asyncio.to_thread(
                        _inspect_ledger, runner, receiver, expected_task_id, marker
                    )
                    task_rows = [
                        row
                        for row in ledger.get("tasks", [])
                        if isinstance(row, dict) and row.get("id") == expected_task_id
                    ]
                    if (
                        len(task_rows) == 1
                        and task_rows[0].get("state") in TERMINAL_STATES
                        and probe_events.result_events
                    ):
                        break
                    await asyncio.sleep(0.5)

                task_completed = (
                    len(task_rows) == 1 and task_rows[0].get("state") == "completed"
                )
                trace = await asyncio.to_thread(
                    _inspect_model_and_tool_trace,
                    runner,
                    receiver,
                    marker=marker,
                    relative_path=relative_path,
                    expected_content=expected_content,
                )
                exact_tool_execution = (
                    bool(trace.get("providers"))
                    and _one_correlated_tool_call(
                        trace.get("file_create_calls"),
                        trace.get("file_create_tool_results"),
                    )
                    and _one_correlated_tool_call(
                        trace.get("file_read_calls"),
                        trace.get("file_read_tool_results"),
                    )
                )
                if not exact_tool_execution and not _trace_scan_complete(trace):
                    raise AcceptanceError(
                        "trace_evidence_insufficient",
                        "the bounded receiver trace window could not prove one semantic-replay tool execution",
                    )
                file_evidence = (
                    await asyncio.to_thread(
                        _check_remote_file,
                        runner,
                        receiver,
                        relative_path,
                        expected_content.encode("utf-8"),
                        expected_workspace=receiver_workspace_path,
                    )
                    if task_completed
                    else None
                )
                correlated_rows = [
                    row
                    for row in ledger.get("matches", [])
                    if isinstance(row, dict)
                    and row.get("kind") == "result"
                    and row.get("state") == "delivered"
                    and row.get("reply_to") == expected_task_id
                    and row.get("thread_id") == expected_task_id
                    and row.get("from") == receiver_address
                    and row.get("to") == probe_address
                ]
                result_events = probe_events.result_events
                task_correlation = (
                    len(task_rows) == 1
                    and task_rows[0].get("id") == expected_task_id
                    and task_rows[0].get("thread_id") == expected_task_id
                    and task_rows[0].get("from") == probe_address
                    and task_rows[0].get("to") == receiver_address
                    and task_rows[0].get("kind") == "message"
                    and task_rows[0].get("content_sha256")
                    == hashlib.sha256(purpose.encode("utf-8")).hexdigest()
                )
                correlated_reply = None
                if len(result_events) == 1 and len(correlated_rows) == 1:
                    callback_event = result_events[0]
                    receiver_event = correlated_rows[0]
                    if (
                        all(
                            callback_event.get(field) == receiver_event.get(field)
                            for field in (
                                "id",
                                "kind",
                                "reply_to",
                                "thread_id",
                                "from",
                                "to",
                                "content_sha256",
                            )
                        )
                        and callback_event.get("state") == "received"
                    ):
                        correlated_reply = {
                            "id": callback_event["id"],
                            "kind": "result",
                            "reply_to": callback_event["reply_to"],
                            "thread_id": callback_event["thread_id"],
                            "from": callback_event["from"],
                            "to": callback_event["to"],
                            "content_sha256": callback_event["content_sha256"],
                            "callback_state": callback_event["state"],
                            "receiver_outbound_state": receiver_event["state"],
                        }
                replay_passed = all(
                    (
                        first_ok,
                        exact_retry_ok,
                        replay_rejection is not None,
                        task_correlation,
                        task_completed,
                        exact_tool_execution,
                        file_evidence is not None,
                        correlated_reply is not None,
                    )
                )
                checks["replay"] = {
                    "passed": replay_passed,
                    "status": (
                        "receiver_deduplicated_exact_semantic_envelope"
                        if replay_passed
                        else "unattributed_or_incomplete"
                    ),
                    "boundary": "semantic message replay inside mutual-TLS application transport",
                    "tls_packet_replay_attempted": False,
                    "task_id": expected_task_id,
                    "semantic_envelope_sha256": envelope_sha256,
                    "exact_semantic_retry_sent": True,
                    "first_admission": first_ok,
                    "identical_retry_duplicate": exact_retry_ok,
                    "conflicting_replay_rejection": replay_rejection,
                    "receiver_task_rows_for_id": len(task_rows),
                    "receiver_request_correlation_verified": task_correlation,
                    "receiver_task_state": (
                        task_rows[0].get("state") if len(task_rows) == 1 else None
                    ),
                    "receiver_model_tool_trace": trace,
                    "exactly_one_file_tool_execution": exact_tool_execution,
                    "provider_response_observed": trace.get("model_turn") is True,
                    "unique_file_tool_execution_ids": {
                        "file_create": [
                            row.get("id")
                            for row in trace.get("file_create_calls", [])
                            if isinstance(row, dict)
                        ],
                        "file_read": [
                            row.get("id")
                            for row in trace.get("file_read_calls", [])
                            if isinstance(row, dict)
                        ],
                    },
                    "correlated_callback_event_count": len(probe_events.records),
                    "correlated_callback_event_bytes": probe_events.total_bytes,
                    "progress_event_count": sum(
                        record.get("kind") == "progress"
                        for record in probe_events.records.values()
                    ),
                    "remote_file": file_evidence,
                    "correlated_reply": correlated_reply,
                    "reply_observer": "isolated secure-probe client callback, not a Hub inbox",
                    "receiver_correlated_result_rows": len(correlated_rows),
                }
            return checks

        try:
            results = asyncio.run(run())
        except AcceptanceError:
            raise
        except Exception as exc:
            raise AcceptanceError(
                "secure_probe_failed",
                "the authenticated receiver-guard probe did not complete",
            ) from exc
    except AcceptanceError:
        raise
    except Exception as exc:
        raise AcceptanceError(
            "secure_probe_failed",
            "the authenticated receiver-guard probe did not complete",
        ) from exc
    finally:
        try:
            if transport is not None:
                transport.close()
            asyncio.run(client.close(disable=True))
        except Exception:
            close_error = True

        try:
            grants_now = _get_receiving_grants(runner, receiver)
            if probe_grant_created and (probe_key, receiver.agent) in grants_now:
                operator_command(
                    "cleanup_secure_probe_conversation_grant",
                    "deny",
                    probe_key,
                    receiver.agent,
                )
            cleanup_report["secure_probe_conversation_grant_removed"] = (
                verify_probe_grant_absent()
            )
        except Exception:
            cleanup_report["secure_probe_conversation_grant_removed"] = False

        try:
            approvals_now = approval_keys()
            if peer_approval_created and probe_key in approvals_now:
                operator_command("revoke_secure_probe_peer", "revoke", probe_key)
            cleanup_report["secure_probe_peer_revoked"] = (
                probe_key not in approval_keys()
            )
        except Exception:
            cleanup_report["secure_probe_peer_revoked"] = False
        cleanup_report["secure_probe_client_closed"] = not close_error

    if (
        close_error
        or cleanup_report.get("secure_probe_conversation_grant_removed") is not True
        or cleanup_report.get("secure_probe_peer_revoked") is not True
    ):
        raise AcceptanceError(
            "secure_probe_cleanup_pending",
            "temporary probe authorization was not fully removed",
        )
    return results


def _safe_remove_owned_probe(run_dir: Path, probe_dir: Path) -> bool:
    try:
        run_resolved = run_dir.resolve(strict=True)
        info = probe_dir.lstat()
        probe_resolved = probe_dir.resolve(strict=True)
        if (
            probe_dir.parent.resolve(strict=True) != run_resolved
            or probe_dir.name != "secure-probe"
            or not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or probe_resolved.parent != run_resolved
        ):
            return False
        shutil.rmtree(probe_resolved)
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def _pair_endpoints(
    runner: Any,
    local: Endpoint,
    remote: Endpoint,
    local_record: dict[str, Any],
    remote_record: dict[str, Any],
    *,
    origin: str,
    run_id: str,
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    Path | None,
    tuple[int, int] | None,
    tuple[str, int, int] | None,
    bool,
    bool,
]:
    local_status = _endpoint_from_record(local_record)
    remote_status = _endpoint_from_record(remote_record)
    expected_origin = _validate_relay_origin(origin)
    invitation_path: Path | None = None
    invitation_identity: tuple[int, int] | None = None
    transfer: tuple[str, int, int] | None = None

    if local_status.origin and local_status.origin != expected_origin:
        raise AcceptanceError(
            "local_origin_mismatch",
            "the local test workspace is already bound to a different relay origin",
        )
    if remote_status.origin and remote_status.origin != expected_origin:
        raise AcceptanceError(
            "remote_origin_mismatch",
            "the remote test workspace is already bound to a different relay origin",
        )
    local_peers = local_record.get("peers", {})
    remote_peers = remote_record.get("peers", {})
    if (
        local_peers.get(remote_status.public_key) is True
        and remote_peers.get(local_status.public_key) is True
    ):
        if local_status.state != "online" or remote_status.state != "online":
            raise AcceptanceError(
                "pair_offline", "the existing approved test pair is not online"
            )
        return local_record, remote_record, None, None, None, False, False

    # A new invitation shares the local room. Do not expose any existing local
    # or remote relay membership to a test peer.
    if local_status.approved_peers or local_status.online_peers:
        raise AcceptanceError(
            "local_pair_state_exists",
            "the local workspace has other relay peers; use a fresh dedicated workspace",
        )
    if (
        remote_status.origin
        or remote_status.approved_peers
        or remote_status.online_peers
    ):
        raise AcceptanceError(
            "remote_pair_state_exists",
            "the remote workspace has existing relay state; use its existing approved pair "
            "or a fresh dedicated workspace",
        )

    if local_status.state != "online":
        if local_status.origin:
            raise AcceptanceError(
                "local_offline",
                "the local relay is offline; reconnect it separately before acceptance",
            )
        _run_text(
            runner,
            local,
            "connect_local_origin",
            _endpoint_command(local, local.agent, "--connect", expected_origin),
        )
        local_record = preflight_endpoint(runner, local)
        local_status = _endpoint_from_record(local_record)
        if local_status.origin != expected_origin or local_status.state != "online":
            raise AcceptanceError(
                "local_connect_failed",
                "the local test workspace did not connect to the requested relay",
            )

    local_approval_attempted = False
    remote_approval_attempted = False
    local_approval_created = False
    remote_approval_created = False
    try:
        invitation_path, invitation_identity = _create_invitation(runner, local)
        invitation_bytes = _read_invitation_for_transfer(
            invitation_path, invitation_identity
        )
        transfer = _remote_write_invitation(runner, remote, run_id, invitation_bytes)
        remote_invite_path = transfer[0]
        try:
            _run_text(
                runner,
                remote,
                "join_pairing_invitation",
                _endpoint_command(
                    remote, remote.agent, "--connect", "join", remote_invite_path
                ),
                timeout=60,
            )
        finally:
            if not _cleanup_remote_invitation(runner, remote, *transfer):
                raise AcceptanceError(
                    "invitation_cleanup_pending",
                    "the owned remote invitation copy could not be safely removed",
                )

        local_approval_attempted = True
        _run_text(
            runner,
            local,
            "approve_remote_peer",
            _endpoint_command(
                local, local.agent, "--connect", "approve", remote_status.public_key
            ),
        )
        local_approval_created = True
        remote_approval_attempted = True
        _run_text(
            runner,
            remote,
            "approve_local_peer",
            _endpoint_command(
                remote, remote.agent, "--connect", "approve", local_status.public_key
            ),
        )
        remote_approval_created = True
        local_record = preflight_endpoint(runner, local)
        remote_record = preflight_endpoint(runner, remote)
        if (
            local_record["relay"]["state"] != "online"
            or remote_record["relay"]["state"] != "online"
        ):
            raise AcceptanceError(
                "pair_offline", "one endpoint was offline after pairing"
            )
        if local_record["peers"].get(remote_record["relay"]["public_key"]) is not True:
            raise AcceptanceError(
                "local_pair_unapproved",
                "the local endpoint did not report explicit remote approval",
            )
        if remote_record["peers"].get(local_record["relay"]["public_key"]) is not True:
            raise AcceptanceError(
                "remote_pair_unapproved",
                "the remote endpoint did not report explicit local approval",
            )
    except AcceptanceError:
        approval_cleanup_ok = True
        if remote_approval_attempted:
            try:
                _run_text(
                    runner,
                    remote,
                    "cleanup_partial_local_approval",
                    _endpoint_command(
                        remote,
                        remote.agent,
                        "--connect",
                        "revoke",
                        local_status.public_key,
                    ),
                )
            except AcceptanceError:
                approval_cleanup_ok = False
        if local_approval_attempted:
            try:
                _run_text(
                    runner,
                    local,
                    "cleanup_partial_remote_approval",
                    _endpoint_command(
                        local,
                        local.agent,
                        "--connect",
                        "revoke",
                        remote_status.public_key,
                    ),
                )
            except AcceptanceError:
                approval_cleanup_ok = False
        invitation_cleanup_ok = True
        if invitation_path is not None and invitation_identity is not None:
            invitation_cleanup_ok = _cleanup_local_invitation(
                invitation_path, local.workspace, *invitation_identity
            )
        if not approval_cleanup_ok or not invitation_cleanup_ok:
            raise AcceptanceError(
                "pair_cleanup_pending",
                "a temporary invitation or partial peer approval could not be removed",
            )
        raise
    return (
        local_record,
        remote_record,
        invitation_path,
        invitation_identity,
        transfer,
        local_approval_created,
        remote_approval_created,
    )


def _cleanup_created_pair_approvals(
    runner: Any,
    local: Endpoint,
    remote: Endpoint,
    *,
    local_public_key: str,
    remote_public_key: str,
    local_approval_created: bool,
    remote_approval_created: bool,
) -> dict[str, bool]:
    """Revoke only peer approvals created by this acceptance run."""
    cleanup: dict[str, bool] = {}
    if remote_approval_created:
        try:
            _run_text(
                runner,
                remote,
                "cleanup_test_pair_local_approval",
                _endpoint_command(
                    remote,
                    remote.agent,
                    "--connect",
                    "revoke",
                    local_public_key,
                ),
            )
            cleanup["remote_approval_removed"] = True
        except AcceptanceError:
            cleanup["remote_approval_removed"] = False
    if local_approval_created:
        try:
            _run_text(
                runner,
                local,
                "cleanup_test_pair_remote_approval",
                _endpoint_command(
                    local,
                    local.agent,
                    "--connect",
                    "revoke",
                    remote_public_key,
                ),
            )
            cleanup["local_approval_removed"] = True
        except AcceptanceError:
            cleanup["local_approval_removed"] = False
    return cleanup


def _check_reconnect(
    runner: Any,
    endpoint: Endpoint,
    origin: str,
    expected_key: str,
    expected_peer_key: str,
) -> dict[str, Any]:
    _run_text(
        runner,
        endpoint,
        "disconnect_test_workspace",
        _endpoint_command(endpoint, endpoint.agent, "--connect", "disconnect"),
    )
    _run_text(
        runner,
        endpoint,
        "reconnect_test_workspace",
        _endpoint_command(endpoint, endpoint.agent, "--connect", origin),
        timeout=60,
    )
    refreshed = preflight_endpoint(runner, endpoint)
    relay = refreshed["relay"]
    if relay["state"] != "online" or relay["public_key"] != expected_key:
        raise AcceptanceError(
            "reconnect_failed",
            "the test workspace did not reconnect with its existing identity",
        )
    if refreshed["peers"].get(expected_peer_key) is not True:
        raise AcceptanceError(
            "reconnect_trust_lost", "peer approval was not retained after reconnect"
        )
    return {
        "state": relay["state"],
        "public_key_preserved": True,
        "peer_approval_preserved": True,
    }


def run_acceptance(
    args: argparse.Namespace, runner: Any | None = None
) -> tuple[dict[str, Any], Path]:
    local = Endpoint("local", args.local_workspace, args.local_agent)
    remote = Endpoint(
        "remote", args.remote_workspace, args.remote_agent, args.remote_target
    )
    runner = runner or ProcessRunner(
        local_kollab=args.kollab,
        remote_kollab=args.remote_kollab,
        local_python=args.local_python,
        remote_python=args.remote_python,
        ssh=args.ssh,
        diagnostic_transcript_dir=args.diagnostic_transcript_dir,
        timeout=args.command_timeout,
    )
    run_id = uuid.uuid4().hex
    run_dir = _artifact_directory(args.artifacts_dir, run_id, (local, remote))
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "mode": "live-acceptance",
        "checks_requested": ["core", *args.check],
        "credentials_inspected": False,
        "credential_values_printed": False,
        "invitation_contents_recorded": False,
        "acceptance_boundaries": {
            "attached_ui_connect_status": {
                "status": "pending" if "ui-command" in args.check else "unverified",
                "requirement": "attached Hub input, /connect status submission, and returned command result",
            }
        },
        "endpoints": {},
        "checks": {},
        "cleanup": {},
    }
    invitation_path: Path | None = None
    invitation_identity: tuple[int, int] | None = None
    transfer: tuple[str, int, int] | None = None
    local_approval_created = False
    remote_approval_created = False
    receiving_grant_existed = False
    receiving_grant_added = False
    grant_cleanup_needed = False
    outbound_probe_markers: list[tuple[str, str]] = []
    question_answer_cleanup_scope: dict[str, Any] = {}
    failure: AcceptanceError | None = None

    try:
        manifest["endpoints"] = {
            local.label: preflight_endpoint(runner, local),
            remote.label: preflight_endpoint(runner, remote),
        }
        if any(
            not row.get("command_scope_matches_workspace")
            for row in manifest["endpoints"].values()
        ):
            raise AcceptanceError(
                "workspace_scope_unverified",
                "an endpoint command did not prove the selected real workspace",
            )
        if "ui-command" in args.check:
            ui_results = {
                endpoint.label: _check_attached_connect_command(runner, endpoint)
                for endpoint in (local, remote)
            }
            manifest["checks"]["ui-command"] = {
                "passed": all(row.get("passed") is True for row in ui_results.values()),
                "endpoints": ui_results,
            }
            manifest["acceptance_boundaries"]["attached_ui_connect_status"][
                "status"
            ] = "passed"
        _pair_check = _pair_endpoints(
            runner,
            local,
            remote,
            manifest["endpoints"]["local"],
            manifest["endpoints"]["remote"],
            origin=args.relay_origin,
            run_id=run_id,
        )
        (
            local_record,
            remote_record,
            invitation_path,
            invitation_identity,
            transfer,
            local_approval_created,
            remote_approval_created,
        ) = _pair_check
        manifest["endpoints"].update(local=local_record, remote=remote_record)
        local_status = _endpoint_from_record(local_record)
        remote_status = _endpoint_from_record(remote_record)
        remote_workspace_path = _verified_remote_workspace_path(remote_record)
        manifest["checks"]["pairing"] = {
            "passed": True,
            "local_public_key": local_status.public_key,
            "remote_public_key": remote_status.public_key,
            "origin": args.relay_origin,
            "approval": "explicit at both endpoints",
            "invitation_source_path": str(invitation_path) if invitation_path else None,
            "invitation_contents_recorded": False,
        }

        sender_address = _find_agent_address(
            runner,
            local,
            remote_status.public_key,
            remote_status.workspace_id,
            remote.agent,
            remote_status.agent_id,
        )
        receiver_address = _find_agent_address(
            runner,
            remote,
            local_status.public_key,
            local_status.workspace_id,
            local.agent,
            local_status.agent_id,
        )
        manifest["checks"]["addressing"] = _addressing_evidence(
            sender_address=receiver_address,
            receiver_address=sender_address,
        )
        existing_grants = _get_receiving_grants(runner, remote)
        receiving_grant_existed = (
            local_status.public_key,
            remote.agent,
        ) in existing_grants

        secure_probe_checks = [
            name
            for name in ("unauthorized", "revoked", "wrong-workspace", "replay")
            if name in args.check
        ]
        if secure_probe_checks:
            if invitation_path is None:
                invitation_path, invitation_identity = _create_invitation(runner, local)
                manifest["checks"]["pairing"]["invitation_source_path"] = str(
                    invitation_path
                )
                manifest["checks"]["pairing"][
                    "invitation_use"
                ] = "isolated secure receiver-guard probe enrollment"
            if invitation_identity is None:
                raise AcceptanceError(
                    "invitation_identity_missing",
                    "the secure receiver-guard probe invitation identity was not verified",
                )
            probe_dir = run_dir / "secure-probe"
            if probe_dir.exists() or probe_dir.is_symlink():
                raise AcceptanceError(
                    "secure_probe_path_exists",
                    "the reserved secure-probe state path already exists",
                )
            probe_dir.mkdir(mode=0o700, exist_ok=False)
            try:
                probe_checks = _probe_secure_receiver_guards(
                    runner=runner,
                    invitation_path=invitation_path,
                    invitation_workspace=local.workspace,
                    invitation_identity=invitation_identity,
                    origin=args.relay_origin,
                    receiver=remote,
                    receiver_key=remote_status.public_key,
                    receiver_workspace_id=remote_status.workspace_id,
                    receiver_workspace_path=remote_workspace_path,
                    receiver_agent_id=remote_status.agent_id,
                    run_id=run_id,
                    isolated_state=probe_dir,
                    requested_checks=secure_probe_checks,
                    deadline_seconds=args.deadline_seconds,
                    cleanup_report=manifest["cleanup"],
                )
                manifest["checks"].update(probe_checks)
                missing_checks = [
                    name
                    for name in secure_probe_checks
                    if probe_checks.get(name, {}).get("passed") is not True
                ]
                if missing_checks:
                    raise AcceptanceError(
                        "secure_receiver_guard_unproven",
                        "one or more receiver guard or semantic replay checks lacked boundary evidence",
                    )
            finally:
                if manifest["cleanup"].get("secure_probe_client_closed") is True:
                    manifest["cleanup"]["secure_probe_state_removed"] = (
                        _safe_remove_owned_probe(run_dir, probe_dir)
                    )
                else:
                    manifest["cleanup"]["secure_probe_state_removed"] = False
                    manifest["cleanup"]["secure_probe_state_retained_path"] = str(
                        probe_dir
                    )

        if not receiving_grant_existed:
            _run_text(
                runner,
                remote,
                "allow_sender_for_test_agent",
                _endpoint_command(
                    remote,
                    remote.agent,
                    "--connect",
                    "allow",
                    local_status.public_key,
                    remote.agent,
                ),
            )
            receiving_grant_added = True
            grant_cleanup_needed = True

        relative_path = f"relay-acceptance-{run_id}.txt"
        expected_content = f"KOLLAB_RELAY_ACCEPTANCE {run_id}"
        marker = f"KOLLAB_RELAY_ACCEPTANCE_{run_id}"
        outbound_probe_markers.append(("file_create_task", marker))
        manifest["checks"]["file_create_task"] = _run_file_exchange(
            runner=runner,
            sender=local,
            receiver=remote,
            recipient_address=sender_address,
            sender_address=receiver_address,
            run_marker=marker,
            relative_path=relative_path,
            expected_content=expected_content,
            expected_workspace=remote_workspace_path,
            deadline_seconds=args.deadline_seconds,
        )
        manifest["endpoints"]["local"][
            "provider_live_execution"
        ] = "confirmed by state.send_message and matching native Hub tool receipt"
        manifest["endpoints"]["remote"][
            "provider_live_execution"
        ] = "confirmed by correlated request, normal file tool trace, exact bytes, and result"

        if "question-answer" in args.check:
            question_marker = f"KOLLAB_RELAY_QUESTION_{run_id}"
            question_path = f"relay-question-answer-{run_id}.txt"
            human_answer = f"KOLLAB_RELAY_ANSWER_{run_id}"
            outbound_probe_markers.append(("question-answer", question_marker))
            manifest["checks"]["question-answer"] = _run_question_answer_exchange(
                runner=runner,
                sender=local,
                receiver=remote,
                recipient_address=sender_address,
                sender_address=receiver_address,
                run_marker=question_marker,
                relative_path=question_path,
                answer_content=human_answer,
                expected_workspace=remote_workspace_path,
                deadline_seconds=args.deadline_seconds,
                cleanup_scope=question_answer_cleanup_scope,
            )

        if "follow-up" in args.check:
            follow_marker = f"KOLLAB_RELAY_FOLLOWUP_{run_id}"
            outbound_probe_markers.append(("follow-up", follow_marker))
            follow_id, _ = _send(
                runner,
                local,
                sender_address,
                f"{follow_marker}: reply with this exact token only; do not use any tools or modify files.",
                "followup_task",
            )
            if not follow_id:
                raise AcceptanceError(
                    "followup_not_created", "the follow-up request was not admitted"
                )
            follow_receipt = _wait_for_task(
                runner,
                local,
                sender_address,
                follow_id,
                deadline_seconds=args.deadline_seconds,
            )
            follow_ledger = _inspect_ledger(runner, local, task_id=follow_id)
            follow_reply = _correlated_reply(
                follow_ledger,
                task_id=follow_id,
                local_address=receiver_address,
                remote_address=sender_address,
            )
            manifest["checks"]["follow-up"] = {
                "passed": follow_receipt.get("state") == "completed",
                "task_id": follow_id,
                "reply": follow_reply,
                "scope": "sequential, separately authorized task to the same peer; a new thread",
            }
            if not manifest["checks"]["follow-up"]["passed"]:
                raise AcceptanceError(
                    "followup_incomplete", "the follow-up conversation did not complete"
                )

        if "cancel" in args.check:
            cancel_marker = f"KOLLAB_RELAY_CANCEL_{run_id}"
            outbound_probe_markers.append(("cancel", cancel_marker))
            cancel_id, _ = _send(
                runner,
                local,
                sender_address,
                f"{cancel_marker}: wait for 45 seconds before replying; do no file or shell operations.",
                "cancel_task",
            )
            if not cancel_id:
                raise AcceptanceError(
                    "cancel_task_not_created",
                    "the cancel test request was not admitted",
                )
            _wait_until_running(runner, local, sender_address, cancel_id)
            cancel_text = _run_text(
                runner,
                local,
                "cancel_remote_task",
                _endpoint_command(
                    local, local.agent, "--connect", "cancel", sender_address, cancel_id
                ),
            )
            cancel_receipt = _json_object(cancel_text)
            passed = (
                cancel_receipt.get("id") == cancel_id
                and cancel_receipt.get("state") == "cancelled"
            )
            manifest["checks"]["cancel"] = {
                "passed": passed,
                "task_id": cancel_id,
                "state": cancel_receipt.get("state"),
            }
            remote_cancel_ledger = _inspect_ledger(runner, remote, task_id=cancel_id)
            manifest["checks"]["cancel"]["receiver_state"] = (
                remote_cancel_ledger.get("tasks", [{}])[0].get("state")
                if remote_cancel_ledger.get("tasks")
                else None
            )
            passed = (
                passed and manifest["checks"]["cancel"]["receiver_state"] == "cancelled"
            )
            manifest["checks"]["cancel"]["passed"] = passed
            if not passed:
                raise AcceptanceError(
                    "cancel_not_confirmed",
                    "the remote endpoint did not confirm task cancellation",
                )

        if "reconnect" in args.check:
            manifest["checks"]["reconnect"] = _check_reconnect(
                runner,
                local,
                args.relay_origin,
                local_status.public_key,
                remote_status.public_key,
            )
            manifest["checks"]["reconnect"]["passed"] = True

    except AcceptanceError as exc:
        failure = exc
        manifest["failure"] = {"code": exc.code, "message": exc.message}
    finally:
        if "question-answer" in args.check:
            task_cleanup, grant_cleanup = (
                _cleanup_question_answer_before_grant_revocation(
                    runner,
                    local,
                    remote,
                    recipient_address=question_answer_cleanup_scope.get(
                        "recipient_address", ""
                    ),
                    task_id=question_answer_cleanup_scope.get("task_id"),
                    outbound_probe_markers=outbound_probe_markers,
                )
            )
            manifest["cleanup"]["question_answer_task"] = task_cleanup
        else:
            grant_cleanup = {
                label: _withdraw_probe_grant(runner, local, marker)
                for label, marker in outbound_probe_markers
            }
        if grant_cleanup:
            manifest["cleanup"]["outbound_send_grants_withdrawn"] = grant_cleanup
        if receiving_grant_added and grant_cleanup_needed:
            try:
                _run_text(
                    runner,
                    remote,
                    "cleanup_test_conversation_grant",
                    _endpoint_command(
                        remote,
                        remote.agent,
                        "--connect",
                        "deny",
                        manifest.get("endpoints", {})
                        .get("local", {})
                        .get("relay", {})
                        .get("public_key", ""),
                        remote.agent,
                    ),
                )
                manifest["cleanup"]["test_conversation_grant_removed"] = True
            except AcceptanceError:
                manifest["cleanup"]["test_conversation_grant_removed"] = False
        elif receiving_grant_existed:
            manifest["cleanup"]["preexisting_conversation_grant_preserved"] = True
        if local_approval_created or remote_approval_created:
            endpoint_records = manifest.get("endpoints", {})
            local_key = (
                endpoint_records.get("local", {}).get("relay", {}).get("public_key", "")
            )
            remote_key = (
                endpoint_records.get("remote", {})
                .get("relay", {})
                .get("public_key", "")
            )
            manifest["cleanup"]["test_pair_approvals_removed"] = (
                _cleanup_created_pair_approvals(
                    runner,
                    local,
                    remote,
                    local_public_key=local_key,
                    remote_public_key=remote_key,
                    local_approval_created=local_approval_created,
                    remote_approval_created=remote_approval_created,
                )
            )
        if invitation_path is not None and invitation_identity is not None:
            manifest["cleanup"]["local_invitation_removed"] = _cleanup_local_invitation(
                invitation_path, local.workspace, *invitation_identity
            )
        if transfer:
            manifest["cleanup"]["remote_invitation_copy_removed"] = (
                _cleanup_remote_invitation(runner, remote, *transfer)
            )
        cleanup_confirmed = all(
            (
                value.get("passed") is True
                if key == "question_answer_task" and isinstance(value, dict)
                else (
                    all(status is True for status in value.values())
                    if isinstance(value, dict)
                    else value is not False
                )
            )
            for key, value in manifest["cleanup"].items()
        )
        manifest["cleanup"]["complete"] = cleanup_confirmed
        if not cleanup_confirmed and failure is None:
            failure = AcceptanceError(
                "cleanup_pending",
                "one or more acceptance-owned permissions or files remain",
            )
            manifest["failure"] = {"code": failure.code, "message": failure.message}
        manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        manifest_path = _write_manifest(run_dir, manifest)

    if failure:
        raise AcceptanceError(
            failure.code, f"{failure.message}; evidence: {manifest_path}"
        )
    return manifest, manifest_path


def run_preflight(
    args: argparse.Namespace, runner: Any | None = None
) -> dict[str, Any]:
    local = Endpoint("local", args.local_workspace, args.local_agent)
    remote = Endpoint(
        "remote", args.remote_workspace, args.remote_agent, args.remote_target
    )
    runner = runner or ProcessRunner(
        local_kollab=args.kollab,
        remote_kollab=args.remote_kollab,
        local_python=args.local_python,
        remote_python=args.remote_python,
        ssh=args.ssh,
        diagnostic_transcript_dir=args.diagnostic_transcript_dir,
        timeout=args.command_timeout,
    )
    return {
        "mode": "preflight-only",
        "credentials_inspected": False,
        "credential_values_printed": False,
        "acceptance_boundaries": {
            "attached_ui_connect_status": {
                "status": "unverified",
                "requirement": "attached Hub input, /connect status submission, and returned command result",
            }
        },
        "endpoints": {
            local.label: preflight_endpoint(runner, local),
            remote.label: preflight_endpoint(runner, remote),
        },
    }


def _print_summary(report: dict[str, Any]) -> None:
    print(f"mode: {report['mode']}")
    print("credential values inspected or printed: no")
    for label, endpoint in report["endpoints"].items():
        print(
            f"{label}: Kollab {endpoint['version']} | agent {endpoint['agent']} | workspace {endpoint['workspace']}"
        )
        configured = (
            "configured"
            if endpoint.get("provider_profile_configured")
            else "not configured or not reported"
        )
        live_execution = endpoint.get("provider_live_execution", "pending")
        if isinstance(live_execution, str) and live_execution.startswith("confirmed"):
            access_status = "live provider execution confirmed"
        else:
            access_status = "credential/API access untested until a live model turn"
        print(f"  provider profile: {configured}; {access_status}")
        print(
            f"  relay: {endpoint['relay']['state']} | origin {endpoint['relay']['origin'] or 'unconfigured'}"
        )
        scope = (
            "matches"
            if endpoint["command_scope_matches_workspace"]
            else "not confirmed"
        )
        print(f"  workspace scope: command cwd {scope} supplied path")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--local-workspace",
        type=Path,
        required=True,
        help="absolute dedicated workspace for the local active agent",
    )
    parser.add_argument(
        "--local-agent", required=True, help="active local Hub identity name"
    )
    parser.add_argument(
        "--remote-target",
        required=True,
        help="preconfigured SSH host alias; no default host is used",
    )
    parser.add_argument(
        "--remote-workspace",
        type=Path,
        required=True,
        help="absolute dedicated remote workspace for the receiver",
    )
    parser.add_argument(
        "--remote-agent", required=True, help="active remote Hub identity name"
    )
    parser.add_argument(
        "--relay-origin",
        help="explicit HTTPS relay origin; required for live execution",
    )
    parser.add_argument(
        "--artifacts-dir", help="absolute private directory for the run manifest"
    )
    parser.add_argument(
        "--kollab", default="kollab", help="local installed Kollab executable"
    )
    parser.add_argument(
        "--remote-kollab", default="kollab", help="remote installed Kollab executable"
    )
    parser.add_argument(
        "--remote-python",
        default="python3",
        help="remote Python helper executable; source mode needs its own PYTHONPATH wrapper",
    )
    parser.add_argument(
        "--local-python",
        default=sys.executable,
        help="local Python helper executable; source mode needs its own PYTHONPATH wrapper",
    )
    parser.add_argument("--ssh", default="ssh", help="local SSH executable")
    parser.add_argument(
        "--diagnostic-transcript-dir",
        type=Path,
        help="private directory for failed read-only Hub command terminal excerpts",
    )
    parser.add_argument("--command-timeout", type=int, default=45)
    parser.add_argument("--deadline-seconds", type=int, default=300)
    parser.add_argument(
        "--execute",
        action="store_true",
        help="run pairing and live model/tool acceptance",
    )
    parser.add_argument(
        "--confirm-local-workspace", help="must exactly match --local-workspace"
    )
    parser.add_argument(
        "--confirm-remote-workspace", help="must exactly match --remote-workspace"
    )
    parser.add_argument(
        "--check",
        choices=CHECKS,
        action="append",
        default=[],
        help="additional live check; repeat to select several",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        args.local_workspace = _resolve_workspace(str(args.local_workspace), "local")
        args.remote_workspace = args.remote_workspace
        if (
            not args.remote_workspace.is_absolute()
            or ".." in args.remote_workspace.parts
            or "\x00" in str(args.remote_workspace)
        ):
            raise AcceptanceError(
                "remote_workspace_not_absolute",
                "remote workspace path must be absolute",
            )
        if not re.fullmatch(r"[A-Za-z0-9_.@:-]{1,253}", args.remote_target):
            raise AcceptanceError(
                "invalid_ssh_target", "SSH target must be a host alias or user@host"
            )
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", args.local_agent):
            raise AcceptanceError("invalid_local_agent", "local agent name was invalid")
        if not re.fullmatch(r"[a-z][a-z0-9-]{0,63}", args.remote_agent):
            raise AcceptanceError(
                "invalid_remote_agent", "remote agent name was invalid"
            )
        if len(set(args.check)) != len(args.check):
            raise AcceptanceError(
                "duplicate_check", "each optional live check may be selected only once"
            )
        if (
            args.command_timeout < 1
            or args.deadline_seconds < 1
            or args.deadline_seconds > TASK_DEADLINE_MAX
        ):
            raise AcceptanceError(
                "invalid_timeout",
                "timeouts must be positive and the task deadline cannot exceed 600 seconds",
            )
        if args.execute:
            if not args.relay_origin or not args.artifacts_dir:
                raise AcceptanceError(
                    "execution_scope_missing",
                    "live execution requires --relay-origin and --artifacts-dir",
                )
            if (
                args.confirm_local_workspace is None
                or Path(args.confirm_local_workspace).expanduser().resolve()
                != args.local_workspace
            ):
                raise AcceptanceError(
                    "local_scope_unconfirmed",
                    "--confirm-local-workspace must exactly match the resolved local workspace",
                )
            if (
                args.confirm_remote_workspace is None
                or Path(args.confirm_remote_workspace).expanduser()
                != args.remote_workspace
            ):
                raise AcceptanceError(
                    "remote_scope_unconfirmed",
                    "--confirm-remote-workspace must exactly match the remote workspace path",
                )
            args.relay_origin = _validate_relay_origin(args.relay_origin)
            report, manifest_path = run_acceptance(args)
            _print_summary(report)
            print(f"acceptance: passed | evidence: {manifest_path}")
            return 0
        report = run_preflight(args)
        _print_summary(report)
        print(
            "acceptance: preflight only; no pair, approval, model, tool, or workspace mutation requested"
        )
        return 0
    except AcceptanceError as exc:
        print(f"acceptance: blocked [{exc.code}] {exc.message}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
