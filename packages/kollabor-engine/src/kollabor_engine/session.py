"""EngineSession - a thin proxy over one headless kollab daemon.

This used to be a second, smaller implementation of a conversation: its own
services, its own history list, and ``turn_runner.py`` running its own turn
loop. It drifted from the terminal client and shipped without plugins, the XML
tag pipeline, the vault, compaction, or a conversation log.

Now a session owns a ``kollab --detached`` daemon - the same process the
terminal client forks - and this class is the adapter between the engine's HTTP
surface and that daemon:

  * reads and writes go out as ``state.*`` RPC calls
  * ``send_message`` submits a turn and returns immediately
  * the daemon's structured display events are relayed as SSE

Everything the terminal client can do, a web session can now do, because it is
the same process doing it.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from kollabor_events.permissions_models import ApprovalMode

from .daemon_pool import DaemonHandle, get_daemon_pool

logger = logging.getLogger(__name__)

PERMISSION_RESPONSE_RPC_METHOD = "permission.respond"

_APPROVAL_MODE_MAP = {
    "confirm_all": ApprovalMode.CONFIRM_ALL,
    "default": ApprovalMode.DEFAULT,
    "auto_approve_edits": ApprovalMode.AUTO_APPROVE_EDITS,
    "trust_all": ApprovalMode.TRUST_ALL,
}


# HTTP decision+scope -> the ConfirmationResponse name the daemon expects.
_APPROVE_SCOPE_RESPONSES = {
    "once": "APPROVE_ONCE",
    "session": "APPROVE_SESSION",
    "project": "APPROVE_PROJECT",
    "always_edits": "APPROVE_ALWAYS",
    "trust_tool": "APPROVE_TOOL_ALWAYS",
}


def _confirmation_response_name(decision: str, scope: str) -> str:
    """Map an API decision+scope onto a ConfirmationResponse member name.

    Anything unrecognized denies: an unknown scope must never widen access.
    """
    if decision != "approve":
        return "DENY"
    return _APPROVE_SCOPE_RESPONSES.get(scope, "APPROVE_ONCE")


def _permission_input_payload(tool_data: Dict[str, Any]) -> Dict[str, Any]:
    """Build the user-visible permission input payload."""
    explicit_input = tool_data.get("input")
    if isinstance(explicit_input, dict) and explicit_input:
        return explicit_input

    arguments = tool_data.get("arguments")
    if isinstance(arguments, dict) and arguments:
        return arguments

    if tool_data.get("type") == "terminal":
        payload: Dict[str, Any] = {}
        for key in (
            "command",
            "cwd",
            "background",
            "timeout",
            "session_name",
            "lines",
        ):
            value = tool_data.get(key)
            if value not in (None, ""):
                payload[key] = value
        return payload

    return {}


class EngineSession:
    """One conversation, backed by a headless kollab daemon."""

    def __init__(
        self,
        session_id: str,
        profile: Any,
        approval_mode: str = "confirm_all",
        workspace: Optional[str] = None,
        system_prompt: Optional[str] = None,
        mcp_server_names: Optional[List[str]] = None,
        user_token: Optional[str] = None,
        agent: Optional[str] = None,
    ):
        self.session_id = session_id
        self.user_token = user_token
        self.workspace = str(Path(workspace).expanduser()) if workspace else None
        self.system_prompt = system_prompt or ""
        self.created_at = datetime.utcnow()
        self.profile = profile
        self.agent = agent
        self.approval_mode = approval_mode
        self.mcp_server_names = mcp_server_names or []

        self.daemon: Optional[DaemonHandle] = None

        # Mirror of the daemon's conversation. The daemon is the source of
        # truth; this is refreshed on demand so synchronous readers (to_dict,
        # the history route) don't have to await mid-render.
        self.history: List[Dict[str, Any]] = []

        self.total_turns = 0
        self.total_input_tokens = 0
        self.total_output_tokens = 0

        self._pending_permissions: Dict[str, Dict[str, Any]] = {}
        self._active_turn_task: Optional[asyncio.Task] = None
        self._event_task: Optional[asyncio.Task] = None

    # === lifecycle ===

    async def initialize(self) -> None:
        """Spawn the daemon and start tracking its event stream."""
        profile_name = getattr(self.profile, "name", None) or str(self.profile or "")
        self.daemon = await get_daemon_pool().spawn(
            self.session_id,
            profile=profile_name or None,
            agent=self.agent,
            workspace=self.workspace,
            system_prompt=self.system_prompt or None,
        )

        if self.approval_mode:
            try:
                await self.state.set_approval_mode(self.approval_mode)
            except Exception as e:
                logger.warning(
                    "session %s: could not set approval mode %s: %s",
                    self.session_id,
                    self.approval_mode,
                    e,
                )

        self._event_task = asyncio.create_task(
            self._track_events(), name=f"session-track-{self.session_id}"
        )
        await self.refresh_history()

    async def shutdown(self) -> None:
        if self._event_task is not None:
            self._event_task.cancel()
            try:
                await self._event_task
            except (asyncio.CancelledError, Exception):
                pass
        await get_daemon_pool().stop(self.session_id)
        self.daemon = None

    # === daemon access ===

    @property
    def state(self) -> Any:
        """The daemon's StateService (the 41 ``state.*`` RPC methods)."""
        if self.daemon is None or self.daemon.state is None:
            raise RuntimeError(f"session {self.session_id} has no live daemon")
        return self.daemon.state

    @property
    def alive(self) -> bool:
        return self.daemon is not None and self.daemon.alive

    def subscribe(self) -> asyncio.Queue:
        if self.daemon is None:
            raise RuntimeError(f"session {self.session_id} has no live daemon")
        return self.daemon.subscribe()

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        if self.daemon is not None:
            self.daemon.unsubscribe(queue)

    # === conversation ===

    async def refresh_history(self) -> List[Dict[str, Any]]:
        """Pull the daemon's conversation into the local mirror."""
        try:
            snapshot = await self.state.get_conversation()
        except Exception as e:
            logger.debug("session %s history refresh failed: %s", self.session_id, e)
            return self.history

        messages = getattr(snapshot, "messages", None)
        if messages is None and isinstance(snapshot, dict):
            messages = snapshot.get("messages", [])

        self.history = [
            m if isinstance(m, dict) else {"role": m.role, "content": m.content}
            for m in (messages or [])
        ]
        return self.history

    async def send_message(self, content: str) -> Dict[str, Any]:
        """Submit a user turn. Returns once accepted, not once complete."""
        return await self.state.send_message(content)

    def cancel_turn(self) -> None:
        """Ask the daemon to cancel the in-flight turn (fire and forget)."""
        if self.daemon is None:
            return
        self._active_turn_task = asyncio.create_task(
            self.state.cancel_current_request(), name=f"cancel-{self.session_id}"
        )

    # === permissions ===

    async def resolve_permission(
        self, tool_id: str, decision: str, scope: str = "once"
    ) -> bool:
        """Answer a permission prompt the daemon is blocked on.

        The HTTP surface speaks decision+scope; the daemon speaks
        ``ConfirmationResponse`` names, so translate at this boundary.
        """
        if self.daemon is None or self.daemon.rpc is None:
            return False

        response = _confirmation_response_name(decision, scope)
        try:
            result = await self.daemon.rpc.call(
                PERMISSION_RESPONSE_RPC_METHOD,
                {"tool_id": tool_id, "response": response, "scope": scope},
            )
        except Exception as e:
            logger.warning("permission response failed for %s: %s", tool_id, e)
            return False
        self._pending_permissions.pop(tool_id, None)
        return bool(isinstance(result, dict) and result.get("ok"))

    # === event tracking ===

    async def _track_events(self) -> None:
        """Keep session-level counters and pending prompts in step with the daemon.

        SSE consumers get their own subscription; this one exists so that a
        session with no attached browser still reports accurate stats and knows
        which permission prompts are outstanding.
        """
        queue = self.subscribe()
        try:
            while True:
                event = await queue.get()
                etype = event.get("type")

                if etype == "turn_complete":
                    self.total_turns += 1
                    self.total_input_tokens += int(event.get("input_tokens", 0) or 0)
                    self.total_output_tokens += int(event.get("output_tokens", 0) or 0)
                    await self.refresh_history()

                elif etype == "permission_request":
                    # Store the whole normalized event, not just the raw
                    # details: a client that reloads mid-prompt needs to
                    # re-render it, and the daemon will not re-send it.
                    tool_id = str(event.get("tool_id") or "")
                    if tool_id:
                        self._pending_permissions[tool_id] = event

                elif etype in ("permission_granted", "permission_denied"):
                    self._pending_permissions.pop(str(event.get("tool_id") or ""), None)

                elif etype == "daemon_closed":
                    break
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("session %s event tracking stopped: %s", self.session_id, e)
        finally:
            self.unsubscribe(queue)

    # === serialization ===

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "profile": getattr(self.profile, "name", str(self.profile or "")),
            "workspace": self.workspace,
            "approval_mode": _APPROVAL_MODE_MAP.get(
                self.approval_mode, ApprovalMode.CONFIRM_ALL
            ).value,
            "created_at": self.created_at.isoformat(),
            "total_turns": self.total_turns,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "history_length": len(self.history),
            "active": self.alive,
            "identity": self.daemon.identity if self.daemon else "",
            "daemon_pid": self.daemon.pid if self.daemon else 0,
            "mcp_servers": self.mcp_server_names,
            "mcp_connected": [],
        }
