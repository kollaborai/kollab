"""Attach-mode permission prompt bridge tests."""

import asyncio
import copy

from kollabor.llm.permissions.attach_bridge import (
    PERMISSION_RESPONSE_RPC_METHOD,
    AttachPermissionBridge,
    resolution_event,
)
from kollabor_events.permissions_models import ConfirmationResponse


class FakeDisplayTap:
    def __init__(self, subscriber_count=1):
        self.subscriber_count = subscriber_count
        self.events = []

    def publish(self, event):
        self.events.append(copy.deepcopy(event))  # as sent: a real tap serializes it


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
            {"tool_id": "terminal_0", "control": "APPROVE_ONCE"}
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
                {"tool_id": "terminal_0", "control": "APPROVE_ONCE"},
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
                {"tool_id": "terminal_0", "control": "APPROVE_ONCE"},
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
            self.timeout = kwargs["timeout"]
            return ConfirmationResponse.APPROVE_ONCE

    app = SimpleNamespace(
        args=SimpleNamespace(detached=detached),
        event_bus=SimpleNamespace(get_service=services.get),
        renderer=SimpleNamespace(layout_manager=SimpleNamespace()),
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
    assert app._attach_permission_bridge.timeout is None  # a parked prompt's card stays open


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
        assert [call[1] for call in rpc_client.calls] == [{"tool_id": "terminal_2", "control": "APPROVE_ONCE"}]
        assert bridge._answered_elsewhere == set()

    asyncio.run(run_bridge())


def test_local_and_attach_prompts_share_first_answer_and_reject_late_answer():
    async def run_bridge():
        daemon = AttachPermissionBridge()
        local = AttachPermissionBridge()
        remote = AttachPermissionBridge()
        display_tap = FakeDisplayTap()
        rpc_server = FakeRpcServer()
        local_rpc = FakeRpcClient()
        remote_rpc = FakeRpcClient()
        local_layout = WaitingLayoutManager()
        remote_layout = WaitingLayoutManager()
        details = {
            "tool_id": "acp-shared",
            "source": "acp",
            "acp_options": [
                {"optionId": "owned-first", "kind": "allow_once", "name": "Allow"},
                {"optionId": "owned-deny", "kind": "reject_once", "name": "Deny"},
            ],
        }
        daemon_task = asyncio.create_task(
            daemon.request_confirmation(
                display_tap=display_tap,
                rpc_server=rpc_server,
                details=details,
                timeout=1,
            )
        )
        await asyncio.sleep(0)
        event = {"type": "permission_request", "details": details}
        local_task = asyncio.create_task(
            local.handle_client_event(
                rpc_client=local_rpc,
                layout_manager=local_layout,
                event=event,
            )
        )
        remote_task = asyncio.create_task(
            remote.handle_client_event(
                rpc_client=remote_rpc,
                layout_manager=remote_layout,
                event=event,
            )
        )
        await asyncio.sleep(0)
        local_layout.press("owned-first")
        await local_task
        result = await rpc_server.handlers[PERMISSION_RESPONSE_RPC_METHOD](
            {"tool_id": "acp-shared", "option_id": "owned-first"}
        )
        assert result == {"ok": True}
        assert await daemon_task == "owned-first"
        resolution = resolution_event("acp-shared", "owned-first", details)
        local.handle_client_resolution(layout_manager=local_layout, event=resolution)
        remote.handle_client_resolution(layout_manager=remote_layout, event=resolution)
        await asyncio.wait_for(remote_task, 1)
        late = await rpc_server.handlers[PERMISSION_RESPONSE_RPC_METHOD](
            {"tool_id": "acp-shared", "option_id": "owned-deny"}
        )
        assert late["ok"] is False
        assert remote_rpc.calls == []
        assert local_rpc.calls == [(PERMISSION_RESPONSE_RPC_METHOD, {
            "tool_id": "acp-shared", "option_id": "owned-first"
        }, 10)]

    asyncio.run(run_bridge())


def test_attach_bridge_accepts_offered_opaque_acp_option_id():
    async def run_bridge():
        bridge = AttachPermissionBridge()
        display_tap = FakeDisplayTap()
        rpc_server = FakeRpcServer()
        details = {
            "tool_id": "acp_0",
            "source": "acp",
            "acp_options": [
                {"optionId": "opaque-first", "kind": "allow_once", "name": "Allow"},
                {"optionId": "opaque-second", "kind": "reject_always", "name": "Reject"},
            ],
        }
        task = asyncio.create_task(
            bridge.request_confirmation(
                display_tap=display_tap,
                rpc_server=rpc_server,
                details=details,
                timeout=1,
            )
        )
        await asyncio.sleep(0)
        accepted = await rpc_server.handlers[PERMISSION_RESPONSE_RPC_METHOD](
            {"tool_id": "acp_0", "option_id": "opaque-second"}
        )
        assert accepted == {"ok": True}
        assert await task == "opaque-second"

    asyncio.run(run_bridge())


def test_attach_bridge_rejects_unoffered_acp_id_without_consuming_prompt():
    async def run_bridge():
        bridge = AttachPermissionBridge()
        display_tap = FakeDisplayTap()
        rpc_server = FakeRpcServer()
        task = asyncio.create_task(
            bridge.request_confirmation(
                display_tap=display_tap,
                rpc_server=rpc_server,
                details={
                    "tool_id": "acp-opaque",
                    "source": "acp",
                    "acp_options": [
                        {"optionId": "owned-first", "kind": "allow_once", "name": "First"},
                        {"optionId": "owned-second", "kind": "allow_once", "name": "Second"},
                    ],
                },
                timeout=1,
            )
        )
        await asyncio.sleep(0)
        handler = rpc_server.handlers[PERMISSION_RESPONSE_RPC_METHOD]
        assert (await handler({"tool_id": "acp-opaque", "option_id": "not-offered"}))["ok"] is False
        assert not task.done()
        assert await handler({"tool_id": "acp-opaque", "option_id": "owned-second"}) == {"ok": True}
        assert await task == "owned-second"

    asyncio.run(run_bridge())


def test_attach_bridge_rejects_duplicate_response_selectors_before_settling():
    async def run_bridge():
        bridge = AttachPermissionBridge()
        rpc_server = FakeRpcServer()
        display_tap = FakeDisplayTap()
        task = asyncio.create_task(
            bridge.request_confirmation(
                display_tap=display_tap,
                rpc_server=rpc_server,
                details={"tool_id": "selector-collision", "source": "acp", "acp_options": []},
                timeout=1,
            )
        )
        await asyncio.sleep(0)
        handler = rpc_server.handlers[PERMISSION_RESPONSE_RPC_METHOD]
        result = await handler(
            {"tool_id": "selector-collision", "control": "CANCEL", "response": "DENY"}
        )
        assert result["ok"] is False
        assert not task.done()
        valid = await handler({"tool_id": "selector-collision", "control": "CANCEL"})
        assert valid == {"ok": True}
        assert await task is ConfirmationResponse.CANCEL

    asyncio.run(run_bridge())


def test_attach_bridge_accepts_native_cancel_alongside_acp_options():
    async def run_bridge():
        bridge = AttachPermissionBridge()
        display_tap = FakeDisplayTap()
        rpc_server = FakeRpcServer()
        task = asyncio.create_task(
            bridge.request_confirmation(
                display_tap=display_tap,
                rpc_server=rpc_server,
                details={
                    "tool_id": "acp-cancel",
                    "tool_type": "terminal",
                    "acp_options": [{"optionId": "owned", "kind": "allow_once"}],
                },
                timeout=1,
            )
        )
        await asyncio.sleep(0)
        result = await rpc_server.handlers[PERMISSION_RESPONSE_RPC_METHOD](
            {"tool_id": "acp-cancel", "control": "CANCEL"}
        )
        assert result == {"ok": True}
        assert await task is ConfirmationResponse.CANCEL

    asyncio.run(run_bridge())


def test_attach_bridge_accepts_acp_cancel_for_nonterminal_kind():
    async def run_bridge():
        bridge = AttachPermissionBridge()
        display_tap = FakeDisplayTap()
        rpc_server = FakeRpcServer()
        task = asyncio.create_task(
            bridge.request_confirmation(
                display_tap=display_tap,
                rpc_server=rpc_server,
                details={
                    "tool_id": "acp-edit",
                    "source": "acp",
                    "tool_type": "file_edit",
                    "operation": "edit:/tmp/work/file.py",
                    "acp_options": [{"optionId": "owned", "kind": "allow_once"}],
                },
                timeout=1,
            )
        )
        await asyncio.sleep(0)
        result = await rpc_server.handlers[PERMISSION_RESPONSE_RPC_METHOD](
            {"tool_id": "acp-edit", "control": "CANCEL"}
        )
        assert result == {"ok": True}
        assert await task is ConfirmationResponse.CANCEL

    asyncio.run(run_bridge())


def test_acp_opaque_id_wins_over_native_enum_name_collision():
    details = {
        "source": "acp",
        "acp_options": [
            {"optionId": "APPROVE_ONCE", "kind": "reject_always", "name": "Never"},
        ],
    }
    assert resolution_event("tool", "APPROVE_ONCE", details) == {
        "type": "permission_denied",
        "tool_id": "tool",
        "option_id": "APPROVE_ONCE",
        "vendor_saved": True,
        "warning": "Saved by the ACP provider; Kollab cannot undo it.",
    }


def test_acp_vendor_always_broadcast_is_truthful_without_edit_scope():
    event = resolution_event(
        "tool",
        "vendor-saved",
        {
            "source": "acp",
            "acp_options": [
                {"optionId": "vendor-saved", "kind": "allow_always"},
            ],
        },
    )
    assert event == {
        "type": "permission_granted",
        "tool_id": "tool",
        "option_id": "vendor-saved",
        "vendor_saved": True,
        "warning": "Saved by the ACP provider; Kollab cannot undo it.",
    }


def test_native_approve_always_scope_is_unchanged():
    assert resolution_event("tool", ConfirmationResponse.APPROVE_ALWAYS) == {
        "type": "permission_granted",
        "tool_id": "tool",
        "scope": "always_edits",
    }


def test_acp_vendor_reject_always_broadcast_is_truthful_without_scope():
    event = resolution_event(
        "tool",
        "vendor-denied",
        {
            "source": "acp",
            "acp_options": [
                {"optionId": "vendor-denied", "kind": "reject_always"},
            ],
        },
    )
    assert event == {
        "type": "permission_denied",
        "tool_id": "tool",
        "option_id": "vendor-denied",
        "vendor_saved": True,
        "warning": "Saved by the ACP provider; Kollab cannot undo it.",
    }


def test_attach_bridge_keeps_native_control_distinct_from_opaque_id_collision():
    async def run_bridge():
        bridge = AttachPermissionBridge()
        display_tap = FakeDisplayTap()
        rpc_server = FakeRpcServer()
        task = asyncio.create_task(
            bridge.request_confirmation(
                display_tap=display_tap,
                rpc_server=rpc_server,
                details={
                    "tool_id": "acp-collision",
                    "source": "acp",
                    "acp_options": [
                        {"optionId": "DENY", "kind": "allow_always", "name": "Vendor"},
                    ],
                },
                timeout=1,
            )
        )
        await asyncio.sleep(0)
        # Native d/CANCEL is carried as a control; an opaque vendor option
        # with the same spelling must not consume the prompt.
        result = await rpc_server.handlers[PERMISSION_RESPONSE_RPC_METHOD](
            {"tool_id": "acp-collision", "control": "CANCEL"}
        )
        assert result == {"ok": True}
        assert await task is ConfirmationResponse.CANCEL

    asyncio.run(run_bridge())


def test_an_attached_window_receives_acp_prompts_without_detached_mode():
    from kollabor.application import TerminalLLMChat

    app, asked = _prompting_app(detached=False)
    response = asyncio.run(
        TerminalLLMChat._try_attach_permission_prompt(
            app, {"source": "acp", "tool_type": "acp", "acp_options": []}
        )
    )
    assert response is ConfirmationResponse.APPROVE_ONCE
    assert asked == [{"source": "acp", "tool_type": "acp", "acp_options": []}]
    assert app._attach_permission_bridge.timeout == 300  # ACP's never parks


def test_attach_permission_timeout_cleans_pending_and_broadcasts_denial():
    async def run_bridge():
        bridge = AttachPermissionBridge()
        display_tap = FakeDisplayTap()
        rpc_server = FakeRpcServer()
        result = await bridge.request_confirmation(
            display_tap=display_tap,
            rpc_server=rpc_server,
            details={"tool_id": "timeout", "source": "acp", "acp_options": []},
            timeout=0.01,
        )
        assert result is ConfirmationResponse.DENY
        assert "timeout" not in bridge._pending
        assert display_tap.events[-1]["type"] == "permission_denied"

    asyncio.run(run_bridge())


class QuietBus:
    async def emit_with_hooks(self, *args, **kwargs):
        return {}


def _confirm_all_manager():
    from kollabor_agent.permissions.manager import PermissionManager
    from kollabor_agent.permissions.risk_assessor import RiskAssessor
    from kollabor_events.dict_utils import deep_merge
    from kollabor_events.permissions_config import PERMISSION_CONFIG_DEFAULTS
    from kollabor_events.permissions_models import RiskAssessmentRules

    config = deep_merge(
        PERMISSION_CONFIG_DEFAULTS,
        {"kollabor": {"permissions": {"approval_mode": "confirm_all"}}},
    )
    return PermissionManager(
        config=config,
        risk_assessor=RiskAssessor(rules=RiskAssessmentRules(), config=config),
        event_bus=QuietBus(),
    )


SHELL_CALL = {"id": "t1", "type": "terminal", "name": "terminal", "command": "ls"}


def test_an_acp_stop_answers_every_open_prompt_cancelled():
    # An ACP Stop ends the turn, so no prompt may keep holding it, and every
    # window closes the prompt it shows.
    async def run_bridge():
        bridge, display_tap = AttachPermissionBridge(), FakeDisplayTap()
        prompts = [_prompt(bridge, display_tap, "terminal_0"), _prompt(bridge, display_tap, "terminal_1")]
        await asyncio.sleep(0)
        bridge.cancel_pending()
        answers = await asyncio.gather(*prompts)
        return answers, [event for event in display_tap.events if event["type"] != "permission_request"]

    answers, closed = asyncio.run(run_bridge())
    assert answers == [ConfirmationResponse.CANCEL, ConfirmationResponse.CANCEL]
    assert closed == [
        {"type": "permission_denied", "tool_id": "terminal_0", "outcome": "cancelled"},
        {"type": "permission_denied", "tool_id": "terminal_1", "outcome": "cancelled"},
    ]


def test_an_acp_stop_refuses_the_tool_its_turn_waits_to_approve():
    # ACP's Stop while a tool waits for approval: the tool is refused at once
    # instead of holding the turn for 5 minutes.
    from kollabor_agent.permissions import handle_confirmation_response

    async def scenario():
        bridge, display_tap = AttachPermissionBridge(), FakeDisplayTap()
        manager = _confirm_all_manager()

        async def ask(details):
            response = await bridge.request_confirmation(
                display_tap=display_tap, rpc_server=FakeRpcServer(), details=dict(details), timeout=300
            )
            return await handle_confirmation_response(manager, details, response)

        manager.set_confirmation_callback(ask, cancel=bridge.cancel_pending)
        check = asyncio.create_task(manager.check_permission(dict(SHELL_CALL)))
        for _ in range(100):
            if bridge._pending:
                break
            await asyncio.sleep(0)
        assert bridge._pending  # the prompt is open
        manager.cancel_confirmations()
        return await asyncio.wait_for(check, 1)

    decision = asyncio.run(scenario())
    assert not decision.allowed and decision.reason == "User cancelled operation"


async def _waiting_prompt(manager, bridge, display_tap, *, stopping=False):
    """A Confirm All check on the real bridge, waiting on its open prompt."""
    from types import SimpleNamespace

    from kollabor_agent.permissions import handle_confirmation_response

    async def ask(details):
        response = await bridge.request_confirmation(
            display_tap=display_tap, rpc_server=FakeRpcServer(), details=dict(details), timeout=None
        )
        return await handle_confirmation_response(manager, details, response)

    manager.set_confirmation_callback(
        ask, cancel=bridge.cancel_pending, park=bridge.park, settle=bridge.try_settle_response
    )
    manager.set_tool_executor(SimpleNamespace(is_cancelled=lambda: stopping))
    check = asyncio.create_task(manager.check_permission(dict(SHELL_CALL)))
    for _ in range(100):
        if bridge._pending or check.done():
            break
        await asyncio.sleep(0)
    return check


def test_a_stop_leaves_its_prompt_open_and_the_tool_asks_for_a_yes():
    # A Stop while a tool waits for approval ends the turn at once, and the
    # card stays open: Allow, or the user's yes, runs it later.
    from kollabor_agent.yes_approval import NEEDS_YES

    async def scenario():
        bridge, display_tap, manager = AttachPermissionBridge(), FakeDisplayTap(), _confirm_all_manager()
        check = await _waiting_prompt(manager, bridge, display_tap)
        assert bridge._pending  # the prompt is open
        manager.park_confirmations()
        decision = await asyncio.wait_for(check, 1)
        await asyncio.sleep(0.01)
        return decision, set(bridge._pending), list(display_tap.events)

    decision, pending, events = asyncio.run(scenario())
    assert not decision.allowed and decision.reason == NEEDS_YES
    assert pending == {"t1"}  # its card is still open
    assert events[-1] == {"type": "permission_parked", "tool_id": "t1"}


def test_a_prompt_unanswered_for_5_minutes_parks(monkeypatch):
    from kollabor_agent.permissions import manager as manager_module
    from kollabor_agent.yes_approval import NEEDS_YES

    monkeypatch.setattr(manager_module, "PARK_AFTER_SECONDS", 0.01)

    async def scenario():
        bridge, display_tap, manager = AttachPermissionBridge(), FakeDisplayTap(), _confirm_all_manager()
        check = await _waiting_prompt(manager, bridge, display_tap)
        decision = await asyncio.wait_for(check, 1)
        await asyncio.sleep(0.01)
        return decision, set(bridge._pending), list(display_tap.events)

    decision, pending, events = asyncio.run(scenario())
    assert decision.reason == NEEDS_YES  # the model asks for the OK in its reply
    assert pending == {"t1"}
    assert events[-1] == {"type": "permission_parked", "tool_id": "t1"}


def test_a_prompt_asked_while_its_turn_stops_shows_and_parks():
    from kollabor_agent.yes_approval import NEEDS_YES

    async def scenario():
        bridge, display_tap, manager = AttachPermissionBridge(), FakeDisplayTap(), _confirm_all_manager()
        check = await _waiting_prompt(manager, bridge, display_tap, stopping=True)
        decision = await asyncio.wait_for(check, 1)
        await asyncio.sleep(0.01)
        return decision, set(bridge._pending), list(display_tap.events)

    decision, pending, events = asyncio.run(scenario())
    assert decision.reason == NEEDS_YES
    # The card shows; terminals are told before it opens, so none shows it.
    assert [event["type"] for event in events] == ["permission_request", "permission_parked"]
    assert events[0]["details"]["parked"] is True
    assert pending == {"t1"}


def test_a_parked_prompt_closes_in_a_terminal_without_an_answer():
    # Its turn went on: a key typed for the next message must not answer it.
    async def run_client():
        bridge, layout, rpc = AttachPermissionBridge(), WaitingLayoutManager(), FakeRpcClient()
        shown = asyncio.create_task(
            bridge.handle_client_event(
                rpc_client=rpc, layout_manager=layout,
                event={"type": "permission_request", "details": {"tool_id": "t1"}},
            )
        )
        await asyncio.sleep(0.01)
        assert layout.shown == ["t1"]
        bridge.handle_client_resolution(
            layout_manager=layout, event={"type": "permission_parked", "tool_id": "t1"}
        )
        await asyncio.wait_for(shown, 1)
        parked = AttachPermissionBridge()
        await parked.handle_client_event(
            rpc_client=rpc, layout_manager=layout,
            event={"type": "permission_request", "details": {"tool_id": "t2", "parked": True}},
        )
        return rpc.calls, layout

    calls, layout = asyncio.run(run_client())
    assert calls == []  # no answer sent from the terminal
    assert layout.shown == ["t1"]  # the one asked while its turn stopped never showed


def test_a_parked_prompts_answer_says_it_was_parked():
    # The web's answer stream ends on a parked prompt's Deny: no turn follows.
    async def run_bridge():
        bridge, display_tap = AttachPermissionBridge(), FakeDisplayTap()
        prompt = _prompt(bridge, display_tap, "t1", timeout=None)
        await asyncio.sleep(0)
        bridge.park("t1")
        bridge.park("t1")  # announced once
        bridge.try_settle_response("t1", ConfirmationResponse.DENY)
        return await prompt, display_tap.events

    answer, events = asyncio.run(run_bridge())
    assert answer is ConfirmationResponse.DENY
    assert [event["type"] for event in events] == ["permission_request", "permission_parked", "permission_denied"]
    assert events[-1] == {"type": "permission_denied", "tool_id": "t1", "parked": True}


def test_a_terminal_never_shows_a_prompt_its_turn_already_left_open():
    from types import SimpleNamespace

    from kollabor.application import TerminalLLMChat

    layout = FakeLayoutManager()
    app = SimpleNamespace(renderer=SimpleNamespace(layout_manager=layout))
    parked = asyncio.run(TerminalLLMChat._show_terminal_permission_prompt(app, {"parked": True}))
    assert parked is ConfirmationResponse.CANCEL and not hasattr(layout, "details")
    shown = asyncio.run(TerminalLLMChat._show_terminal_permission_prompt(app, {"tool_id": "t2"}))
    assert shown is ConfirmationResponse.APPROVE_ONCE and layout.details == {"tool_id": "t2"}
