"""Real ACP queue admission keeps history faults out of custody receipts."""

from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from kollabor.llm.acp_bridge import ACPProfile, ACPResult, ACPRunner
from kollabor.llm.acp_orchestrator import ACPOrchestrator
from kollabor.state.web_message_attachments import (
    AttachmentScopeBinding,
    WebMessageAttachmentRegistry,
)
from kollabor.state.web_message_queue import WebMessage, WebMessageQueue
from kollabor.state.web_message_queue_dispatch import WebMessageQueueDispatcher
from kollabor.state.web_message_queue_worker import (
    ClaimOutcome,
    ConversationIdentity,
    WebMessageQueueWorker,
    WorkerStatus,
)


class _Connection:
    async def cancel(self, *, session_id):
        return None

    async def close(self):
        return None

    async def prompt(self, *, session_id, prompt):
        return SimpleNamespace(stopReason="end_turn", usage=None)


async def _real_acp_state(tmp_path, *, start_completions=False):
    from kollabor.llm.llm_coordinator import LLMService
    from kollabor.state import LocalStateService
    from kollabor_ai.conversation_logger import KollaborConversationLogger
    from kollabor_events import EventBus

    config = MagicMock()
    config.get.side_effect = lambda _key, default=None: default
    event_bus = EventBus()
    llm = LLMService(config, event_bus, MagicMock())
    event_bus.register_service("llm_service", llm)
    llm.api_service.supports_image_input = lambda: True
    logger = KollaborConversationLogger(tmp_path / "conversations")
    logger.reset_session(llm.conversation_logger.session_id)
    llm.conversation_logger = logger
    llm.config._conversations_dir = logger.conversations_dir
    llm.conversation_manager.current_session_id = logger.session_id
    await llm.register_hooks()
    state = LocalStateService(
        llm_service=llm,
        profile_manager=MagicMock(),
        event_bus=event_bus,
    )

    profile = MagicMock()
    profile.get_provider.return_value = "acp"
    profile_manager = MagicMock()
    profile_manager.get_active_profile.return_value = profile
    llm.profile_manager = profile_manager

    runner = ACPRunner(ACPProfile("queue-test", (sys.executable,)))
    runner.session_id = "session-acp"
    runner.generation = 3
    runner._connection = _Connection()

    async def run_prompt(admission, generation):
        return ACPResult(
            generation=generation,
            session_id="session-acp",
            text=admission.request_id,
            stop_reason="end_turn",
            updates=(),
            end_frame={"stopReason": "end_turn"},
            turn_id=admission.turn_id,
            request_id=admission.request_id,
            source=admission.source,
            thread_id=admission.thread_id,
            history_key=admission.history_key,
        )

    runner._run_prompt = run_prompt
    acp = ACPOrchestrator(event_bus)
    acp._runner = runner
    if not start_completions:
        acp._start_completion = lambda _admission: True
    else:
        runner.update_callback = acp._on_runner_update
    event_bus.register_service("acp_orchestrator", acp)
    return state, llm, logger, acp, runner


@pytest.mark.asyncio
async def test_acp_history_append_failure_rolls_back_only_unsent_suffix(
    tmp_path, monkeypatch
):
    from kollabor_ai import conversation_logger

    queue = WebMessageQueue(scope_id="conversation-scope")
    batch_id = queue.new_batch_id()
    request_ids = (queue.new_request_id(), queue.new_request_id())
    await queue.enqueue_batch(
        queue.new_operation_id(),
        batch_id,
        tuple(
            WebMessage(request_id, json.dumps({"text": text}).encode())
            for request_id, text in zip(request_ids, ("accepted first", "failed second"))
        ),
    )
    attachments = WebMessageAttachmentRegistry(
        resolve_scope=lambda _params, _caller: AttachmentScopeBinding(
            queue.scope_id, queue.incarnation
        ),
        validate_owner_id=lambda _binding, _request_id: True,
    )
    state, llm, logger, acp, runner = await _real_acp_state(tmp_path)
    identity = ConversationIdentity(
        llm._current_conversation_uid(), llm.get_conversation_revision()
    )
    original_write = conversation_logger.os.write
    write_calls = 0

    def fail_second_append(fd, data):
        nonlocal write_calls
        write_calls += 1
        if write_calls == 2:
            raise OSError("injected JSONL append failure")
        return original_write(fd, data)

    monkeypatch.setattr(conversation_logger.os, "write", fail_second_append)
    worker = WebMessageQueueWorker(
        queue,
        identity,
        state.get_conversation_identity,
        WebMessageQueueDispatcher(state, attachments),
    )

    try:
        result = await worker.dispatch_batch(batch_id, send_now=True)

        assert result.status is WorkerStatus.NOT_SENT
        assert [item.request_id for item in result.items] == list(request_ids)
        assert [item.outcome for item in result.items] == [
            ClaimOutcome.ACCEPTED,
            ClaimOutcome.NOT_SENT,
        ]
        assert result.items[0].recording_status.value == "recorded"
        assert result.items[1].custody_id is None
        assert [item.request_id for item in runner._queue._queue] == [request_ids[0]]
        assert runner._queue_handoff_batch is None
        assert runner._queue_release.is_set()

        rows = [
            json.loads(line)
            for line in logger.session_file.read_text().splitlines()
            if json.loads(line).get("type") == "user"
        ]
        assert [
            row["message"]["metadata"]["queue_handoff"]["request_id"]
            for row in rows
        ] == [request_ids[0]]
        metadata = rows[0]["message"]["metadata"]
        assert isinstance(metadata["turn_id"], int)
        assert metadata["acp_turn_key"]
        assert metadata["queue_handoff"]["force_send"] is True
        snapshot = await queue.snapshot()
        assert snapshot.batches[0].request_ids == (request_ids[1],)
    finally:
        await acp.close()


@pytest.mark.asyncio
async def test_acp_forced_batch_history_and_events_follow_execution_identity(
    tmp_path,
):
    from kollabor_tui.display_tap import DisplayTap

    queue = WebMessageQueue(scope_id="conversation-scope")
    batch_id = queue.new_batch_id()
    forced_ids = (queue.new_request_id(), queue.new_request_id())
    await queue.enqueue_batch(
        queue.new_operation_id(),
        batch_id,
        tuple(
            WebMessage(request_id, json.dumps({"text": text}).encode())
            for request_id, text in zip(forced_ids, ("forced B", "forced C"))
        ),
    )
    attachments = WebMessageAttachmentRegistry(
        resolve_scope=lambda _params, _caller: AttachmentScopeBinding(
            queue.scope_id, queue.incarnation
        ),
        validate_owner_id=lambda _binding, _request_id: True,
    )
    state, llm, logger, acp, runner = await _real_acp_state(
        tmp_path, start_completions=True
    )
    tap = DisplayTap()
    events = []
    tap.add_listener(events.append)
    llm.event_bus.register_service("display_tap", tap)
    started = []

    async def run_prompt(admission, generation):
        started.append(admission.request_id)
        start_update = {
            "kind": "turn_start",
            "request_id": admission.request_id,
            "turn_id": admission.turn_id,
            "source": admission.source,
            "thread_id": admission.thread_id,
            "generation": generation,
            "acp_turn_key": admission.history_key,
        }
        persist = runner.update_callback(start_update)
        if hasattr(persist, "__await__"):
            await persist
        dispatch_epoch = str(start_update.get("dispatch_epoch") or "")
        dispatch_order = int(start_update.get("dispatch_order") or 0)
        return ACPResult(
            generation=generation,
            session_id="session-acp",
            text=f"reply {admission.request_id}",
            stop_reason="end_turn",
            updates=(),
            end_frame={
                "stopReason": "end_turn",
                "dispatchEpoch": dispatch_epoch,
                "dispatchOrder": dispatch_order,
            },
            turn_id=admission.turn_id,
            request_id=admission.request_id,
            source=admission.source,
            thread_id=admission.thread_id,
            history_key=admission.history_key,
            dispatch_epoch=dispatch_epoch,
            dispatch_order=dispatch_order,
        )

    runner._run_prompt = run_prompt
    older = acp.admit("older E", request_id="older-E", source="web", record=True)
    await asyncio.gather(*tuple(acp._history_tasks))
    identity = ConversationIdentity(
        llm._current_conversation_uid(), llm.get_conversation_revision()
    )
    worker = WebMessageQueueWorker(
        queue,
        identity,
        state.get_conversation_identity,
        WebMessageQueueDispatcher(state, attachments),
    )
    drain = None
    try:
        result = await worker.dispatch_batch(batch_id, send_now=True)
        assert result.status is WorkerStatus.ADMITTED
        pending = list(runner._queue._queue)
        assert [item.request_id for item in pending] == [
            *forced_ids,
            older.request_id,
        ]

        drain = asyncio.create_task(runner._drain_queue())
        results = await asyncio.wait_for(
            asyncio.gather(*(item.future for item in pending)), timeout=2
        )
        await asyncio.gather(*tuple(acp._completion_tasks))

        assert started == [*forced_ids, older.request_id]
        assert [result.request_id for result in results] == [
            *forced_ids,
            older.request_id,
        ]
        starts = [event for event in events if event.get("type") == "turn_start"]
        completes = [event for event in events if event.get("type") == "turn_complete"]
        assert [event.get("request_id") for event in starts] == [
            *forced_ids,
            older.request_id,
        ]
        assert [event.get("dispatch_order") for event in starts] == [1, 2, 3]
        assert [event.get("request_id") for event in completes] == [
            *forced_ids,
            older.request_id,
        ]
        assert [event.get("turn_id") for event in starts] == [
            item.turn_id for item in pending
        ]

        records = [json.loads(line) for line in logger.session_file.read_text().splitlines()]
        users = [
            row for row in records
            if row.get("type") == "user"
            and (row.get("message") or {}).get("metadata", {}).get("provider") == "acp"
        ]
        assistants = [
            row for row in records
            if row.get("type") == "assistant"
            and row.get("display_metadata", {}).get("provider") == "acp"
        ]
        assert [row["message"]["metadata"]["request_id"] for row in users] == [
            older.request_id,
            *forced_ids,
        ]
        assert [row["display_metadata"]["request_id"] for row in assistants] == [
            *forced_ids,
            older.request_id,
        ]
        user_keys = {
            row["message"]["metadata"]["request_id"]:
            row["message"]["metadata"]["acp_turn_key"]
            for row in users
        }
        assistant_keys = {
            row["display_metadata"]["request_id"]:
            row["display_metadata"]["acp_turn_key"]
            for row in assistants
        }
        assert user_keys == assistant_keys
        assert len(set(user_keys.values())) == 3
        user_dispatch = {
            row["message"]["metadata"]["request_id"]:
            row["message"]["metadata"]["dispatch_order"]
            for row in users
        }
        assistant_dispatch = {
            row["display_metadata"]["request_id"]:
            row["display_metadata"]["dispatch_order"]
            for row in assistants
        }
        assert user_dispatch == dict(zip((*forced_ids, older.request_id), (1, 2, 3)))
        assert assistant_dispatch == user_dispatch
    finally:
        if drain is not None:
            runner._closing = True
            runner._queue_release.set()
            runner._queue_changed.set()
            drain.cancel()
            await asyncio.gather(drain, return_exceptions=True)
        await acp.close()


@pytest.mark.asyncio
async def test_dispatch_order_is_persisted_before_the_acp_prompt_starts(tmp_path):
    from kollabor_tui.display_tap import DisplayTap

    _state, _llm, logger, acp, runner = await _real_acp_state(
        tmp_path, start_completions=True
    )
    tap = DisplayTap()
    events = []
    tap.add_listener(events.append)
    acp.event_bus.register_service("display_tap", tap)

    admission = acp.admit("ordered request", request_id="ordered-1", source="web")
    try:
        result = await ACPRunner._run_prompt(runner, admission, admission.generation)
        assert result.dispatch_epoch
        assert result.dispatch_order == 1
        assert result.end_frame["dispatchEpoch"] == result.dispatch_epoch
        assert result.end_frame["dispatchOrder"] == 1

        user_rows = [
            row for row in map(json.loads, logger.session_file.read_text().splitlines())
            if row.get("type") == "user"
            and row.get("message", {}).get("metadata", {}).get("request_id")
            == admission.request_id
        ]
        assert len(user_rows) == 1
        user_metadata = user_rows[0]["message"]["metadata"]
        assert user_metadata["acp_turn_key"] == admission.history_key
        assert user_metadata["dispatch_epoch"] == result.dispatch_epoch
        assert user_metadata["dispatch_order"] == 1

        admission.future.set_result(result)
        await asyncio.gather(*tuple(acp._completion_tasks))
        assistant_rows = [
            row for row in map(json.loads, logger.session_file.read_text().splitlines())
            if row.get("type") == "assistant"
            and row.get("display_metadata", {}).get("request_id") == admission.request_id
        ]
        assert len(assistant_rows) == 1
        assistant_metadata = assistant_rows[0]["display_metadata"]
        assert assistant_metadata["dispatch_epoch"] == result.dispatch_epoch
        assert assistant_metadata["dispatch_order"] == 1

        start = next(event for event in events if event.get("type") == "turn_start")
        complete = next(event for event in events if event.get("type") == "turn_complete")
        assert (start["dispatch_epoch"], start["dispatch_order"]) == (
            result.dispatch_epoch,
            1,
        )
        assert (complete["dispatch_epoch"], complete["dispatch_order"]) == (
            result.dispatch_epoch,
            1,
        )
    finally:
        if not admission.future.done():
            admission.future.cancel()
        await acp.close()


@pytest.mark.asyncio
async def test_acp_send_now_waits_for_cancelled_partial_history(tmp_path):
    queue = WebMessageQueue(scope_id="conversation-scope")
    batch_id = queue.new_batch_id()
    send_now_id = queue.new_request_id()
    await queue.enqueue_batch(
        queue.new_operation_id(),
        batch_id,
        (WebMessage(send_now_id, json.dumps({"text": "next input"}).encode()),),
    )
    attachments = WebMessageAttachmentRegistry(
        resolve_scope=lambda _params, _caller: AttachmentScopeBinding(
            queue.scope_id, queue.incarnation
        ),
        validate_owner_id=lambda _binding, _request_id: True,
    )
    state, llm, logger, acp, runner = await _real_acp_state(
        tmp_path, start_completions=True
    )

    cut_started = asyncio.Event()
    cancel_received = asyncio.Event()
    completion_waiting = asyncio.Event()
    release_completion = asyncio.Event()
    hub_completion_started = asyncio.Event()
    release_hub_completion = asyncio.Event()
    send_now_user_written = asyncio.Event()
    send_now_manager_recorded = asyncio.Event()

    async def hold_hub_completion(**_kwargs):
        hub_completion_started.set()
        await release_hub_completion.wait()

    acp.hub_plugin = SimpleNamespace(
        on_acp_completion=hold_hub_completion, _identity=None
    )

    async def cancel(*, session_id):
        cancel_received.set()

    runner._connection.cancel = cancel

    async def run_prompt(admission, generation):
        async def update(kind, **values):
            result = runner.update_callback(
                {
                    "kind": kind,
                    "request_id": admission.request_id,
                    "turn_id": admission.turn_id,
                    "source": admission.source,
                    "thread_id": admission.thread_id,
                    "generation": generation,
                    "acp_turn_key": admission.history_key,
                    **values,
                }
            )
            if hasattr(result, "__await__"):
                await result

        await update("turn_start")
        if admission.request_id == "cut-turn":
            cut_started.set()
            await cancel_received.wait()
            await update("text", text="partial reply")
            await update(
                "tool_call",
                payload={
                    "toolCallId": "cut-tool",
                    "title": "Read file",
                    "kind": "read",
                    "rawInput": {"path": "notes.txt"},
                    "status": "completed",
                    "rawOutput": "partial tool output",
                },
            )
            await update("turn_finished")
            stop_reason = "cancelled"
            text = "partial reply"
        else:
            await update("turn_finished")
            stop_reason = "end_turn"
            text = "next reply"
        return ACPResult(
            generation=generation,
            session_id="session-acp",
            text=text,
            stop_reason=stop_reason,
            updates=(),
            end_frame={"stopReason": stop_reason},
            turn_id=admission.turn_id,
            request_id=admission.request_id,
            source=admission.source,
            thread_id=admission.thread_id,
            history_key=admission.history_key,
        )

    runner._run_prompt = run_prompt
    cut = acp.admit("original input", request_id="cut-turn", source="web")
    await asyncio.gather(*tuple(acp._history_tasks))

    original_await_user_history = acp._await_user_history

    async def pause_after_user_history(turn_key):
        await original_await_user_history(turn_key)
        if turn_key == (cut.generation, cut.turn_id):
            completion_waiting.set()
            await release_completion.wait()

    acp._await_user_history = pause_after_user_history
    original_log_user_message = logger.log_user_message

    async def record_send_now_user(text, **kwargs):
        result = await original_log_user_message(text, **kwargs)
        metadata = kwargs.get("message_metadata") or {}
        if metadata.get("request_id") == send_now_id:
            send_now_user_written.set()
        return result

    logger.log_user_message = record_send_now_user
    original_add_message = llm.conversation_manager.add_message

    def record_send_now_manager_message(role, text, **kwargs):
        result = original_add_message(role, text, **kwargs)
        metadata = kwargs.get("metadata") or {}
        if role == "user" and metadata.get("request_id") == send_now_id:
            send_now_manager_recorded.set()
        return result

    llm.conversation_manager.add_message = record_send_now_manager_message
    identity = ConversationIdentity(
        llm._current_conversation_uid(), llm.get_conversation_revision()
    )
    worker = WebMessageQueueWorker(
        queue,
        identity,
        state.get_conversation_identity,
        WebMessageQueueDispatcher(state, attachments),
    )
    drain = asyncio.create_task(runner._drain_queue())
    dispatch = None
    try:
        await asyncio.wait_for(cut_started.wait(), timeout=1)
        dispatch = asyncio.create_task(worker.dispatch_batch(batch_id, send_now=True))
        await asyncio.wait_for(completion_waiting.wait(), timeout=1)

        for event in (send_now_user_written, send_now_manager_recorded):
            try:
                await asyncio.wait_for(event.wait(), timeout=0.2)
            except asyncio.TimeoutError:
                pass
        manager_rows_while_paused = llm.conversation_manager.messages
        manager_row_while_paused = any(
            message.get("role") == "user"
            and (message.get("metadata") or {}).get("request_id") == send_now_id
            for message in manager_rows_while_paused
        )
        assert not (
            send_now_user_written.is_set() or manager_row_while_paused
        ), (
            "Send Now reached history before the cancelled partial assistant row: "
            f"jsonl={send_now_user_written.is_set()}, "
            f"manager={manager_row_while_paused}"
        )

        release_completion.set()
        result = await asyncio.wait_for(dispatch, timeout=2)
        assert result.status is WorkerStatus.ADMITTED
        await asyncio.wait_for(hub_completion_started.wait(), timeout=1)
        assert not release_hub_completion.is_set()
        release_hub_completion.set()
        await asyncio.wait_for(asyncio.gather(*tuple(acp._completion_tasks)), timeout=2)

        records = [json.loads(line) for line in logger.session_file.read_text().splitlines()]
        cut_assistant_index = next(
            index
            for index, row in enumerate(records)
            if row.get("type") == "assistant"
            and row.get("display_metadata", {}).get("request_id") == "cut-turn"
        )
        send_now_user_index = next(
            index
            for index, row in enumerate(records)
            if row.get("type") == "user"
            and row.get("message", {}).get("metadata", {}).get("request_id")
            == send_now_id
        )
        assert cut_assistant_index < send_now_user_index
        assert any(
            part.get("type") == "tool_use" and part.get("id") == "cut-tool"
            for part in records[cut_assistant_index]["message"]["content"]
        )

        messages = llm.conversation_manager.messages
        cut_assistant_position = next(
            index
            for index, message in enumerate(messages)
            if message.get("role") == "assistant"
            and (message.get("metadata") or {}).get("request_id") == "cut-turn"
        )
        send_now_user_position = next(
            index
            for index, message in enumerate(messages)
            if message.get("role") == "user"
            and (message.get("metadata") or {}).get("request_id") == send_now_id
        )
        assert cut_assistant_position < send_now_user_position
    finally:
        release_completion.set()
        release_hub_completion.set()
        if dispatch is not None:
            await asyncio.gather(dispatch, return_exceptions=True)
        runner._closing = True
        runner._queue_release.set()
        runner._queue_changed.set()
        drain.cancel()
        await asyncio.gather(drain, return_exceptions=True)
        await acp.close()


@pytest.mark.asyncio
async def test_acp_turn_start_waits_for_durable_dispatch_identity(tmp_path):
    from kollabor_tui.display_tap import DisplayTap

    _state, _llm, logger, acp, runner = await _real_acp_state(tmp_path)
    runner.update_callback = acp._on_runner_update
    tap = DisplayTap()
    events = []
    tap.add_listener(events.append)
    acp.event_bus.register_service("display_tap", tap)
    admission = acp.admit("ordered request", request_id="ordered-2", source="web")
    await asyncio.gather(*tuple(acp._history_tasks))

    original_update = logger.update_user_message_metadata
    persistence_started = asyncio.Event()
    allow_persistence = asyncio.Event()
    prompt_started = asyncio.Event()

    async def delayed_update(*args, **kwargs):
        persistence_started.set()
        await allow_persistence.wait()
        return await original_update(*args, **kwargs)

    async def prompt(*, session_id, prompt):
        prompt_started.set()
        return SimpleNamespace(stopReason="end_turn", usage=None)

    logger.update_user_message_metadata = delayed_update
    runner._connection.prompt = prompt
    try:
        task = asyncio.create_task(
            ACPRunner._run_prompt(runner, admission, admission.generation)
        )
        await asyncio.wait_for(persistence_started.wait(), timeout=1)
        assert not prompt_started.is_set()
        assert not any(event.get("type") == "turn_start" for event in events)

        allow_persistence.set()
        result = await asyncio.wait_for(task, timeout=1)
        assert result.dispatch_order == 1
        assert prompt_started.is_set()
        assert any(event.get("type") == "turn_start" for event in events)
    finally:
        allow_persistence.set()
        if not admission.future.done():
            admission.future.cancel()
        await acp.close()
