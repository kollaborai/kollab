"""Queued WebUI inputs retain the state service's command semantics."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from kollabor.llm.queue_handoff import QueueHandoffContext
from kollabor.state.local import LocalStateService


@pytest.mark.asyncio
async def test_queued_goal_does_not_enter_provider_admission():
    state = object.__new__(LocalStateService)
    submit = AsyncMock(return_value={"status": "queued"})
    state._llm_service = SimpleNamespace(submit_queued_human_input=submit)
    task = asyncio.create_task(asyncio.sleep(0, result={"success": True}))
    state._try_goal_command = Mock(
        return_value={"accepted": True, "_private_task": task}
    )
    state.get_conversation_uid = Mock(return_value="conversation-a")
    state.chat_stream = SimpleNamespace(
        begin_ordered_command=Mock(return_value="turn-1"),
        finish_ordered_command=Mock(),
    )
    context = QueueHandoffContext(
        request_id="web-goal-1", conversation_uid="conversation-a",
        conversation_revision=1, images=(), attempt_id="attempt-a",
        queue_scope_id="scope-a", goal_reply_id="goal-" + "a" * 32,
    )

    result = await state.submit_queue_message("/goal show", queue_handoff=context)

    state._try_goal_command.assert_called_once_with(
        "/goal show",
        "/goal show",
        client_request_id="web-goal-1",
        private_reply=True,
        reply_id=context.goal_reply_id,
        expected_uid="conversation-a",
        return_task=True,
    )
    submit.assert_not_awaited()
    assert result == {"status": "accepted", "request_id": "web-goal-1"}
    assert context.provider_custody_id == "ordered-command-web-goal-1"
    state.chat_stream.finish_ordered_command.assert_called_once_with(
        "web-goal-1", "conversation-a", "turn-1", "completed"
    )


@pytest.mark.asyncio
async def test_queued_slash_command_uses_existing_executor_in_order():
    state = object.__new__(LocalStateService)
    submit = AsyncMock(return_value={"status": "queued"})
    executor = SimpleNamespace()
    parser = SimpleNamespace(is_slash_command=Mock(return_value=True))
    event_bus = SimpleNamespace(
        get_service=lambda name: {
            "slash_parser": parser,
            "command_executor": executor,
        }.get(name)
    )
    state._llm_service = SimpleNamespace(submit_queued_human_input=submit)
    state._event_bus = event_bus
    state.get_conversation_uid = Mock(return_value="conversation-a")
    state.chat_stream = SimpleNamespace(
        begin_ordered_command=Mock(return_value="command-turn-1"),
        finish_ordered_command=Mock(),
    )
    run_command = AsyncMock(return_value={"success": True, "message": "help"})
    state._execute_slash_command = run_command
    context = QueueHandoffContext(
        request_id="web-help-1",
        conversation_uid="conversation-a",
        conversation_revision=1,
        images=(),
        attempt_id="attempt-help",
        queue_scope_id="scope-a",
    )

    result = await state.submit_queue_message("/help", queue_handoff=context)

    parser.is_slash_command.assert_called_once_with("/help")
    run_command.assert_awaited_once_with(
        "/help",
        parser,
        executor,
        request_id="web-help-1",
        turn_id="command-turn-1",
    )
    submit.assert_not_awaited()
    assert result == {"status": "accepted", "request_id": "web-help-1"}
    state.chat_stream.finish_ordered_command.assert_called_once_with(
        "web-help-1", "conversation-a", "command-turn-1", "completed"
    )


@pytest.mark.asyncio
async def test_queued_goal_requires_a_private_reply_capability():
    state = object.__new__(LocalStateService)
    submit = AsyncMock(return_value={"status": "queued"})
    state._llm_service = SimpleNamespace(submit_queued_human_input=submit)
    state._try_goal_command = Mock()
    context = QueueHandoffContext(
        request_id="web-goal-no-cap",
        conversation_uid="conversation-a",
        conversation_revision=1,
        images=(),
        attempt_id="attempt-no-cap",
    )

    result = await state.submit_queue_message("/goal show", queue_handoff=context)

    assert result == {"status": "rejected", "reason": "private_goal_reply_required"}
    state._try_goal_command.assert_not_called()
    submit.assert_not_awaited()
