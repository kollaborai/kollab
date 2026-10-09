"""Attach-mode permission prompt bridge tests."""

import asyncio

from kollabor.llm.permissions.attach_bridge import (
    PERMISSION_RESPONSE_RPC_METHOD,
    AttachPermissionBridge,
)
from kollabor_events.permissions_models import ConfirmationResponse


class FakeDisplayTap:
    def __init__(self, subscriber_count=1):
        self.subscriber_count = subscriber_count
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


class BlockingRpcClient:
    def __init__(self):
        self.calls = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def call(self, method, params, timeout=None):
        self.calls.append((method, params, timeout))
        self.started.set()
        await self.release.wait()
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


def test_attach_bridge_client_event_can_send_response_without_waiting_for_reply():
    async def run_bridge():
        bridge = AttachPermissionBridge()
        rpc_client = BlockingRpcClient()
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
            wait_for_rpc_reply=False,
        )

        await asyncio.wait_for(rpc_client.started.wait(), timeout=1)
        assert rpc_client.calls == [
            (
                PERMISSION_RESPONSE_RPC_METHOD,
                {"tool_id": "terminal_0", "response": "APPROVE_ONCE"},
                10,
            )
        ]
        rpc_client.release.set()
        await asyncio.sleep(0)

    asyncio.run(run_bridge())


def test_attach_bridge_fire_and_forget_ignores_cancellation():
    async def run_bridge():
        bridge = AttachPermissionBridge()
        rpc_client = BlockingRpcClient()
        layout_manager = FakeLayoutManager()
        loop = asyncio.get_running_loop()
        errors = []
        loop.set_exception_handler(lambda _loop, context: errors.append(context))

        await bridge.handle_client_event(
            rpc_client=rpc_client,
            layout_manager=layout_manager,
            event={"type": "permission_request", "details": {"tool_id": "terminal_0"}},
            wait_for_rpc_reply=False,
        )
        await asyncio.wait_for(rpc_client.started.wait(), timeout=1)

        rpc_tasks = [
            task
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task() and not task.done()
        ]
        assert rpc_tasks
        for task in rpc_tasks:
            task.cancel()
        await asyncio.sleep(0)

        assert not errors

    asyncio.run(run_bridge())


def test_attach_bridge_waits_for_late_visible_client():
    async def run_bridge():
        bridge = AttachPermissionBridge()
        display_tap = FakeDisplayTap(subscriber_count=0)

        async def subscribe_later():
            await asyncio.sleep(0.05)
            display_tap.subscriber_count = 1

        task = asyncio.create_task(subscribe_later())
        assert await bridge.wait_for_visible_attach_client(
            display_tap,
            timeout=0.5,
        )
        await task

    asyncio.run(run_bridge())


def test_attach_bridge_returns_false_when_no_visible_client_arrives():
    async def run_bridge():
        bridge = AttachPermissionBridge()
        display_tap = FakeDisplayTap(subscriber_count=0)

        assert not await bridge.wait_for_visible_attach_client(
            display_tap,
            timeout=0.01,
        )

    asyncio.run(run_bridge())


def _prompting_app(detached):
    """A TerminalLLMChat stand-in with one attach client watching it."""
    from types import SimpleNamespace

    services = {"display_tap": FakeDisplayTap(subscriber_count=1), "rpc_server": FakeRpcServer()}
    asked = []

    class Bridge:
        async def wait_for_visible_attach_client(self, display_tap):
            return True

        async def request_confirmation(self, **kwargs):
            asked.append(kwargs["details"])
            return ConfirmationResponse.APPROVE_ONCE

    app = SimpleNamespace(
        args=SimpleNamespace(detached=detached),
        event_bus=SimpleNamespace(get_service=services.get),
        _attach_permission_bridge=Bridge(),
    )
    return app, asked


def test_a_terminal_session_keeps_its_prompts_while_a_window_is_attached():
    # Opening a --no-daemon terminal session in the web UI attaches the engine to
    # it; the prompt must still show in that terminal, not move to the browser.
    from kollabor.application import TerminalLLMChat

    app, asked = _prompting_app(detached=False)
    response = asyncio.run(TerminalLLMChat._try_attach_permission_prompt(app, {"tool": "shell"}))

    assert response is None
    assert asked == []


def test_a_detached_daemon_sends_its_prompts_to_the_attached_window():
    from kollabor.application import TerminalLLMChat

    app, asked = _prompting_app(detached=True)
    response = asyncio.run(TerminalLLMChat._try_attach_permission_prompt(app, {"tool": "shell"}))

    assert response is ConfirmationResponse.APPROVE_ONCE
    assert asked == [{"tool": "shell"}]


def _prompt(bridge, display_tap, tool_id="terminal_0", timeout=1):
    return asyncio.create_task(
        bridge.request_confirmation(
            display_tap=display_tap,
            rpc_server=FakeRpcServer(),
            details={"tool_id": tool_id, "tool_type": "terminal"},
            timeout=timeout,
        )
    )


def test_a_closed_prompt_is_announced_to_every_attached_window():
    # A terminal and the web UI can both watch one daemon: whichever answers,
    # the other must hear the prompt is closed.
    async def run_bridge():
        bridge = AttachPermissionBridge()
        display_tap = FakeDisplayTap()
        approved = _prompt(bridge, display_tap, "terminal_0")
        await asyncio.sleep(0)
        bridge._pending["terminal_0"].set_result(ConfirmationResponse.APPROVE_SESSION)
        await approved
        denied = _prompt(bridge, display_tap, "terminal_1")
        await asyncio.sleep(0)
        bridge._pending["terminal_1"].set_result(ConfirmationResponse.DENY)
        await denied
        expired = _prompt(bridge, display_tap, "terminal_2", timeout=0.01)
        assert await expired is ConfirmationResponse.DENY
        return [event for event in display_tap.events if event["type"] != "permission_request"]

    assert asyncio.run(run_bridge()) == [
        {"type": "permission_granted", "tool_id": "terminal_0", "scope": "session"},
        {"type": "permission_denied", "tool_id": "terminal_1"},
        {"type": "permission_denied", "tool_id": "terminal_2"},
    ]


class WaitingLayoutManager:
    """A terminal prompt that waits for a key, like render_layout's."""

    def __init__(self):
        self.shown = []
        self._answer = None

    async def show_permission_prompt(self, details):
        self.shown.append(details["tool_id"])
        self._answer = asyncio.get_running_loop().create_future()
        return await self._answer

    def press(self, response):
        self._answer.set_result(response)

    def cancel_permission_prompt(self):
        if self._answer is not None and not self._answer.done():
            self._answer.set_result(ConfirmationResponse.CANCEL)


def test_a_prompt_another_window_answered_closes_without_an_answer_from_here():
    async def run_bridge():
        bridge = AttachPermissionBridge()
        rpc_client = FakeRpcClient()
        layout = WaitingLayoutManager()

        def request(tool_id):
            return asyncio.create_task(
                bridge.handle_client_event(
                    rpc_client=rpc_client,
                    layout_manager=layout,
                    event={"type": "permission_request", "details": {"tool_id": tool_id}},
                )
            )

        showing, queued = request("terminal_0"), request("terminal_1")
        await asyncio.sleep(0.01)
        assert layout.shown == ["terminal_0"]  # one prompt on screen at a time

        # The web UI answers both: the shown one closes, the queued one never shows.
        for tool_id in ("terminal_0", "terminal_1"):
            bridge.handle_client_resolution(
                layout_manager=layout, event={"type": "permission_granted", "tool_id": tool_id}
            )
        await asyncio.wait_for(asyncio.gather(showing, queued), 2)
        assert layout.shown == ["terminal_0"]
        assert rpc_client.calls == []

        # This window's own answer comes back as the same event: nothing to close.
        mine = request("terminal_2")
        await asyncio.sleep(0.01)
        layout.press(ConfirmationResponse.APPROVE_ONCE)
        await mine
        bridge.handle_client_resolution(
            layout_manager=layout, event={"type": "permission_granted", "tool_id": "terminal_2"}
        )
        assert [call[1] for call in rpc_client.calls] == [{"tool_id": "terminal_2", "response": "APPROVE_ONCE"}]
        assert bridge._answered_elsewhere == set()

    asyncio.run(run_bridge())
