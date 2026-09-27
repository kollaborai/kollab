"""Coverage for human input producers that bypass the terminal composer."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor.application import TerminalLLMChat
from kollabor.llm.llm_coordinator import LLMService
from kollabor.state import LocalStateService
from kollabor.user_input_source import UserInputSource
from kollabor_events import EventBus, EventType, Hook, HookPriority


async def _pipeline(*, cancel_pre: bool = False):
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
        return {"status": "queued"}

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

    async def submit_human_input(self, message, *, source, pre_displayed=False):
        return await self._service.submit_human_input(message, source=source, pre_displayed=pre_displayed)

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

    await app.start_pipe_mode("do not process")

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

    result = await llm.submit_human_input("cancel before model", source=UserInputSource.STATE_RPC)

    assert result == {"status": "cancelled", "phase": "pre_user_input"}
    assert calls == [("pre", "state_rpc", "cancel before model")]
    pending_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_internal_caller_cannot_select_a_human_source_string():
    _, llm, calls, _ = await _pipeline()

    result = await llm.submit_human_input("forged", source="user")  # type: ignore[arg-type]

    assert result == {"status": "rejected", "reason": "invalid_input_source"}
    assert calls == []
