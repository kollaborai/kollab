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


def resolution_event(
    tool_id: str,
    response: Any,
    details: dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Resolve the offered choice without losing its explicit turn owner."""
    event = _permission_resolution(tool_id, response, details)
    if details:
        for key in ("request_id", "turn_id", "input_source", "generation", "thread_id"):
            if details.get(key) is not None:
                event[key] = details[key]
    return event


def _permission_resolution(
    tool_id: str,
    response: Any,
    details: dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build the broadcast emitted when an attached prompt is resolved.

    ACP option IDs are opaque strings and must be resolved before native enum
    names.  An ACP provider is allowed to offer an ID such as
    ``APPROVE_ONCE`` for a rejecting option; treating that ID as a native
    control would incorrectly grant access to every attached window.
    """
    if isinstance(response, str) and details:
        options = details.get("acp_options")
        if isinstance(options, (list, tuple)):
            for option in options:
                if not isinstance(option, dict):
                    continue
                if str(option.get("optionId") or "") != response:
                    continue
                kind = str(option.get("kind") or "").lower()
                if kind == "allow_once":
                    return {
                        "type": "permission_granted",
                        "tool_id": tool_id,
                        "scope": "once",
                        "option_id": response,
                    }
                if kind == "reject_once":
                    return {"type": "permission_denied", "tool_id": tool_id}
                if kind in {"allow_always", "reject_always"}:
                    return {
                        "type": "permission_granted"
                        if kind == "allow_always"
                        else "permission_denied",
                        "tool_id": tool_id,
                        "option_id": response,
                        "vendor_saved": True,
                        "warning": (
                            "Saved by the ACP provider; Kollab cannot undo it."
                        ),
                    }
                return {"type": "permission_denied", "tool_id": tool_id}

    if response is ConfirmationResponse.CANCEL:
        return {
            "type": "permission_denied",
            "tool_id": tool_id,
            "outcome": "cancelled",
        }

    response_name = getattr(response, "name", None)
    scope = _GRANTED_SCOPES.get(response_name or str(response))
    if scope:
        return {"type": "permission_granted", "tool_id": tool_id, "scope": scope}
    return {"type": "permission_denied", "tool_id": tool_id}


class AttachPermissionBridge:
    """Routes daemon permission prompts to the visible attach client."""

    def __init__(self) -> None:
        self._pending: Dict[str, asyncio.Future[ConfirmationResponse | str]] = {}
        self._pending_option_ids: Dict[str, set[str]] = {}
        self._pending_details: Dict[str, dict[str, Any]] = {}
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
            option_id = params.get("option_id")
            control_name = params.get("control")
            response_field = params.get("response")
            selector_count = sum(
                value is not None
                for value in (option_id, control_name, response_field)
            )
            if selector_count != 1:
                return {"ok": False, "error": f"mixed permission response for {tool_id}"}
            future = self._pending.get(tool_id)
            offered_ids = self._pending_option_ids.get(tool_id, set())
            details = self._pending_details.get(tool_id, {})
            if option_id is not None:
                response_name = str(option_id)
                if response_name not in offered_ids:
                    return {"ok": False, "error": f"unoffered option for {tool_id}"}
                response: ConfirmationResponse | str = response_name
            else:
                response_name = str(control_name if control_name is not None else response_field or "")
                if not self._is_native_response_name(response_name):
                    return {"ok": False, "error": f"unsupported control for {tool_id}"}
                response = self._parse_response(response_name)
                if not self._native_response_allowed(details, response, response_name):
                    return {"ok": False, "error": f"unoffered control for {tool_id}"}
            if future is None or future.done():
                return {"ok": False, "error": f"no pending prompt for {tool_id}"}
            if not self.try_settle_response(tool_id, response):
                return {"ok": False, "error": f"no pending prompt for {tool_id}"}
            return {"ok": True}

        try:
            rpc_server.register(PERMISSION_RESPONSE_RPC_METHOD, _respond)
            self._registered_rpc_servers.add(server_id)
        except ValueError:
            self._registered_rpc_servers.add(server_id)

    def try_settle_response(self, tool_id: str, response: Any) -> bool:
        """Atomically settle one offered response from any surface."""
        future = self._pending.get(tool_id)
        if future is None or future.done():
            return False
        details = self._pending_details.get(tool_id, {})
        if isinstance(response, str):
            if response not in self._pending_option_ids.get(tool_id, set()):
                return False
        elif isinstance(response, ConfirmationResponse):
            if not self._native_response_allowed(details, response, response.name):
                return False
        else:
            return False
        future.set_result(response)
        return True

    async def request_confirmation(
        self,
        *,
        display_tap: Any,
        rpc_server: Any,
        details: dict[str, Any],
        timeout: float,
        local_layout_manager: Any = None,
    ) -> ConfirmationResponse | str:
        """Share one pending decision between local and attached surfaces."""
        self.register_response_handler(rpc_server)
        tool_id = str(details.get("tool_id") or uuid.uuid4().hex)
        details["tool_id"] = tool_id
        future = asyncio.get_running_loop().create_future()
        self._pending[tool_id] = future
        self._pending_details[tool_id] = details
        self._pending_option_ids[tool_id] = {
            str(option.get("optionId"))
            for option in details.get("acp_options", ())
            if isinstance(option, dict) and option.get("optionId")
        }
        local_task = None
        response: ConfirmationResponse | str = ConfirmationResponse.CANCEL
        try:
            if local_layout_manager is not None:
                async def local_answer() -> None:
                    try:
                        candidate = await local_layout_manager.show_permission_prompt(details)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("local ACP permission prompt failed for %s", tool_id)
                        self.try_settle_response(tool_id, ConfirmationResponse.CANCEL)
                        return
                    self.try_settle_response(tool_id, candidate)
                local_task = asyncio.create_task(local_answer())
            display_tap.publish({"type": "permission_request", "details": details})
            try:
                response = await asyncio.wait_for(future, timeout=timeout)
            except asyncio.TimeoutError:
                response = ConfirmationResponse.DENY
        finally:
            self._pending.pop(tool_id, None)
            self._pending_option_ids.pop(tool_id, None)
            self._pending_details.pop(tool_id, None)
            if not details.pop("_acp_resolution_published", False):
                display_tap.publish(resolution_event(tool_id, response, details))
            if local_task is not None:
                if not local_task.done():
                    local_layout_manager.cancel_permission_prompt()
                    local_task.cancel()
                await asyncio.gather(local_task, return_exceptions=True)
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
        response_name = getattr(response, "name", None)
        response_params = {"tool_id": details.get("tool_id")}
        if response_name is not None:
            response_params["control"] = response_name
        else:
            response_params["option_id"] = str(response)

        response_call = rpc_client.call(
            PERMISSION_RESPONSE_RPC_METHOD,
            response_params,
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

    @staticmethod
    def _is_native_response_name(value: str) -> bool:
        return value.strip().upper() in ConfirmationResponse.__members__

    @staticmethod
    def _native_response_allowed(
        details: dict[str, Any],
        response: ConfirmationResponse,
        response_name: str,
    ) -> bool:
        """Allow only explicitly eligible native controls for this prompt."""
        if str(details.get("source") or "").lower() == "acp":
            if response is ConfirmationResponse.CANCEL:
                return True
            options = details.get("acp_options") or []
            once = [
                item for item in options
                if isinstance(item, dict) and item.get("kind") == "allow_once"
            ]
            operation = details.get("operation")
            if response is ConfirmationResponse.APPROVE_ONCE:
                return len(once) == 1
            if response in (
                ConfirmationResponse.APPROVE_SESSION,
                ConfirmationResponse.APPROVE_PROJECT,
            ):
                return len(once) == 1 and bool(operation)
            if response is ConfirmationResponse.APPROVE_ALWAYS:
                return (
                    len(once) == 1
                    and bool(operation)
                    and (
                        (isinstance(operation, dict) and operation.get("kind") == "edit")
                        or (isinstance(operation, str) and operation.split(":", 1)[0] == "edit")
                    )
                )
            return response is ConfirmationResponse.DENY
        if not details.get("acp_options"):
            return True
        return response in {
            ConfirmationResponse.APPROVE_ONCE,
            ConfirmationResponse.DENY,
            ConfirmationResponse.CANCEL,
        }


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
