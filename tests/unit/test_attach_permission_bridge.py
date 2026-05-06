"""Attach-mode permission prompt bridge tests."""

import asyncio

from kollabor.llm.permissions.attach_bridge import (
    PERMISSION_RESPONSE_RPC_METHOD,
    AttachPermissionBridge,
)
from kollabor_events.permissions_models import ConfirmationResponse


class FakeDisplayTap:
    subscriber_count = 1

    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)


class FakeRpcServer:
    def __init__(self):
        self.handlers = {}

    def register(self, method, handler):
        if method in self.handlers:
            raise ValueError("duplicate")
        self.handlers[method] = handler


class FakeRpcClient:
    def __init__(self):
        self.calls = []

    async def call(self, method, params, timeout=None):
        self.calls.append((method, params, timeout))
        return {"ok": True}


class FakeLayoutManager:
    async def show_permission_prompt(self, details):
        self.details = details
        return ConfirmationResponse.APPROVE_ONCE


def test_attach_bridge_publishes_prompt_and_waits_for_rpc_response():
    async def run_bridge():
        bridge = AttachPermissionBridge()
        display_tap = FakeDisplayTap()
        rpc_server = FakeRpcServer()

        task = asyncio.create_task(
            bridge.request_confirmation(
                display_tap=display_tap,
                rpc_server=rpc_server,
                details={
                    "tool_id": "terminal_0",
                    "tool_type": "terminal",
                    "risk_level": "MEDIUM",
                },
                timeout=1,
            )
        )

        await asyncio.sleep(0)

        assert display_tap.events == [
            {
                "type": "permission_request",
                "details": {
                    "tool_id": "terminal_0",
                    "tool_type": "terminal",
                    "risk_level": "MEDIUM",
                },
            }
        ]
        assert PERMISSION_RESPONSE_RPC_METHOD in rpc_server.handlers

        response = await rpc_server.handlers[PERMISSION_RESPONSE_RPC_METHOD](
            {"tool_id": "terminal_0", "response": "APPROVE_ONCE"}
        )

        assert response == {"ok": True}
        assert await task is ConfirmationResponse.APPROVE_ONCE

    asyncio.run(run_bridge())


def test_attach_bridge_client_event_shows_prompt_and_replies_over_rpc():
    async def run_bridge():
        bridge = AttachPermissionBridge()
        rpc_client = FakeRpcClient()
        layout_manager = FakeLayoutManager()

        await bridge.handle_client_event(
            rpc_client=rpc_client,
            layout_manager=layout_manager,
            event={
                "type": "permission_request",
                "details": {
                    "tool_id": "terminal_0",
                    "tool_type": "terminal",
                    "risk_level": "MEDIUM",
                },
            },
        )

        assert layout_manager.details["tool_id"] == "terminal_0"
        assert rpc_client.calls == [
            (
                PERMISSION_RESPONSE_RPC_METHOD,
                {"tool_id": "terminal_0", "response": "APPROVE_ONCE"},
                10,
            )
        ]

    asyncio.run(run_bridge())
