"""Lifecycle boundaries for the daemon-owned WebUI queue supervisor."""

from __future__ import annotations

import asyncio
import base64
import json
from types import SimpleNamespace

import pytest

from kollabor.state.web_message_queue_runtime import register_web_message_queue_runtime
from kollabor_rpc import RpcCallerContext, RpcServer
from kollabor_tui.display_tap import DisplayTap


class _EventBus:
    def __init__(self, **services):
        self.services = services

    def get_service(self, name):
        return self.services.get(name)

    def register_service(self, name, service):
        self.services[name] = service


class _State:
    def __init__(self, *, processing=True, event_bus=None, llm_service=None):
        from kollabor.state.web_message_queue_worker import ConversationIdentity

        self.identity = ConversationIdentity("conversation-a", 3)
        self.is_processing = processing
        self.dispatches = []
        self.dispatched = asyncio.Event()
        self._event_bus = event_bus
        self._llm_service = llm_service

    def get_conversation_identity(self):
        return self.identity

    def get_conversation_uid(self):
        return self.identity.uid

    async def submit_queue_message(self, message, *, queue_handoff, batch_lease=None):
        self.dispatches.append((message, queue_handoff, batch_lease))
        queue_handoff.provider_custody_id = f"test:{queue_handoff.request_id}"
        queue_handoff.recording_status = "recorded"
        self.dispatched.set()
        return {"status": "queued"}

    async def begin_queue_handoff_batch(self, request):
        return object()

    def finish_queue_handoff_batch(self, batch_lease, *, accepted_count, uncertain):
        return not uncertain


async def _call(server: RpcServer, method: str, params: dict) -> dict:
    return await server.handle_wire(
        {
            "action": "rpc_request",
            "request_id": "supervisor-lifecycle-test",
            "method": method,
            "params": params,
        },
        caller_context=RpcCallerContext.from_unix_peer(501),
    )


async def _enqueue_one(server: RpcServer) -> dict[str, str]:
    binding = {"expected_conversation_uid": "conversation-a"}
    allocated = await _call(
        server,
        "state.web_queue.allocate_ids",
        {**binding, "item_count": 1},
    )
    minted = allocated["result"]
    enqueued = await _call(
        server,
        "state.web_queue.enqueue_batch",
        {
            **binding,
            "operation_id": minted["operation_ids"][0],
            "batch_id": minted["batch_id"],
            "items": [
                {
                    "request_id": minted["request_ids"][0],
                    "payload_b64": base64.b64encode(
                        json.dumps({"text": "queued"}, separators=(",", ":")).encode()
                    ).decode("ascii"),
                }
            ],
        },
    )
    assert enqueued["error"] is None
    return minted


@pytest.mark.asyncio
async def test_acp_turn_complete_wakes_queue_if_idle_flag_clears_after_event():
    tap = DisplayTap()
    state = _State(event_bus=_EventBus(display_tap=tap))
    server = RpcServer()
    runtime = register_web_message_queue_runtime(server, state)
    minted = await _enqueue_one(server)

    await asyncio.sleep(0.02)
    assert state.dispatches == []

    # ACP publishes turn_complete immediately before its owner clears the busy
    # flag. The supervisor must retry the authoritative queue scan after idle.
    loop = asyncio.get_running_loop()
    loop.call_later(0.02, setattr, state, "is_processing", False)
    await asyncio.to_thread(tap.publish, {"type": "turn_complete"})
    try:
        await asyncio.wait_for(state.dispatched.wait(), timeout=0.5)
    finally:
        await runtime.close(timeout=0.1)

    assert [item[1].request_id for item in state.dispatches] == minted["request_ids"]
    assert tap._listeners == ()


@pytest.mark.asyncio
async def test_acp_turn_complete_waits_for_slow_completion_callback_settlement():
    tap = DisplayTap()
    acp = SimpleNamespace(_completion_tasks=set())
    llm = SimpleNamespace(_active_acp=lambda: acp)
    state = _State(event_bus=_EventBus(display_tap=tap), llm_service=llm)
    server = RpcServer()
    runtime = register_web_message_queue_runtime(server, state)
    minted = await _enqueue_one(server)
    release_completion = asyncio.Event()

    async def settle_completion():
        await release_completion.wait()
        state.is_processing = False

    completion = asyncio.create_task(settle_completion())
    acp._completion_tasks.add(completion)
    completion.add_done_callback(acp._completion_tasks.discard)

    asyncio.get_running_loop().call_later(2.2, release_completion.set)
    await asyncio.to_thread(tap.publish, {"type": "turn_complete"})
    try:
        await asyncio.wait_for(state.dispatched.wait(), timeout=3.0)
    finally:
        await runtime.close(timeout=0.1)

    assert [item[1].request_id for item in state.dispatches] == minted["request_ids"]
    assert tap._listeners == ()


@pytest.mark.asyncio
async def test_repeated_turn_complete_extends_active_idle_retry_window():
    tap = DisplayTap()
    state = _State(event_bus=_EventBus(display_tap=tap))
    server = RpcServer()
    runtime = register_web_message_queue_runtime(server, state)
    minted = await _enqueue_one(server)
    loop = asyncio.get_running_loop()
    loop.call_later(1.5, tap.publish, {"type": "turn_complete"})
    loop.call_later(2.2, setattr, state, "is_processing", False)

    await asyncio.to_thread(tap.publish, {"type": "turn_complete"})
    try:
        await asyncio.wait_for(state.dispatched.wait(), timeout=3.0)
    finally:
        await runtime.close(timeout=0.1)

    assert [item[1].request_id for item in state.dispatches] == minted["request_ids"]
    assert tap._listeners == ()


@pytest.mark.asyncio
async def test_active_acp_reports_send_now_only_when_batch_gate_is_available():
    class _ACP:
        active = True

    class _LLM:
        def __init__(self, *, gate):
            self.acp = _ACP()
            self.gate = gate

        def _active_acp(self):
            return self.acp

        def _profile_selects_acp(self):
            return True, False

        async def begin_queue_handoff_batch(self, request):
            if not self.gate:
                raise AssertionError("capability probing must not acquire a batch")

        def finish_queue_handoff_batch(self, lease, *, accepted_count, uncertain):
            if not self.gate:
                raise AssertionError("capability probing must not finish a batch")

    state = _State(llm_service=_LLM(gate=True))
    runtime = register_web_message_queue_runtime(RpcServer(), state)
    assert runtime._send_now_available() is True
    assert await runtime.close() is True

    unavailable_state = _State(llm_service=_LLM(gate=False))
    unavailable = register_web_message_queue_runtime(RpcServer(), unavailable_state)
    # A selected ACP session with no real gate support must fail closed.
    unavailable_state._llm_service.begin_queue_handoff_batch = None
    assert unavailable._send_now_available() is False
    assert await unavailable.close() is True


@pytest.mark.asyncio
async def test_unavailable_send_now_still_authenticates_the_caller():
    server = RpcServer()

    class _UnavailableState:
        def get_conversation_uid(self):
            return "conversation-a"

    runtime = register_web_message_queue_runtime(server, _UnavailableState())
    assert runtime.dispatch_available is False

    denied = await server.handle_wire(
        {
            "action": "rpc_request",
            "request_id": "unauthenticated-send-now",
            "method": "state.web_queue.send_now",
            "params": {
                "expected_conversation_uid": "conversation-a",
                "batch_id": "batch-a",
            },
        },
        caller_context=None,
    )

    assert denied["error"] is not None
    assert denied["result"] is None
    assert await runtime.close() is True


@pytest.mark.asyncio
async def test_close_is_bounded_and_retries_a_worker_that_did_not_settle():
    class _RetryWorker:
        def __init__(self):
            self.calls = 0
            self.release_first = asyncio.Event()

        async def close(self, *, timeout):
            self.calls += 1
            if self.calls == 1:
                await self.release_first.wait()
                return False
            return True

    state = _State()
    runtime = register_web_message_queue_runtime(RpcServer(), state)
    worker = _RetryWorker()
    runtime._active_workers.add(worker)

    first_task = asyncio.create_task(runtime.close(timeout=0.01))
    done, _ = await asyncio.wait({first_task}, timeout=0.1)
    first_finished_in_time = bool(done)
    worker.release_first.set()
    first = await first_task
    await asyncio.sleep(0)
    second = await runtime.close(timeout=0.1)

    assert first_finished_in_time is True
    assert first is False
    assert second is True
    assert worker.calls == 2


@pytest.mark.asyncio
async def test_close_does_not_wait_forever_for_a_cancellation_resistant_supervisor():
    state = _State()
    runtime = register_web_message_queue_runtime(RpcServer(), state)
    release = asyncio.Event()

    async def cancellation_resistant():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()

    task = asyncio.create_task(cancellation_resistant())
    runtime._supervisor_task = task
    closing = asyncio.create_task(runtime.close(timeout=0.01))
    done, _ = await asyncio.wait({closing}, timeout=0.1)
    closed_in_time = bool(done)
    release.set()
    await task
    first = await closing
    second = await runtime.close(timeout=0.1)

    assert closed_in_time is True
    assert first is False
    assert second is True


@pytest.mark.asyncio
async def test_concurrent_close_call_respects_its_own_timeout_waiting_for_lock():
    class _HeldWorker:
        def __init__(self):
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def close(self, *, timeout):
            self.entered.set()
            await self.release.wait()
            return False

    runtime = register_web_message_queue_runtime(RpcServer(), _State())
    worker = _HeldWorker()
    runtime._active_workers.add(worker)

    first = asyncio.create_task(runtime.close(timeout=0.5))
    await asyncio.wait_for(worker.entered.wait(), timeout=0.1)
    second = asyncio.create_task(runtime.close(timeout=0.01))
    done, _ = await asyncio.wait({second}, timeout=0.05)
    second_finished_in_time = bool(done)
    worker.release.set()
    await first
    second_result = await second

    assert second_finished_in_time is True
    assert second_result is False


@pytest.mark.asyncio
async def test_application_closes_queue_runtime_before_shutting_down_llm():
    from kollabor.application import TerminalLLMChat

    order = []

    class _QueueRuntime:
        async def close(self):
            order.append("queue.close")
            return True

    class _Hub:
        async def stop_message_intake(self):
            order.append("hub.stop_intake")

    class _Service:
        def __init__(self, name):
            self.name = name

        async def shutdown(self):
            order.append(f"{self.name}.shutdown")

    class _EventBus:
        async def emit_with_hooks(self, *args, **kwargs):
            order.append("event.shutdown")

        def get_service(self, name):
            return {
                "hub_plugin": _Hub(),
                "web_message_queue_runtime": _QueueRuntime(),
            }.get(name)

    app = object.__new__(TerminalLLMChat)
    app.event_bus = _EventBus()
    app.script_widget_manager = SimpleNamespace(
        shutdown_refresh_scheduler=lambda: _async_record(order, "scripts.shutdown")
    )
    app.input_handler = SimpleNamespace(
        stop=lambda: _async_record(order, "input.stop")
    )
    app.llm_service = _Service("llm")
    app.conversation_logger = _Service("logger")
    app.mcp_integration = _Service("mcp")
    app.plugin_instances = {}

    await app.shutdown()

    assert order.index("queue.close") < order.index("llm.shutdown")


def _shutdown_app(order, queue_results, *, acp=None):
    from kollabor.application import TerminalLLMChat

    class _QueueRuntime:
        def __init__(self):
            self.results = list(queue_results)

        async def close(self):
            order.append("queue.close")
            return self.results.pop(0)

    class _Hub:
        async def stop_message_intake(self):
            order.append("hub.stop_intake")

    class _Service:
        def __init__(self, name):
            self.name = name

        async def shutdown(self):
            order.append(f"{self.name}.shutdown")

    class _EventBus:
        async def emit_with_hooks(self, *args, **kwargs):
            order.append("event.shutdown")

        def get_service(self, name):
            return {
                "hub_plugin": _Hub(),
                "web_message_queue_runtime": queue_runtime,
            }.get(name)

    class _InputHandler:
        running = True

        async def stop(self):
            order.append("input.stop")

    class _ScriptWidgetManager:
        async def shutdown_refresh_scheduler(self):
            order.append("scripts.shutdown")

    queue_runtime = _QueueRuntime()
    app = object.__new__(TerminalLLMChat)
    app.event_bus = _EventBus()
    app.script_widget_manager = _ScriptWidgetManager()
    app.input_handler = _InputHandler()
    app.llm_service = _Service("llm")
    app.conversation_logger = _Service("logger")
    app.mcp_integration = _Service("mcp")
    app.plugin_instances = {}
    app.running = True
    app._background_tasks = []
    if acp is not None:
        app.acp_orchestrator = acp
    return app


@pytest.mark.asyncio
async def test_shutdown_leaves_provider_open_when_queue_close_is_unconfirmed():
    order = []
    app = _shutdown_app(order, [False, True])

    first = await app.shutdown()

    assert first is False
    assert "llm.shutdown" not in order
    assert "logger.shutdown" not in order
    assert "mcp.shutdown" not in order
    assert getattr(app, "_shutdown_complete", False) is False

    second = await app.shutdown()

    assert second is True
    assert order.index("queue.close") < order.index("llm.shutdown")
    assert order.count("llm.shutdown") == 1
    assert app._shutdown_complete is True


@pytest.mark.asyncio
async def test_cleanup_closes_queue_before_acp_and_retries_after_unconfirmed_close(
    monkeypatch,
):
    from kollabor import code_service

    order = []

    class _ACP:
        async def shutdown(self):
            order.append("acp.shutdown")

    app = _shutdown_app(order, [False, True], acp=_ACP())

    async def fake_code_service_shutdown():
        order.append("code_service.shutdown")

    monkeypatch.setattr(code_service, "shutdown", fake_code_service_shutdown)

    first = await app.cleanup()

    assert first is False
    assert order.index("hub.stop_intake") < order.index("queue.close")
    assert "acp.shutdown" not in order
    assert "llm.shutdown" not in order
    assert getattr(app, "_cleanup_complete", False) is False
    assert getattr(app, "_shutdown_complete", False) is False

    second = await app.cleanup()

    assert second is True
    assert order.index("queue.close") < order.index("acp.shutdown")
    assert order.index("acp.shutdown") < order.index("llm.shutdown")
    assert app._cleanup_complete is True
    assert app._shutdown_complete is True


async def _async_record(order, label):
    order.append(label)
