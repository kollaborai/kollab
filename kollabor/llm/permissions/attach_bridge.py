"""Attach-mode permission prompt bridge."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Dict, Optional

from kollabor_events.permissions_models import ConfirmationResponse

logger = logging.getLogger(__name__)

PERMISSION_RESPONSE_RPC_METHOD = "permission.respond"


class AttachPermissionBridge:
    """Routes daemon permission prompts to the visible attach client."""

    def __init__(self) -> None:
        self._pending: Dict[str, asyncio.Future[ConfirmationResponse]] = {}
        self._registered_rpc_servers: set[int] = set()

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

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning("attach permission prompt timed out for %s", tool_id)
            return ConfirmationResponse.DENY
        finally:
            self._pending.pop(tool_id, None)

    async def handle_client_event(
        self,
        *,
        rpc_client: Any,
        layout_manager: Any,
        event: dict[str, Any],
    ) -> None:
        """Show a permission_request event locally and send the answer back."""
        details = event.get("details") or {}
        if not isinstance(details, dict):
            details = {}

        response = await layout_manager.show_permission_prompt(details)
        response_name = getattr(response, "name", str(response))

        await rpc_client.call(
            PERMISSION_RESPONSE_RPC_METHOD,
            {
                "tool_id": details.get("tool_id"),
                "response": response_name,
            },
            timeout=10,
        )

    def has_visible_attach_client(self, display_tap: Any) -> bool:
        """Return true when at least one attach client can answer prompts."""
        return bool(getattr(display_tap, "subscriber_count", 0))

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
