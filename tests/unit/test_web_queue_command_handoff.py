"""Queued WebUI inputs retain the state service's command semantics."""

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
    state._try_goal_command = Mock(return_value={"accepted": True, "reason": "goal command"})
    context = QueueHandoffContext(
        request_id="web-goal-1", conversation_uid="conversation-a",
        conversation_revision=1, images=(), attempt_id="attempt-a",
    )

    await state.submit_queue_message("/goal show", queue_handoff=context)

    state._try_goal_command.assert_called_once()
    submit.assert_not_awaited()
