"""Attach-mode permission prompt bridge."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Dict, Optional

from kollabor_events.permissions_models import ConfirmationResponse

logger = logging.getLogger(__name__)

PERMISSION_RESPONSE_RPC_METHOD = "permission.respond"
DEFAULT_VISIBLE_CLIENT_GRACE_SECONDS = 2.0

# An approving response and the scope the engine's permission_granted event
# names (kollabor_engine.session._APPROVE_SCOPE_RESPONSES, inverted).
_GRANTED_SCOPES = {
    "APPROVE_ONCE": "once",
    "APPROVE_SESSION": "session",
    "APPROVE_PROJECT": "project",
    "APPROVE_ALWAYS": "always_edits",
    "APPROVE_TOOL_ALWAYS": "trust_tool",
}


def resolution_event(tool_id: str, response: Any) -> Dict[str, Any]:
    """The event that tells every attached window a prompt is closed."""
    scope = _GRANTED_SCOPES.get(getattr(response, "name", str(response)))
    if scope:
        return {"type": "permission_granted", "tool_id": tool_id, "scope": scope}
    return {"type": "permission_denied", "tool_id": tool_id}


class AttachPermissionBridge:
    """Routes daemon permission prompts to the visible attach client."""

    def __init__(self) -> None:
        self._pending: Dict[str, asyncio.Future[ConfirmationResponse]] = {}
        self._registered_rpc_servers: set[int] = set()
        # Attach-client side: one prompt on screen at a time, and the prompts
        # (shown or queued) that another window answered first.
        self._prompt_lock = asyncio.Lock()
        self._showing: Optional[str] = None
        self._queued: set[str] = set()
        self._answered_elsewhere: set[str] = set()

    def register_response_handler(self, rpc_server: Any) -> None:
        """Register the daemon-side RPC handler that resolves prompts."""
        server_id = id(rpc_server)
        if server_id in self._registered_rpc_servers:
            return

        async def _respond(params: dict[str, Any]) -> dict[str, Any]:
            tool_id = str(params.get("tool_id") or "")
            response_name = str(params.get("response") or "")
            response = self._parse_response(response_name)
            future = self._pending.get(tool_id)
            if future is None or future.done():
                return {"ok": False, "error": f"no pending prompt for {tool_id}"}
            future.set_result(response)
            return {"ok": True}

        try:
            rpc_server.register(PERMISSION_RESPONSE_RPC_METHOD, _respond)
            self._registered_rpc_servers.add(server_id)
        except ValueError:
            self._registered_rpc_servers.add(server_id)

    async def request_confirmation(
        self,
        *,
        display_tap: Any,
        rpc_server: Any,
        details: dict[str, Any],
        timeout: float,
    ) -> ConfirmationResponse:
        """Publish a prompt to attach clients and wait for a response."""
        self.register_response_handler(rpc_server)

        tool_id = str(details.get("tool_id") or uuid.uuid4().hex)
        details["tool_id"] = tool_id

        loop = asyncio.get_running_loop()
        future: asyncio.Future[ConfirmationResponse] = loop.create_future()
        self._pending[tool_id] = future

        display_tap.publish({"type": "permission_request", "details": details})

        response = ConfirmationResponse.DENY
        try:
            response = await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("attach permission prompt timed out for %s", tool_id)
        finally:
            self._pending.pop(tool_id, None)
            # Every attached window shows this prompt (a terminal and the web UI
            # can watch one daemon): tell them all it is closed, so the ones that
            # did not answer stop showing it.
            display_tap.publish(resolution_event(tool_id, response))
        return response

    async def handle_client_event(
        self,
        *,
        rpc_client: Any,
        layout_manager: Any,
        event: dict[str, Any],
        wait_for_rpc_reply: bool = True,
    ) -> None:
        """Show a permission_request event locally and send the answer back.

        Another window can answer first (handle_client_resolution): then the
        prompt closes here and no answer is sent.
        """
        details = event.get("details") or {}
        if not isinstance(details, dict):
            details = {}
        tool_id = str(details.get("tool_id") or "")

        self._queued.add(tool_id)
        try:
            async with self._prompt_lock:
                self._queued.discard(tool_id)
                if tool_id in self._answered_elsewhere:
                    self._answered_elsewhere.discard(tool_id)
                    return
                self._showing = tool_id
                try:
                    response = await layout_manager.show_permission_prompt(details)
                finally:
                    self._showing = None
                if tool_id in self._answered_elsewhere:
                    self._answered_elsewhere.discard(tool_id)
                    return
        finally:
            self._queued.discard(tool_id)
        response_name = getattr(response, "name", str(response))

        response_call = rpc_client.call(
            PERMISSION_RESPONSE_RPC_METHOD,
            {
                "tool_id": details.get("tool_id"),
                "response": response_name,
            },
            timeout=10,
        )
        if wait_for_rpc_reply:
            await response_call
            return

        task = asyncio.create_task(response_call)

        def _log_response_error(done_task: asyncio.Task[Any]) -> None:
            # Cancellation is expected during attach shutdown; do not surface it
            # as an unhandled callback exception (CancelledError is BaseException).
            if done_task.cancelled():
                return
            try:
                done_task.result()
            except Exception as exc:
                logger.warning(
                    "attach permission response rpc failed: %s",
                    exc,
                    exc_info=True,
                )

        task.add_done_callback(_log_response_error)

    def handle_client_resolution(self, *, layout_manager: Any, event: dict[str, Any]) -> None:
        """A prompt closed on the daemon (any window answered it): close it here.

        Only a prompt this window is showing or has queued; the echo of this
        window's own answer finds neither and does nothing.
        """
        tool_id = str(event.get("tool_id") or "")
        if not tool_id:
            return
        if tool_id == self._showing:
            self._answered_elsewhere.add(tool_id)
            layout_manager.cancel_permission_prompt()
        elif tool_id in self._queued:
            self._answered_elsewhere.add(tool_id)

    def has_visible_attach_client(self, display_tap: Any) -> bool:
        """Return true when at least one attach client can answer prompts."""
        return bool(getattr(display_tap, "subscriber_count", 0))

    async def wait_for_visible_attach_client(
        self,
        display_tap: Any,
        *,
        timeout: float = DEFAULT_VISIBLE_CLIENT_GRACE_SECONDS,
    ) -> bool:
        """Wait briefly for an attach client subscription to become visible.

        Attach sends its ack before the streaming subscription is registered.
        A fast first tool call can otherwise fall back to the hidden daemon TUI
        and leave the visible client stuck at "executing tools".
        """
        if self.has_visible_attach_client(display_tap):
            return True

        deadline = asyncio.get_running_loop().time() + max(timeout, 0)
        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.05)
            if self.has_visible_attach_client(display_tap):
                return True

        return self.has_visible_attach_client(display_tap)

    def _parse_response(self, response_name: str) -> ConfirmationResponse:
        """Parse wire response names from the attach client."""
        value = response_name.strip()
        if not value:
            return ConfirmationResponse.DENY
        if value in ConfirmationResponse.__members__:
            return ConfirmationResponse[value]
        upper = value.upper()
        if upper in ConfirmationResponse.__members__:
            return ConfirmationResponse[upper]
        return ConfirmationResponse.DENY
