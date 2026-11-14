"""Promotion gate: shared checkouts are touched only after scratch validation (#725).

Enforced by the tool layer (``ToolExecutor`` asks this gate before any tool
runs), not by the prompt. Off by default (``sandbox_gate.enabled``).

An agent works in a scratch checkout. To write into a protected root it asks
for a promotion (``sandbox-promote``): the configured validation commands run
in the scratch checkout, its ``git status`` must be clean, and only then is a
token issued. A token is bound to one root and one scratch HEAD SHA, lives
``token_ttl_seconds`` (default 15 minutes), is spent by the first guarded call
that uses it, and is void if the scratch HEAD moves. Every decision is
appended to an audit log (no file contents, no command output).

The owner's emergency override is read from the process environment only
(``KOLLAB_SANDBOX_GATE_OVERRIDE=<root>@<unix-expiry>``), never from config or
tool input, so an agent cannot grant it; it is time-boxed and logged.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

OVERRIDE_ENV = "KOLLAB_SANDBOX_GATE_OVERRIDE"
MAX_OVERRIDE_SECONDS = 3600
DEFAULT_TOKEN_TTL_SECONDS = 900
DEFAULT_VALIDATION_TIMEOUT_SECONDS = 600

READ_TYPES = {"file_read", "file_grep"}
PATH_KEYS = ("file", "file_path", "path", "from", "to")


@dataclass
class PromotionToken:
    """Permission to touch one protected root with one validated change."""

    token_id: str
    root: Path
    scratch: Path
    sha: str
    agent: str
    expires: float


def _git(cwd: Path, *args: str) -> Optional[str]:
    try:
        done = subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def _inside(path: Path, root: Path) -> bool:
    return path == root or path.is_relative_to(root)


class SandboxGate:
    """Decides whether a tool call may touch a protected shared root."""

    def __init__(self, config: Any = None, clock: Callable[[], float] = time.time):
        self._config = config
        self.clock = clock
        self._tokens: Dict[Path, PromotionToken] = {}

    # -- config ------------------------------------------------------------

    def _get(self, key: str, default: Any) -> Any:
        if self._config is None:
            return default
        return self._config.get(f"sandbox_gate.{key}", default)

    @property
    def enabled(self) -> bool:
        return self._get("enabled", False) is True

    def protected_roots(self) -> List[Path]:
        roots = self._get("protected_roots", []) or []
        return [Path(str(r)).expanduser().resolve() for r in roots if str(r).strip()]

    def _audit_path(self) -> Path:
        configured = self._get("audit_log", "")
        if configured:
            return Path(str(configured)).expanduser()
        return Path.home() / ".kollab" / "sandbox-gate" / "audit.jsonl"

    @staticmethod
    def agent_name() -> str:
        return os.environ.get("KOLLAB_IDENTITY", "default")

    # -- audit -------------------------------------------------------------

    def _log(self, event: str, **fields: Any) -> None:
        record = {
            "event": event,
            "timestamp": datetime.fromtimestamp(self.clock(), timezone.utc).isoformat(),
            "agent": self.agent_name(),
            **fields,
        }
        try:
            path = self._audit_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, default=str) + "\n")
        except OSError as exc:
            logger.error("sandbox gate audit log not written: %s", exc)

    # -- enforcement -------------------------------------------------------

    def check(self, tool_data: Dict[str, Any], workspace: Path) -> Optional[str]:
        """Return a refusal message when the call may not run, else None."""
        if not self.enabled:
            return None
        roots = self.protected_roots()
        if not roots:
            return None
        touched = self._roots_touched(tool_data, workspace, roots)
        if not touched:
            return None

        refused = [r for r in touched if not self._has_authority(r)]
        if refused:
            root = refused[0]
            self._log(
                "refused",
                root=str(root),
                tool=tool_data.get("type"),
                call_id=tool_data.get("id"),
            )
            return (
                f"Sandbox gate: {root} is a protected shared root. Work in a scratch "
                "checkout, then run sandbox-promote with the validated scratch "
                "checkout to get a one-time promotion token."
            )
        for root in touched:
            self._spend(root, tool_data)
        return None

    def _roots_touched(
        self, tool_data: Dict[str, Any], workspace: Path, roots: List[Path]
    ) -> List[Path]:
        tool_type = str(tool_data.get("type", ""))
        base = Path(workspace).resolve()
        paths: List[Path] = []
        if tool_type.startswith("file_") or tool_type == "malformed_file_op":
            if tool_type in READ_TYPES and self._get("allow_read", True) is True:
                return []
            sources = [tool_data]
            if isinstance(tool_data.get("arguments"), dict):
                sources.append(tool_data["arguments"])
            for source in sources:
                for key in PATH_KEYS:
                    value = source.get(key)
                    if isinstance(value, str) and value:
                        paths.append(self._resolve(value, base))
            if not paths:
                paths.append(base)
        elif tool_type == "terminal":
            cwd = tool_data.get("cwd")
            cwd_path = self._resolve(cwd, base) if cwd else base
            paths.append(cwd_path)
            command = str(tool_data.get("command", ""))
            try:
                words = shlex.split(command)
            except ValueError:
                words = command.split()
            for word in words:
                word = word.split("=", 1)[-1] if word.startswith("-") else word
                if word:
                    paths.append(self._resolve(word, cwd_path))
            for root in roots:
                if str(root) in command:
                    paths.append(root)
        else:
            return []
        return [r for r in roots if any(self._inside_protected_root(p, r) for p in paths)]

    @staticmethod
    def _inside_protected_root(path: Path, root: Path) -> bool:
        if _inside(path, root):
            return True
        candidate = path
        while True:
            try:
                if candidate.samefile(root):
                    return True
            except OSError:
                pass
            parent = candidate.parent
            if parent == candidate:
                return False
            candidate = parent

    @staticmethod
    def _resolve(value: str, base: Path) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute():
            path = base / path
        try:
            return path.resolve()
        except (OSError, RuntimeError):
            return path

    def _override_active(self, root: Path) -> bool:
        now = self.clock()
        for entry in os.environ.get(OVERRIDE_ENV, "").split(os.pathsep):
            target, _, expiry = entry.rpartition("@")
            try:
                expires = float(expiry)
                same = Path(target).expanduser().resolve() == root
            except (ValueError, OSError):
                continue
            # An override must be time-boxed: an expiry further out than the cap is ignored.
            if same and now < expires <= now + MAX_OVERRIDE_SECONDS:
                return True
        return False

    def _token_valid(self, root: Path) -> bool:
        token = self._tokens.get(root)
        if token is None:
            return False
        if self.clock() >= token.expires:
            del self._tokens[root]
            self._log("token_expired", root=str(root), sha=token.sha, token=token.token_id)
            return False
        if _git(token.scratch, "rev-parse", "HEAD") != token.sha:
            del self._tokens[root]
            self._log("token_voided", root=str(root), sha=token.sha, token=token.token_id)
            return False
        return True

    def _has_authority(self, root: Path) -> bool:
        return self._override_active(root) or self._token_valid(root)

    def _spend(self, root: Path, tool_data: Dict[str, Any]) -> None:
        if self._override_active(root):
            self._log("override", root=str(root), tool=tool_data.get("type"))
            return
        token = self._tokens.pop(root)
        self._log(
            "promotion_used",
            root=str(root),
            sha=token.sha,
            token=token.token_id,
            tool=tool_data.get("type"),
            call_id=tool_data.get("id"),
        )

    # -- promotion ---------------------------------------------------------

    def promote(self, scratch: str, root: str) -> Dict[str, Any]:
        """Validate a scratch checkout and, if it passes, issue a token.

        Returns ``{"success": bool, "message": str, ...}``. Blocking: run it in
        a worker thread.
        """
        if not self.enabled:
            return self._reject("the sandbox gate is not enabled", scratch, root)
        roots = self.protected_roots()
        target = Path(root).expanduser().resolve()
        scratch_path = Path(scratch).expanduser().resolve()
        if target not in roots:
            return self._reject("that root is not a protected root", scratch, root)
        if not scratch_path.is_dir():
            return self._reject("the scratch checkout does not exist", scratch, root)
        if any(_inside(scratch_path, r) for r in roots):
            return self._reject("a scratch checkout cannot live inside a protected root", scratch, root)
        scratch_root = str(self._get("scratch_root", "") or "")
        if scratch_root and not _inside(scratch_path, Path(scratch_root).expanduser().resolve()):
            return self._reject("the checkout is outside sandbox_gate.scratch_root", scratch, root)
        commands = [str(c) for c in (self._get("validation", []) or []) if str(c).strip()]
        if not commands:
            return self._reject("no sandbox_gate.validation commands are configured", scratch, root)

        sha = _git(scratch_path, "rev-parse", "HEAD")
        if sha is None:
            return self._reject("the scratch checkout is not a git repository with a commit", scratch, root)
        if _git(scratch_path, "status", "--porcelain") != "":
            return self._reject(
                "git status in the scratch checkout is not clean; commit only the intended files",
                scratch,
                root,
                sha=sha,
            )

        timeout = float(self._get("validation_timeout_seconds", DEFAULT_VALIDATION_TIMEOUT_SECONDS))
        results: List[Dict[str, Any]] = []
        for command in commands:
            started = self.clock()
            try:
                done = subprocess.run(
                    shlex.split(command), cwd=scratch_path, capture_output=True, timeout=timeout
                )
                code = done.returncode
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                code = -1
                logger.warning("sandbox gate validation could not run: %s", exc)
            results.append(
                {"command": command, "exit_code": code, "seconds": round(self.clock() - started, 3)}
            )
            if code != 0:
                reason = f"validation failed: {command} (exit {code})"
                return self._reject(reason, scratch, root, sha=sha, validation=results)

        if _git(scratch_path, "rev-parse", "HEAD") != sha or _git(scratch_path, "status", "--porcelain") != "":
            return self._reject("validation changed the scratch checkout", scratch, root, sha=sha, validation=results)

        ttl = float(self._get("token_ttl_seconds", DEFAULT_TOKEN_TTL_SECONDS))
        token = PromotionToken(
            token_id=uuid.uuid4().hex[:12],
            root=target,
            scratch=scratch_path,
            sha=sha,
            agent=self.agent_name(),
            expires=self.clock() + ttl,
        )
        self._tokens[target] = token
        self._log(
            "promoted", root=str(target), scratch=str(scratch_path), sha=sha,
            token=token.token_id, validation=results, expires=token.expires,
        )
        return {
            "success": True,
            "message": (
                f"Promotion token issued for {target} at {sha[:12]}. It is valid for "
                f"{int(ttl)}s, for one guarded call, and void if the scratch HEAD changes."
            ),
        }

    def _reject(self, reason: str, scratch: str, root: str, **fields: Any) -> Dict[str, Any]:
        self._log("promotion_rejected", reason=reason, scratch=str(scratch), root=str(root), **fields)
        return {"success": False, "message": f"Sandbox gate: promotion refused: {reason}."}
