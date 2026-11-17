"""Agent Session State - persistent working context across sessions.

When an agent shuts down, its working state is serialized to disk.
On rebirth, the state is rehydrated so the agent can pick up where
it left off instead of cold-booting from vault summaries alone.

State includes: open files, investigation notes, claimed lanes,
pending promises, last command, and focus file.
"""

import json
import logging
import math
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from kollabor_config.atomic_write import write_text_atomic
from kollabor_config.file_lock import locked_file

logger = logging.getLogger(__name__)


class SessionStateError(RuntimeError):
    """Raised when session state cannot be read or safely updated."""


class SessionState:
    """Working state that survives session restarts.

    This is NOT conversation history (compaction kills that).
    This is structured working context that gets serialized to disk
    and re-injected on rebirth.
    """

    def __init__(
        self,
        identity: str = "",
        open_files: Optional[List[str]] = None,
        investigation_notes: str = "",
        claimed_lanes: Optional[List[str]] = None,
        pending_promises: Optional[List[Dict[str, str]]] = None,
        last_command: str = "",
        focus_file: str = "",
    ):
        self.identity = identity
        self.open_files = open_files or []
        self.investigation_notes = investigation_notes
        self.claimed_lanes = claimed_lanes or []
        self.pending_promises = pending_promises or []
        self.last_command = last_command
        self.focus_file = focus_file
        self.saved_at: float = 0.0
        self.saved_at_human: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Serialize state to a dict."""
        return {
            "identity": self.identity,
            "open_files": self.open_files,
            "investigation_notes": self.investigation_notes,
            "claimed_lanes": self.claimed_lanes,
            "pending_promises": self.pending_promises,
            "last_command": self.last_command,
            "focus_file": self.focus_file,
            "saved_at": self.saved_at,
            "saved_at_human": self.saved_at_human,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SessionState":
        """Deserialize state from a dict."""
        if not isinstance(data, dict):
            raise ValueError("Session state must be an object")

        for key in (
            "identity",
            "investigation_notes",
            "last_command",
            "focus_file",
            "saved_at_human",
        ):
            if key in data and not isinstance(data[key], str):
                raise ValueError(f"Session state field {key} must be text")

        for key in ("open_files", "claimed_lanes"):
            value = data.get(key, [])
            if not isinstance(value, list) or any(
                not isinstance(item, str) for item in value
            ):
                raise ValueError(f"Session state field {key} must be a list of text")

        promises = data.get("pending_promises", [])
        if not isinstance(promises, list) or any(
            not isinstance(item, dict)
            or any(
                not isinstance(name, str) or not isinstance(value, str)
                for name, value in item.items()
            )
            for item in promises
        ):
            raise ValueError("Session state field pending_promises is invalid")

        saved_at = data.get("saved_at", 0.0)
        if isinstance(saved_at, bool) or not isinstance(saved_at, (int, float)):
            raise ValueError("Session state field saved_at must be a finite number")
        try:
            if not math.isfinite(saved_at):
                raise ValueError("Session state field saved_at must be a finite number")
        except OverflowError as e:
            raise ValueError(
                "Session state field saved_at must be a finite number"
            ) from e

        state = cls(
            identity=data.get("identity", ""),
            open_files=data.get("open_files", []),
            investigation_notes=data.get("investigation_notes", ""),
            claimed_lanes=data.get("claimed_lanes", []),
            pending_promises=data.get("pending_promises", []),
            last_command=data.get("last_command", ""),
            focus_file=data.get("focus_file", ""),
        )
        state.saved_at = data.get("saved_at", 0.0)
        state.saved_at_human = data.get("saved_at_human", "")
        return state

    def to_injection_prompt(self) -> str:
        """Format state for system prompt injection.

        This is what the agent sees on rebirth so it can
        pick up naturally instead of cold-booting.
        """
        lines = []
        lines.append("--- previous session state ---")
        lines.append(f"saved: {self.saved_at_human or 'unknown'}")
        lines.append("")

        if self.focus_file:
            lines.append(f"focus file: {self.focus_file}")

        if self.open_files:
            lines.append("open files:")
            for f in self.open_files:
                marker = " ◄ focus" if f == self.focus_file else ""
                lines.append(f"  - {f}{marker}")
            lines.append("")

        if self.claimed_lanes:
            lines.append("claimed lanes:")
            for lane in self.claimed_lanes:
                lines.append(f"  - {lane}")
            lines.append("")

        if self.pending_promises:
            lines.append("pending promises:")
            for p in self.pending_promises:
                to = p.get("to", "?")
                what = p.get("what", "?")
                lines.append(f"  -> {to}: {what}")
            lines.append("")

        if self.investigation_notes:
            lines.append("investigation notes:")
            # Truncate if too long
            notes = self.investigation_notes
            if len(notes) > 2000:
                notes = notes[-2000:]
            lines.append(notes)
            lines.append("")

        if self.last_command:
            lines.append(f"last command: {self.last_command}")
            lines.append("")

        lines.append("pick up where you left off.")
        lines.append("--- end session state ---")

        return "\n".join(lines)

    def merge_with_current(self, current: "SessionState") -> "SessionState":
        """Merge loaded state with current state.

        Current state wins on conflicts (identity, focus_file).
        Lists are unioned (deduped).
        Investigation notes are concatenated.
        """
        merged = SessionState(
            identity=current.identity or self.identity,
            focus_file=current.focus_file or self.focus_file,
            last_command=current.last_command or self.last_command,
            investigation_notes="",
        )

        # Union open files (dedupe, preserve order, current first)
        seen = set()
        merged_files = []
        for f in current.open_files + self.open_files:
            if f not in seen:
                seen.add(f)
                merged_files.append(f)
        merged.open_files = merged_files

        # Union claimed lanes
        seen_lanes = set()
        merged_lanes = []
        for lane in current.claimed_lanes + self.claimed_lanes:
            if lane not in seen_lanes:
                seen_lanes.add(lane)
                merged_lanes.append(lane)
        merged.claimed_lanes = merged_lanes

        # Union pending promises (by to+what key)
        seen_promises = set()
        merged_promises = []
        for p in current.pending_promises + self.pending_promises:
            key = f"{p.get('to', '')}:{p.get('what', '')}"
            if key not in seen_promises:
                seen_promises.add(key)
                merged_promises.append(p)
        merged.pending_promises = merged_promises

        # Concatenate investigation notes (current first, then previous)
        parts = []
        if current.investigation_notes:
            parts.append(current.investigation_notes)
        if (
            self.investigation_notes
            and self.investigation_notes != current.investigation_notes
        ):
            parts.append(f"[previous session]\n{self.investigation_notes}")
        merged.investigation_notes = "\n\n".join(parts)

        return merged


class SessionStateManager:
    """Manages session state persistence for agents.

    Thread-safe. Uses the agent's vault directory for storage.
    """

    def __init__(self):
        self._write_lock = threading.Lock()

    def _state_path(self, vault_dir: Path) -> Path:
        """Get the state file path for a vault directory."""
        return vault_dir / "session_state.json"

    def _state_lock_path(self, state_path: Path) -> Path:
        """Get the stable sidecar lock path used by all state writers."""
        return state_path.with_name(f".{state_path.name}.lock")

    def _load_state_unlocked(self, path: Path) -> Optional[SessionState]:
        """Read and validate state without changing the saved file."""
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except UnicodeError as e:
            logger.warning("Session state load error: %s", type(e).__name__)
            raise SessionStateError(
                "Session state is malformed; the original file was preserved. "
                "Back it up, then explicitly repair or remove it before retrying."
            ) from e
        except OSError as e:
            logger.warning("Session state load error: %s", type(e).__name__)
            raise SessionStateError(
                "Session state could not be read and was not treated as empty. "
                "Check access to the state file, then retry."
            ) from e

        try:
            data = json.loads(raw)
            state = SessionState.from_dict(data)
        except (
            json.JSONDecodeError,
            RecursionError,
            TypeError,
            ValueError,
            OverflowError,
        ) as e:
            logger.warning("Session state validation error: %s", type(e).__name__)
            raise SessionStateError(
                "Session state is malformed or invalid; the original file was "
                "preserved. Back it up, then explicitly repair or remove it "
                "before retrying."
            ) from e

        logger.debug("Session state loaded for %s", state.identity)
        return state

    def _save_state_unlocked(self, path: Path, state: SessionState) -> bool:
        """Atomically save validated state while the caller owns the lock."""
        saved_at = time.time()
        saved_at_human = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            data = state.to_dict()
            data["saved_at"] = saved_at
            data["saved_at_human"] = saved_at_human
            validated_state = SessionState.from_dict(data)
            serialized = json.dumps(validated_state.to_dict(), indent=2)
            write_text_atomic(path, serialized)
        except Exception as e:
            logger.warning("Session state save error: %s", type(e).__name__)
            return False

        state.saved_at = saved_at
        state.saved_at_human = saved_at_human
        logger.debug("Session state saved for %s", state.identity)
        return True

    def save_state(
        self,
        vault_dir: Path,
        state: SessionState,
    ) -> bool:
        """Serialize session state to disk.

        Args:
            vault_dir: The agent's vault directory.
            state: The session state to save.

        Returns:
            True if save succeeded.
        """
        try:
            path = self._state_path(vault_dir)
            with self._write_lock:
                with locked_file(self._state_lock_path(path), private=True):
                    return self._save_state_unlocked(path, state)
        except Exception as e:
            logger.warning("Session state save error: %s", type(e).__name__)
            return False

    def load_state(self, vault_dir: Path) -> Optional[SessionState]:
        """Load session state from disk.

        Args:
            vault_dir: The agent's vault directory.

        Returns:
            SessionState if found, None otherwise.

        Raises:
            SessionStateError: If saved state is unreadable, malformed, or invalid.
        """
        return self._load_state_unlocked(self._state_path(vault_dir))

    def clear_state(self, vault_dir: Path) -> bool:
        """Clear session state from disk.

        Args:
            vault_dir: The agent's vault directory.

        Returns:
            True if deletion succeeded or file didn't exist.
        """
        try:
            path = self._state_path(vault_dir)
            with self._write_lock:
                with locked_file(self._state_lock_path(path), private=True):
                    path.unlink(missing_ok=True)
            return True
        except FileNotFoundError:
            return True
        except Exception as e:
            logger.warning("Session state clear error: %s", type(e).__name__)
            return False

    def update_state(
        self,
        vault_dir: Path,
        updates: Dict[str, Any],
    ) -> SessionState:
        """Load existing state and apply partial updates.

        Merges updates into the existing state on disk.
        List fields are replaced, not appended.

        Args:
            vault_dir: The agent's vault directory.
            updates: Dict of fields to update.

        Returns:
            Updated SessionState.

        Raises:
            SessionStateError: If the existing state is unreadable or the update
                cannot be persisted safely.
        """
        if not isinstance(updates, dict):
            raise SessionStateError("Session state updates must be a JSON object.")

        path = self._state_path(vault_dir)
        try:
            with self._write_lock:
                with locked_file(self._state_lock_path(path), private=True):
                    state = self._load_state_unlocked(path)
                    if state is None:
                        state = SessionState()

                    for key, value in updates.items():
                        if hasattr(state, key):
                            setattr(state, key, value)

                    if not self._save_state_unlocked(path, state):
                        raise SessionStateError(
                            "Session state could not be saved; the update was "
                            "not applied."
                        )
                    return state
        except SessionStateError:
            raise
        except Exception as e:
            logger.warning("Session state update error: %s", type(e).__name__)
            raise SessionStateError(
                "Session state could not be updated safely; no success was "
                "reported. Check vault access, then retry."
            ) from e

    def get_injection_prompt(self, vault_dir: Path) -> str:
        """Load state and format it for system prompt injection.

        Returns an empty string if no state exists. Raises SessionStateError if
        the state cannot be read safely.

        Args:
            vault_dir: The agent's vault directory.

        Returns:
            Formatted injection prompt or empty string.
        """
        state = self.load_state(vault_dir)
        if state is None:
            return ""
        return state.to_injection_prompt()
