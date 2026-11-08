"""Coverage for human input producers that bypass the terminal composer."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor.application import TerminalLLMChat
from kollabor.llm.agent_hud import AgentHudEntry
from kollabor.llm.llm_coordinator import LLMService
from kollabor.state import LocalStateService
from kollabor.user_input_source import UserInputSource
from kollabor_agent.queue_processor import QueuedInput, TaggedInput
from kollabor_events import EventBus, EventType, Hook, HookPriority


async def _pipeline(
    *, cancel_pre: bool = False, submission_result: dict | None = None
):
    bus = EventBus()
    calls: list[tuple[str, str, str]] = []
    pending_tool = AsyncMock()

    async def pre(data, event):
        calls.append(("pre", event.source, str(data["message"])))
        if cancel_pre:
            event.cancelled = True
        else:
            event.data["message"] = f"{data['message']} [normalized]"
        return data

    async def main(data, event):
        calls.append(("main", event.source, str(data["message"])))
        await pending_tool()
        return (
            submission_result
            if submission_result is not None
            else {"status": "queued"}
        )

    async def post(data, event):
        calls.append(("post", event.source, str(data["message"])))
        return data

    for hook in (
        Hook(
            name="normalize_human_input",
            plugin_name="test",
            event_type=EventType.USER_INPUT_PRE,
            priority=HookPriority.SECURITY.value,
            callback=pre,
        ),
        Hook(
            name="process_user_input",
            plugin_name="llm_core",
            event_type=EventType.USER_INPUT,
            priority=HookPriority.LLM.value,
            callback=main,
        ),
        Hook(
            name="record_human_input",
            plugin_name="test",
            event_type=EventType.USER_INPUT_POST,
            priority=HookPriority.DISPLAY.value,
            callback=post,
        ),
    ):
        assert await bus.register_hook(hook)

    service = object.__new__(LLMService)
    service.event_bus = bus
    return bus, service, calls, pending_tool


class _StateLLMProxy:
    def __init__(self, service, *, processing_states=()):
        self._service = service
        self._processing_states = iter(processing_states)
        self.tasks = []
        self.config = MagicMock()
        self.context_calls = []

    async def submit_human_input(
        self, message, *, source, pre_displayed=False, **kwargs
    ):
        self.context_calls.append(kwargs)
        return await self._service.submit_human_input(
            message, source=source, pre_displayed=pre_displayed, **kwargs
        )

    def create_background_task(self, coro, name=None):
        task = asyncio.create_task(coro, name=name)
        self.tasks.append(task)
        return task

    async def register_hooks(self):
        return None

    @property
    def is_processing(self):
        try:
            return next(self._processing_states)
        except StopIteration:
            return False


@pytest.mark.asyncio
async def test_state_rpc_uses_one_full_user_input_event_and_propagates_pre_data():
    bus, llm, calls, pending_tool = await _pipeline()
    proxy = _StateLLMProxy(llm)
    state = LocalStateService(
        llm_service=proxy,
        profile_manager=MagicMock(),
        event_bus=bus,
    )

    result = await state.send_message("ordinary request")
    assert result["accepted"] is True
    await asyncio.gather(*proxy.tasks)

    assert calls == [
        ("pre", "state_rpc", "ordinary request"),
        ("main", "state_rpc", "ordinary request [normalized]"),
        ("post", "state_rpc", "ordinary request [normalized]"),
    ]
    pending_tool.assert_awaited_once()


@pytest.mark.asyncio
async def test_state_rpc_accepts_injected_submission_result():
    bus, llm, calls, pending_tool = await _pipeline(
        submission_result={"status": "injected"}
    )
    proxy = _StateLLMProxy(llm)
    state = LocalStateService(
        llm_service=proxy,
        profile_manager=MagicMock(),
        event_bus=bus,
    )

    result = await state.send_message("steered request")

    assert result["accepted"] is True, result
    assert result["reason"] == ""
    await asyncio.gather(*proxy.tasks)
    assert [phase for phase, _, _ in calls] == ["pre", "main", "post"]
    assert {source for _, source, _ in calls} == {"state_rpc"}
    pending_tool.assert_awaited_once()


@pytest.mark.asyncio
async def test_state_rpc_preserves_separate_request_thread_context():
    bus, llm, _, pending_tool = await _pipeline()
    proxy = _StateLLMProxy(llm)
    state = LocalStateService(
        llm_service=proxy, profile_manager=MagicMock(), event_bus=bus
    )
    result = await state.send_message(
        {"message": "ordinary request", "request_id": "req-state",
         "thread_id": "thread-state", "origin_context": {"sender": "agent-a"}}
    )
    assert result["accepted"] is True
    await asyncio.gather(*proxy.tasks)
    assert proxy.context_calls[-1] == {
        "request_id": "req-state", "thread_id": "thread-state",
        "origin_context": {"sender": "agent-a"},
    }
    pending_tool.assert_awaited_once()


@pytest.mark.asyncio
async def test_state_rpc_does_not_acknowledge_missing_input_handler():
    bus = EventBus()
    service = object.__new__(LLMService)
    service.event_bus = bus
    proxy = _StateLLMProxy(service)
    state = LocalStateService(
        llm_service=proxy,
        profile_manager=MagicMock(),
        event_bus=bus,
    )

    result = await state.send_message("ordinary request")

    assert result == {"accepted": False, "reason": "input_handler_unavailable"}
    assert proxy.tasks == []


@pytest.mark.asyncio
async def test_cli_initial_prompt_uses_user_input_hooks_once():
    _, llm, calls, _ = await _pipeline()
    app = object.__new__(TerminalLLMChat)
    app.llm_service = _StateLLMProxy(llm)

    result = await app._submit_cli_initial_message("first prompt")

    assert result["status"] == "queued"
    assert [phase for phase, _, _ in calls] == ["pre", "main", "post"]
    assert {source for _, source, _ in calls} == {"cli_initial"}


@pytest.mark.asyncio
async def test_pipe_input_uses_same_pipeline_and_stops_on_pre_cancellation():
    _, llm, calls, pending_tool = await _pipeline(cancel_pre=True)
    proxy = _StateLLMProxy(llm, processing_states=[False])
    proxy.api_service = SimpleNamespace(is_provider_available=lambda: True)
    renderer = SimpleNamespace(
        pipe_mode=False,
        message_renderer=SimpleNamespace(pipe_mode=False),
    )
    app = object.__new__(TerminalLLMChat)
    app.renderer = renderer
    app.llm_service = proxy
    app.pipe_mode = False
    app._startup_complete = False
    app.running = False
    app._initialize_llm_core = AsyncMock()
    app._initialize_plugins = AsyncMock()
    app.cleanup = AsyncMock()

    with pytest.raises(SystemExit) as exc_info:
        await app.start_pipe_mode("do not process")

    assert exc_info.value.code == 1
    assert calls == [("pre", "pipe", "do not process")]
    pending_tool.assert_not_awaited()
    app.cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_native_tui_event_runs_pre_main_post_once():
    bus, _, calls, pending_tool = await _pipeline()

    result = await bus.emit_with_hooks(
        EventType.USER_INPUT,
        {"message": "typed in terminal"},
        UserInputSource.TUI.value,
    )

    assert result["cancelled"] is False
    assert [phase for phase, _, _ in calls] == ["pre", "main", "post"]
    assert {source for _, source, _ in calls} == {"user"}
    assert calls[1][2] == "typed in terminal [normalized]"
    pending_tool.assert_awaited_once()


@pytest.mark.asyncio
async def test_pre_cancellation_stops_model_and_pending_tool_execution():
    _, llm, calls, pending_tool = await _pipeline(cancel_pre=True)

    result = await llm.submit_human_input(
        "cancel before model", source=UserInputSource.STATE_RPC
    )

    assert result == {"status": "cancelled", "phase": "pre_user_input"}
    assert calls == [("pre", "state_rpc", "cancel before model")]
    pending_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_internal_caller_cannot_select_a_human_source_string():
    _, llm, calls, _ = await _pipeline()

    result = await llm.submit_human_input("forged", source="user")  # type: ignore[arg-type]

    assert result == {"status": "rejected", "reason": "invalid_input_source"}
    assert calls == []


@pytest.mark.asyncio
async def test_submit_rejects_when_primary_input_handler_is_unregistered():
    service = object.__new__(LLMService)
    service.event_bus = EventBus()

    result = await service.submit_human_input(
        "no handler", source=UserInputSource.STATE_RPC
    )

    assert result == {
        "status": "rejected",
        "reason": "input_handler_unavailable",
    }


@pytest.mark.asyncio
async def test_submit_rejects_when_primary_input_handler_fails():
    bus = EventBus()
    service = object.__new__(LLMService)
    service.event_bus = bus

    async def fail(_data, _event):
        raise RuntimeError("private failure detail")

    assert await bus.register_hook(
        Hook(
            name="process_user_input",
            plugin_name="llm_core",
            event_type=EventType.USER_INPUT,
            priority=HookPriority.LLM.value,
            callback=fail,
        )
    )

    result = await service.submit_human_input(
        "handler error", source=UserInputSource.STATE_RPC
    )

    assert result == {"status": "rejected", "reason": "input_processing_failed"}


@pytest.mark.asyncio
async def test_pipe_slash_command_runs_through_registry_and_prints(capsys):
    """`kollab -p "/connect <domain>"` prints the outcome, no LLM call (#155)."""
    bus, llm, calls, pending_tool = await _pipeline()
    proxy = _StateLLMProxy(llm)
    proxy.api_service = SimpleNamespace(is_provider_available=lambda: True)
    renderer = SimpleNamespace(
        pipe_mode=False,
        message_renderer=SimpleNamespace(pipe_mode=False),
    )
    app = object.__new__(TerminalLLMChat)
    app.renderer = renderer
    app.llm_service = proxy
    app.pipe_mode = False
    app._startup_complete = False
    app.running = False
    app._initialize_llm_core = AsyncMock()
    app._initialize_plugins = AsyncMock()
    app.cleanup = AsyncMock()

    slash_parser = SimpleNamespace(
        parse_command=lambda text: SimpleNamespace(name="connect", args="relay.example")
    )
    printed = []

    class _Result:
        message = "network marco-home via relay.example"
        display_type = None
        success = True
        ui_config = None

    executor = SimpleNamespace(
        execute_command=AsyncMock(return_value=_Result()),
    )
    app.event_bus = bus
    app.input_handler = SimpleNamespace(
        slash_parser=slash_parser, command_executor=executor
    )
    app._print_command_result = lambda result: printed.append(result.message)

    with pytest.raises(SystemExit) as exc_info:
        await app.start_pipe_mode("/connect relay.example")

    assert exc_info.value.code == 0
    executor.execute_command.assert_awaited_once()
    assert printed == ["network marco-home via relay.example"]
    # The command must never reach the model as chat text.
    assert calls == []
    pending_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_pipe_slash_command_prints_unknown_for_unparsed_input(capsys):
    """An unparsable slash command says so instead of failing silently."""
    bus, llm, calls, _ = await _pipeline()
    proxy = _StateLLMProxy(llm)
    proxy.api_service = SimpleNamespace(is_provider_available=lambda: True)
    renderer = SimpleNamespace(
        pipe_mode=False,
        message_renderer=SimpleNamespace(pipe_mode=False),
    )
    app = object.__new__(TerminalLLMChat)
    app.renderer = renderer
    app.llm_service = proxy
    app.pipe_mode = False
    app._startup_complete = False
    app.running = False
    app._initialize_llm_core = AsyncMock()
    app._initialize_plugins = AsyncMock()
    app.cleanup = AsyncMock()
    app.event_bus = bus
    app.input_handler = SimpleNamespace(
        slash_parser=SimpleNamespace(parse_command=lambda text: None),
        command_executor=SimpleNamespace(execute_command=AsyncMock()),
    )

    with pytest.raises(SystemExit) as exc_info:
        await app.start_pipe_mode("/no-such-command")

    assert exc_info.value.code == 2
    assert "Unknown command" in capsys.readouterr().out
    assert calls == []


@pytest.mark.asyncio
async def test_pipe_plain_text_still_reaches_the_llm():
    """Non-slash pipe input is untouched by the command registry path."""
    bus, llm, calls, _ = await _pipeline(cancel_pre=True)
    proxy = _StateLLMProxy(llm, processing_states=[False])
    proxy.api_service = SimpleNamespace(is_provider_available=lambda: True)
    renderer = SimpleNamespace(
        pipe_mode=False,
        message_renderer=SimpleNamespace(pipe_mode=False),
    )
    app = object.__new__(TerminalLLMChat)
    app.renderer = renderer
    app.llm_service = proxy
    app.pipe_mode = False
    app._startup_complete = False
    app.running = False
    app._initialize_llm_core = AsyncMock()
    app._initialize_plugins = AsyncMock()
    app.cleanup = AsyncMock()

    executor = SimpleNamespace(execute_command=AsyncMock())
    app.event_bus = bus
    app.input_handler = SimpleNamespace(
        slash_parser=SimpleNamespace(parse_command=lambda text: None),
        command_executor=executor,
    )

    with pytest.raises(SystemExit) as exc_info:
        await app.start_pipe_mode("ordinary question")

    # The stub LLM queue stops pipe mode as before (exit 1), unchanged path.
    assert exc_info.value.code == 1
    executor.execute_command.assert_not_awaited()
    assert calls == [("pre", "pipe", "ordinary question")]

@pytest.mark.asyncio
async def test_selected_acp_profile_rejects_when_orchestrator_missing():
    service = object.__new__(LLMService)
    service.event_bus = EventBus()
    profile = SimpleNamespace(get_provider=lambda: "acp")
    service.profile_manager = SimpleNamespace(get_active_profile=lambda: profile)

    result = await service.submit_human_input("hello", source=UserInputSource.STATE_RPC)

    assert result == {"status": "rejected", "reason": "acp_unavailable"}


@pytest.mark.asyncio
async def test_acp_startup_failure_keeps_code_and_actionable_detail():
    service = object.__new__(LLMService)
    service.event_bus = EventBus()
    service.event_bus.register_service("acp_orchestrator", SimpleNamespace(
        active=False, startup_error="claude-agent-acp not found. Install the pinned adapter."
    ))
    service.profile_manager = SimpleNamespace(
        get_active_profile=lambda: SimpleNamespace(get_provider=lambda: "acp")
    )
    result = await service.submit_human_input("hello", source=UserInputSource.STATE_RPC)
    assert result["reason"] == "acp_unavailable"
    assert result["status"] == "rejected"
    assert "Install the pinned adapter" in result["message"]



@pytest.mark.asyncio
async def test_acp_admission_emits_hooks_once_and_returns_immediately():
    bus = EventBus()
    service = object.__new__(LLMService)
    service.event_bus = bus
    calls = []

    class ACP:
        active = True

        async def submit(self, message, *, source):
            calls.append((message, source))

    bus.register_service("acp_orchestrator", ACP())
    bus.emit_with_hooks = AsyncMock(
        return_value={
            "pre": {},
            "main": {
                "hook_results": [
                    {
                        "hook_key": "llm_core.process_user_input",
                        "success": True,
                        "result": {"status": "queued", "request_id": "r1"},
                    }
                ]
            },
        }
    )
    result = await service.submit_human_input("hello", source=UserInputSource.STATE_RPC)
    assert result == {"status": "queued", "request_id": "r1"}
    bus.emit_with_hooks.assert_awaited_once()
    assert calls == []


@pytest.mark.asyncio
async def test_acp_main_hook_backgrounds_submit_without_native_fallback():
    bus = EventBus()
    service = object.__new__(LLMService)
    service.event_bus = bus
    tasks = []
    service.create_background_task = lambda coro, name=None: tasks.append(
        asyncio.create_task(coro, name=name)
    )
    calls = []

    class ACP:
        active = True

        async def steer(self, *args, **kwargs):
            return False

        def admit(self, message, *, request_id, source, thread_id=None, origin=None):
            calls.append((message, source))
            return SimpleNamespace(request_id=request_id, turn_id="turn-1")

    bus.register_service("acp_orchestrator", ACP())
    event = SimpleNamespace(source="state_rpc")
    result = await service._handle_user_input(
        {"message": "hello", "_acp_dispatch": True, "_acp_request_id": "r2"},
        event,
    )
    assert result["status"] == "queued"
    assert result["request_id"]
    assert result["turn_id"] == "turn-1"
    await asyncio.gather(*tasks)
    assert calls == [("hello", "state_rpc")]

@pytest.mark.asyncio
async def test_acp_admission_rejects_full_queue_before_hooks():
    bus = EventBus()
    service = object.__new__(LLMService)
    service.event_bus = bus

    class ACP:
        active = True

        async def steer(self, *args, **kwargs):
            return False

        def admit(self, *args, **kwargs):
            raise RuntimeError("ACP prompt queue is full")

    bus.register_service("acp_orchestrator", ACP())
    result = await service._handle_user_input({"message": "hello"}, SimpleNamespace(source="state_rpc"))
    assert result == {"status": "rejected", "reason": "acp_queue_full"}


@pytest.mark.asyncio
async def test_broad_admission_exception_with_full_text_keeps_queue_full():
    """Any admission exception whose text contains 'full' stays acp_queue_full.

    Main's broad admission handler maps queue-full text to acp_queue_full
    regardless of exception class; only the typed-first check narrows to
    RuntimeError/ValueError. A non-RuntimeError admission failure carrying
    the canonical queue-full text must not degrade to acp_admission_failed.
    """
    bus = EventBus()
    service = object.__new__(LLMService)
    service.event_bus = bus

    class WeirdAdmissionError(Exception):
        pass

    class ACP:
        active = True

        async def steer(self, *args, **kwargs):
            return False

        def admit(self, *args, **kwargs):
            raise WeirdAdmissionError("ACP prompt queue is full")

    bus.register_service("acp_orchestrator", ACP())
    result = await service._handle_user_input(
        {"message": "hello"}, SimpleNamespace(source="state_rpc")
    )
    assert result == {"status": "rejected", "reason": "acp_queue_full"}

    class PlainAdmissionError(Exception):
        pass

    class ACPOther:
        active = True

        async def steer(self, *args, **kwargs):
            return False

        def admit(self, *args, **kwargs):
            raise PlainAdmissionError("registry exploded")

    bus2 = EventBus()
    service2 = object.__new__(LLMService)
    service2.event_bus = bus2
    bus2.register_service("acp_orchestrator", ACPOther())
    result2 = await service2._handle_user_input(
        {"message": "hello"}, SimpleNamespace(source="state_rpc")
    )
    assert result2 == {"status": "rejected", "reason": "acp_admission_failed"}



@pytest.mark.asyncio
async def test_public_submit_settles_acp_queue_full_rejection():
    bus = EventBus()
    service = object.__new__(LLMService)
    service.event_bus = bus
    service.profile_manager = SimpleNamespace(
        get_active_profile=lambda: SimpleNamespace(get_provider=lambda: "acp")
    )

    class ACP:
        active = True

        async def steer(self, *args, **kwargs):
            return False

        def admit(self, *args, **kwargs):
            raise RuntimeError("ACP prompt queue is full")

    bus.register_service("acp_orchestrator", ACP())
    await bus.register_hook(
        Hook(
            name="process_user_input",
            plugin_name="llm_core",
            event_type=EventType.USER_INPUT,
            priority=HookPriority.LLM.value,
            callback=service._handle_user_input,
        )
    )

    result = await service.submit_human_input(
        "hello", source=UserInputSource.STATE_RPC
    )

    assert result == {"status": "rejected", "reason": "acp_queue_full"}


@pytest.mark.asyncio
async def test_authenticated_agent_input_is_nonhuman_and_permission_pending_rejected():
    bus = EventBus()
    seen = []

    async def reject_agent(data, event):
        seen.append(event.source)
        if event.source == "agent":
            event.cancelled = True
        return data

    await bus.register_hook(Hook(
        name="reject_agent_pending_permission", plugin_name="test",
        event_type=EventType.USER_INPUT_PRE, priority=HookPriority.SECURITY.value,
        callback=reject_agent,
    ))
    service = object.__new__(LLMService)
    service.event_bus = bus
    service.profile_manager = SimpleNamespace(
        get_active_profile=lambda: SimpleNamespace(get_provider=lambda: "acp")
    )
    bus.register_service("acp_orchestrator", SimpleNamespace(active=True))
    result = await service.submit_human_input(
        "agent command", source="agent",
        origin_context={"authenticated": True, "sender": "agent-a"},
    )
    assert result == {"status": "cancelled", "phase": "pre_user_input"}
    assert seen == ["agent"]


@pytest.mark.asyncio
async def test_authenticated_agent_rejects_acp_registry_lookup_failure():
    service = object.__new__(LLMService)
    service.event_bus = MagicMock()
    service.event_bus.get_service.side_effect = RuntimeError("registry closed")
    service.profile_manager = MagicMock()
    service.profile_manager.get_active_profile.return_value = SimpleNamespace(
        get_provider=lambda: "acp"
    )
    result = await service.submit_human_input(
        "agent command", source="agent",
        origin_context={"authenticated": True, "sender": "agent-a"},
        request_id="req-fail", thread_id="thread-fail",
    )
    assert result == {"status": "rejected", "reason": "acp_unavailable"}


async def _native_route_service(profile_provider: str, *, acp_active: bool):
    """Minimal service with real ACP admission and a spy native queue."""
    bus = EventBus()
    service = object.__new__(LLMService)
    service.event_bus = bus
    service.profile_manager = SimpleNamespace(
        get_active_profile=lambda: SimpleNamespace(
            get_provider=lambda: profile_provider
        )
    )
    service._pending_agent_hud = []
    service.current_parent_uuid = "parent-1"
    service._queue_processor = SimpleNamespace(
        enqueue=AsyncMock(),
        process_message_batch=AsyncMock(return_value="parent-2"),
        continue_conversation=AsyncMock(return_value="parent-3"),
        turn_completed=False,
    )
    service._enqueue_with_overflow_strategy = AsyncMock()
    service._message_handler = SimpleNamespace(
        handle_user_input=AsyncMock(return_value={"status": "native"})
    )
    service.message_display_service = SimpleNamespace(
        display_user_message=MagicMock(),
        display_error_message=MagicMock(),
    )
    service.conversation_logger = SimpleNamespace(
        log_user_message=AsyncMock(return_value="parent-2")
    )
    service.api_service = SimpleNamespace(model="native-model")
    service.supports_image_input = MagicMock(return_value=True)
    service.is_processing = True
    service.cancel_processing = False
    service.cancellation_message_shown = False
    service.create_background_task = MagicMock()

    class ACP:
        active = acp_active
        startup_error = None

        def __init__(self):
            self.admissions = []
            self.voice_rules_epochs = set()

        async def steer(self, *args, **kwargs):
            return False

        def admit(self, message, *, request_id, source, thread_id=None, origin=None):
            self.admissions.append((message, source))
            return SimpleNamespace(request_id=request_id, turn_id="turn-1")

    acp = ACP()
    if acp_active:
        bus.register_service("acp_orchestrator", acp)
    await bus.register_hook(
        Hook(
            name="process_user_input",
            plugin_name="llm_core",
            event_type=EventType.USER_INPUT,
            priority=HookPriority.LLM.value,
            callback=service._handle_user_input,
        )
    )
    return service, acp


@pytest.mark.asyncio
async def test_process_user_input_routes_active_acp_through_canonical_admission():
    service, acp = await _native_route_service("AcP", acp_active=True)

    result = await service.process_user_input("normal human prompt")

    assert result["status"] == "queued"
    assert acp.admissions == [("normal human prompt", UserInputSource.TUI.value)]
    service._enqueue_with_overflow_strategy.assert_not_awaited()
    service.conversation_logger.log_user_message.assert_not_awaited()
    service._message_handler.handle_user_input.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_user_input_rejects_unavailable_acp_without_native_enqueue():
    service, _ = await _native_route_service(" ACP ", acp_active=False)

    result = await service.process_user_input("normal human prompt")

    assert result["status"] == "rejected"
    assert result["reason"] == "acp_unavailable"
    service._enqueue_with_overflow_strategy.assert_not_awaited()
    service.conversation_logger.log_user_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_native_only_generation_is_rejected_for_active_acp():
    service, acp = await _native_route_service("acp", acp_active=True)

    result = await service.process_user_input("generate agent files", native_only=True)

    assert result == {
        "status": "rejected",
        "reason": "native_provider_required",
    }
    assert acp.admissions == []
    service._enqueue_with_overflow_strategy.assert_not_awaited()


@pytest.mark.asyncio
async def test_logger_await_cannot_reparent_an_admitted_user_turn():
    bus = EventBus()
    service = object.__new__(LLMService)
    service.event_bus = bus
    service.profile_manager = SimpleNamespace(
        get_active_profile=lambda: SimpleNamespace(get_provider=lambda: "native")
    )
    service._pending_agent_hud = []
    service.current_parent_uuid = "pre-admission-parent"
    service._turn_serial = 0
    service._cursor_winner_serial = -1
    service._continue_serial = None
    logger_started, release_logger, enqueued = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    logged_parents: list[str | None] = []
    captured_items: list[TaggedInput] = []

    async def log_user_message(_content, *, parent_uuid=None):
        logged_parents.append(parent_uuid)
        logger_started.set()
        await release_logger.wait()
        return "user-row"

    async def enqueue(item):
        captured_items.append(item)
        enqueued.set()
        return True

    service._queue_processor = SimpleNamespace(
        enqueue=enqueue,
        process_message_batch=AsyncMock(return_value="assistant-row"),
        continue_conversation=AsyncMock(return_value="parent-3"),
        turn_completed=False,
        is_processing=True,
        processing_queue=SimpleNamespace(full=MagicMock(return_value=False)),
    )
    service._enqueue_with_overflow_strategy = enqueue
    service.is_processing = True
    service.cancel_processing = False
    service.cancellation_message_shown = False
    service.create_background_task = MagicMock()
    service.message_display_service = SimpleNamespace(
        display_user_message=MagicMock(), display_error_message=MagicMock()
    )
    service.conversation_logger = SimpleNamespace(log_user_message=log_user_message)
    service._message_handler = SimpleNamespace(
        handle_user_input=AsyncMock(return_value={"status": "native"})
    )
    assert await bus.register_hook(
        Hook(
            name="process_user_input",
            plugin_name="llm_core",
            event_type=EventType.USER_INPUT,
            priority=HookPriority.LLM.value,
            callback=service._handle_user_input,
        )
    )

    task = asyncio.create_task(service.process_user_input("hello"))
    try:
        await asyncio.wait_for(logger_started.wait(), timeout=1)
        assert logged_parents == ["pre-admission-parent"]

        # Serial 2 publishes while serial 1 is suspended in the logger.
        later_serial = service.claim_turn_serial()
        assert service.publish_cursor("newer-hub-assistant", later_serial)
        release_logger.set()
        await asyncio.wait_for(task, timeout=1)
        await asyncio.wait_for(enqueued.wait(), timeout=1)
    finally:
        release_logger.set()
        if not task.done():
            await task

    [item] = captured_items
    assert item.parent_uuid == "user-row"
    assert item.admission_serial == 1
    assert service.current_parent_uuid == "newer-hub-assistant"


@pytest.mark.asyncio
async def test_older_batch_completion_does_not_restore_its_continue_ticket():
    first_started, second_started = asyncio.Event(), asyncio.Event()
    release_first, release_second = asyncio.Event(), asyncio.Event()
    service = object.__new__(LLMService)
    service._pending_agent_hud = []
    service.current_parent_uuid = "parent-before"
    service._turn_serial = 1
    service._cursor_winner_serial = -1
    service._continue_serial = None

    class GatedQueueProcessor:
        async def process_message_batch(
            self, *, messages, current_parent_uuid, request_ids
        ):
            serial = messages[0].admission_serial
            assert service._continue_serial == serial
            started, release = (
                (first_started, release_first)
                if serial == 1
                else (second_started, release_second)
            )
            started.set()
            await release.wait()
            return f"assistant-{serial}"

    service._queue_processor = GatedQueueProcessor()
    first = TaggedInput(
        content="first",
        request_ids=("first",),
        parent_uuid="user-1",
        admission_serial=1,
    )
    second_serial = service.claim_turn_serial()
    second = TaggedInput(
        content="second",
        request_ids=("second",),
        parent_uuid="user-2",
        admission_serial=second_serial,
    )

    first_task = asyncio.create_task(service._process_message_batch([first]))
    try:
        await asyncio.wait_for(first_started.wait(), timeout=1)
        second_task = asyncio.create_task(service._process_message_batch([second]))
        await asyncio.wait_for(second_started.wait(), timeout=1)
        assert service._continue_serial == second_serial

        # The older batch returns first; it must not reinstall ticket 1.
        release_first.set()
        await asyncio.wait_for(first_task, timeout=1)
        assert service._continue_serial == second_serial

        release_second.set()
        await asyncio.wait_for(second_task, timeout=1)
    finally:
        release_first.set()
        release_second.set()
        if not first_task.done():
            await first_task
        if "second_task" in locals() and not second_task.done():
            await second_task


@pytest.mark.asyncio
async def test_concurrent_pointer_advance_between_user_row_and_queue_start_cannot_reparent_turn():
    """A detached completion that advances ``current_parent_uuid`` between an
    ordinary turn's user-row log and its queue start must not become that
    turn's parent. The turn threads the parent captured at admission (its own
    user row), never the mutable pointer the batch reads at start."""
    bus = EventBus()
    service = object.__new__(LLMService)
    service.event_bus = bus
    service.profile_manager = SimpleNamespace(
        get_active_profile=lambda: SimpleNamespace(get_provider=lambda: "native")
    )
    service._pending_agent_hud = []
    service.current_parent_uuid = "prev-turn"
    enqueued = asyncio.Event()
    gate = asyncio.Event()
    captured_items: list = []

    async def gated_enqueue(item):
        captured_items.append(item)
        enqueued.set()
        await gate.wait()  # the ordinary turn sits between log and queue start
        return True

    batch_spy = AsyncMock(return_value="assistant-row")
    service._queue_processor = SimpleNamespace(
        enqueue=gated_enqueue,
        process_message_batch=batch_spy,
        continue_conversation=AsyncMock(return_value="parent-3"),
        turn_completed=False,
        processing_queue=SimpleNamespace(full=MagicMock(return_value=False)),
    )
    service.is_processing = True  # property: backed by _queue_processor
    service.cancel_processing = False
    service.cancellation_message_shown = False
    service.create_background_task = MagicMock()
    service.message_display_service = SimpleNamespace(
        display_user_message=MagicMock(),
        display_error_message=MagicMock(),
    )
    service.conversation_logger = SimpleNamespace(
        log_user_message=AsyncMock(return_value="user-row")
    )
    service._message_handler = SimpleNamespace(
        handle_user_input=AsyncMock(return_value={"status": "native"})
    )
    service._enqueue_with_overflow_strategy = gated_enqueue
    assert await bus.register_hook(
        Hook(
            name="process_user_input",
            plugin_name="llm_core",
            event_type=EventType.USER_INPUT,
            priority=HookPriority.LLM.value,
            callback=service._handle_user_input,
        )
    )

    task = asyncio.create_task(service.process_user_input("hello"))
    await asyncio.wait_for(enqueued.wait(), 1)
    # Admission logged the user row and the pointer moved onto it.
    assert service.current_parent_uuid == "user-row"
    assert len(captured_items) == 1

    # The race window: a detached mention completion advances the pointer.
    service.current_parent_uuid = "hub-assistant"

    gate.set()
    result = await asyncio.wait_for(task, 1)
    assert result["status"] == "queued"

    # The drain now starts the batch (the queue-start pointer read).
    await service._process_message_batch(captured_items[:1])

    batch_spy.assert_awaited_once()
    batch_kwargs = batch_spy.await_args.kwargs
    assert batch_kwargs["current_parent_uuid"] == "user-row", (
        "ordinary turn must parent from its admission-time user row, "
        f"not the pointer a concurrent completion advanced "
        f"({batch_kwargs['current_parent_uuid']})"
    )


@pytest.mark.asyncio
async def test_pending_hud_rebatch_of_typed_turn_keeps_admission_parent():
    """A pending-HUD rebatch of an ordinary typed turn must carry the
    admission-time capture through the combined payload. The non-voice branch
    collapses the batch into a bare string with no capture, so without the
    fix the queue-start read falls back to the mutable pointer -- which a
    detached completion may have advanced while this turn sat queued (#676
    round 5)."""
    bus = EventBus()
    service = object.__new__(LLMService)
    service.event_bus = bus
    service.profile_manager = SimpleNamespace(
        get_active_profile=lambda: SimpleNamespace(get_provider=lambda: "native")
    )
    service._pending_agent_hud = [  # truthy: exercises the rebatch branch
        AgentHudEntry("system", "hub_nudge", "[nudge] save notes")
    ]
    service.current_parent_uuid = "hub-assistant"  # already advanced by a detached completion
    batch_spy = AsyncMock(return_value="assistant-row")
    service._queue_processor = SimpleNamespace(
        enqueue=AsyncMock(),
        process_message_batch=batch_spy,
        continue_conversation=AsyncMock(return_value="parent-3"),
        turn_completed=False,
        processing_queue=SimpleNamespace(full=MagicMock(return_value=False)),
    )

    typed = TaggedInput(
        content="hello",
        request_ids=("req-1",),
        parent_uuid="user-row",  # frozen at admission (#676)
    )
    await service._process_message_batch([typed])

    batch_spy.assert_awaited_once()
    batch_kwargs = batch_spy.await_args.kwargs
    assert batch_kwargs["current_parent_uuid"] == "user-row", (
        "pending-HUD rebatch of a typed turn must parent from the "
        f"admission-time capture, not the pointer a detached completion "
        f"advanced ({batch_kwargs['current_parent_uuid']})"
    )


def test_stale_untracked_nudge_cannot_overwrite_newer_hub_cursor() -> None:
    async def scenario() -> None:
        nudge_started = asyncio.Event()
        release_nudge = asyncio.Event()
        nudge = QueuedInput(
            content="finish the task",
            voice={},
            is_system_nudge=True,
        )

        class GatedQueueProcessor:
            turn_completed = False

            async def process_message_batch(
                self, *, messages, current_parent_uuid, request_ids
            ) -> str:
                assert messages == [nudge]
                assert nudge.admission_serial is None
                assert current_parent_uuid == "parent-before-nudge"
                nudge_started.set()
                await release_nudge.wait()
                return "stale-nudge-assistant"

            async def continue_conversation(self, *, current_parent_uuid) -> str:
                return "stale-nudge-continuation"

        # Use the real LLMService batch/cursor methods. Only the queue's model
        # execution is gated so the completion order is deterministic.
        service = object.__new__(LLMService)
        service._queue_processor = GatedQueueProcessor()
        service._pending_agent_hud = []
        service.current_parent_uuid = "parent-before-nudge"
        service._turn_serial = 0
        service._cursor_winner_serial = -1

        nudge_task = asyncio.create_task(service._process_message_batch([nudge]))
        hub_wake_serial = None
        try:
            await asyncio.wait_for(nudge_started.wait(), timeout=2)
            # A hub wake admits while the nudge batch (ticket claimed at
            # batch start, #676) is still waiting on the model response.
            hub_wake_serial = service.claim_turn_serial()
            assert service.publish_cursor("newer-hub-wake", hub_wake_serial)
        finally:
            release_nudge.set()
            await nudge_task

        # The nudge batch claimed ticket 1 at batch start (#676); the hub
        # wake admitted later and holds ticket 2.
        assert hub_wake_serial == 2
        assert service._continue_serial == 1
        assert service._cursor_winner_serial == hub_wake_serial
        assert service.current_parent_uuid == "newer-hub-wake"

        # The nudge's follow-on tool-loop write carries the nudge's claimed
        # ticket (1), older than the hub wake's -- rejected.
        await service._continue_conversation()
        assert service._cursor_winner_serial == hub_wake_serial
        assert service.current_parent_uuid == "newer-hub-wake"
        assert service.publish_cursor("untracked-write", None) is False
        assert service.current_parent_uuid == "newer-hub-wake"

    asyncio.run(scenario())

@pytest.mark.asyncio
async def test_untracked_nudge_batch_claims_a_ticket_and_advances_the_cursor():
    """A batch with no admission ticket (system nudge, bare payload) claims
    one at batch start (#676). The claim is strictly later than everything
    already admitted -- the batch's true execution order -- so its
    completion advances the shared cursor instead of stranding it before
    the nudge rows."""
    service = object.__new__(LLMService)
    service._queue_processor = SimpleNamespace(
        process_message_batch=AsyncMock(return_value="nudge-assistant"),
        continue_conversation=AsyncMock(return_value="nudge-continuation"),
        turn_completed=False,
    )
    service._pending_agent_hud = []
    service.current_parent_uuid = "parent-before"
    service._turn_serial = 0
    service._cursor_winner_serial = -1
    service._continue_serial = None

    nudge = QueuedInput(content="finish it", voice={}, is_system_nudge=True)
    await service._process_message_batch([nudge])

    assert service._turn_serial == 1, "untracked batch must claim at start"
    assert service._continue_serial == 1
    assert service.current_parent_uuid == "nudge-assistant", (
        "the nudge batch's completion must advance the shared cursor, "
        f"got {service.current_parent_uuid!r}"
    )
    assert service._cursor_winner_serial == 1


@pytest.mark.asyncio
async def test_continuation_parents_on_chain_tail_not_a_drifted_pointer():
    """A tool-loop continuation follows its own chain's last row, not the
    shared pointer (#676): a later-admitted mention that completed mid-batch
    keeps the cursor while the batch's chain stays contiguous."""
    calls: list[tuple[str, str]] = []

    class RecordingQueueProcessor:
        turn_completed = False

        async def process_message_batch(
            self, *, messages, current_parent_uuid, request_ids
        ) -> str:
            calls.append(("batch", current_parent_uuid))
            return "batch-tail"

        async def continue_conversation(self, *, current_parent_uuid) -> str:
            calls.append(("cont", current_parent_uuid))
            return "cont-row"

    service = object.__new__(LLMService)
    service._queue_processor = RecordingQueueProcessor()
    service._pending_agent_hud = []
    service.current_parent_uuid = "parent-before"
    # Ticket 1 was already handed out at the batch's admission; the mention
    # below claims the strictly later ticket 2.
    service._turn_serial = 1
    service._cursor_winner_serial = -1
    service._continue_serial = None

    tracked = TaggedInput(
        content="hello",
        request_ids=("req-1",),
        parent_uuid="user-row",
        admission_serial=1,
    )
    await service._process_message_batch([tracked])
    assert service.current_parent_uuid == "batch-tail"

    # Mid-run drift: a later-admitted mention completes while LOOP 2 runs.
    mention_serial = service.claim_turn_serial()
    assert service.publish_cursor("mention-reply", mention_serial)

    await service._continue_conversation()
    assert calls[-1] == ("cont", "batch-tail"), (
        f"continuation must parent on the chain tail, got {calls[-1]}"
    )
    # The mention keeps the cursor (strictly later admission wins).
    assert service.current_parent_uuid == "mention-reply"


@pytest.mark.asyncio
async def test_delayed_continuation_cannot_clobber_newer_batch_tail_or_cursor():
    """A stale tool-loop continuation commits only under its own serial (#768).

    Bismuth's overlap probe on the #768 head: ``_continue_conversation``
    wrote its continuation uuid into ``_batch_tail_uuid`` and published on
    the re-read ``_continue_serial`` AFTER its await. A newer batch that
    admitted while the old continuation was suspended had its tail
    overwritten and its serial hijacked by the stale chain.
    """
    release_cont = asyncio.Event()
    cont_started = asyncio.Event()
    service = object.__new__(LLMService)
    service._pending_agent_hud = []
    service.current_parent_uuid = "parent-before"
    service._turn_serial = 1
    service._cursor_winner_serial = -1
    service._continue_serial = 1
    service._batch_tail_uuid = "batch1-tail"
    service._batch_tail_serial = 1

    class GatedQueueProcessor:
        async def continue_conversation(self, *, current_parent_uuid):
            cont_started.set()
            await release_cont.wait()
            return "stale-cont-1"

        async def process_message_batch(
            self, *, messages, current_parent_uuid, request_ids
        ):
            return "assistant-2"

    service._queue_processor = GatedQueueProcessor()

    cont_task = asyncio.create_task(service._continue_conversation())
    try:
        await asyncio.wait_for(cont_started.wait(), timeout=1)

        # A newer batch admits and completes while the old continuation is
        # suspended: serial 2 owns the tail and the cursor.
        second = TaggedInput(
            content="second",
            request_ids=("second",),
            parent_uuid="user-2",
            admission_serial=2,
        )
        await asyncio.wait_for(
            service._process_message_batch([second]), timeout=1
        )
        assert service._batch_tail_uuid == "assistant-2"
        assert service._batch_tail_serial == 2
        assert service._cursor_winner_serial == 2

        # The stale continuation resumes now: it must not overwrite the
        # newer tail, and its publish on the captured serial 1 must lose.
        release_cont.set()
        await asyncio.wait_for(cont_task, timeout=1)

        assert service._batch_tail_uuid == "assistant-2", (
            "stale continuation overwrote the newer batch tail"
        )
        assert service._batch_tail_serial == 2
        assert service.current_parent_uuid == "assistant-2", (
            "stale continuation published on the newer serial and won the cursor"
        )
        assert service._cursor_winner_serial == 2
    finally:
        release_cont.set()
        if not cont_task.done():
            await cont_task
