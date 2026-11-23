"""#1371: Send Now must correlate injection or atomically stop and admit."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kollabor.llm.acp_bridge import ACPProfile, ACPRunner
from kollabor.llm.llm_coordinator import LLMService


def _service() -> LLMService:
    service = object.__new__(LLMService)
    return service


@pytest.mark.asyncio
async def test_injected_send_keeps_client_request_and_joined_turn_owner():
    joined = {
        "joined_request_id": "active-A",
        "joined_turn_id": 12,
        "joined_generation": 7,
    }
    acp = SimpleNamespace(
        supports_image_input=False,
        steer=AsyncMock(return_value=joined),
        admit=AsyncMock(),
    )

    result = await _service()._admit_acp(
        acp,
        {"message": "message B", "request_id": "client-B"},
        "state_rpc",
    )

    assert acp.steer.await_args.kwargs["request_id"] == "client-B"
    assert result == {"status": "injected", "request_id": "client-B", **joined}
    acp.admit.assert_not_awaited()


@pytest.mark.asyncio
async def test_send_now_admits_and_prioritizes_before_fencing_same_turn_cancel():
    calls = []
    admission = SimpleNamespace(
        request_id="client-B",
        turn_id=13,
        future=asyncio.get_running_loop().create_future(),
    )
    owner = SimpleNamespace(request_id="active-A")
    runner = SimpleNamespace(
        _active_context=owner,
        _active_turn_id=12,
        _active_generation=7,
        session_id="session-A",
    )

    class ACP:
        active = True
        supports_image_input = False
        voice_rules_epochs = set()

        def __init__(self):
            self.runner = runner

        async def steer(self, *_args, **_kwargs):
            calls.append("steer")
            return False

        def admit(self, _message, **kwargs):
            calls.append("admit")
            assert kwargs["request_id"] == "client-B"
            return admission

        def prioritize_queued(self, admitted):
            calls.append("prioritize")
            assert admitted is admission
            return True

        def prepare_cancel(self, *, session_id, generation):
            calls.append("prepare_cancel")
            assert (session_id, generation) == ("session-A", 7)
            return True

        def schedule_prepared_cancel(self, *, session_id, generation):
            calls.append("schedule_cancel")
            assert (session_id, generation) == ("session-A", 7)

    result = await _service()._admit_acp(
        ACP(),
        {"message": "message B", "request_id": "client-B", "send_now": True},
        "state_rpc",
    )

    assert result == {"status": "queued", "request_id": "client-B", "turn_id": 13}
    assert calls == ["steer", "admit", "prioritize", "prepare_cancel", "schedule_cancel"]


@pytest.mark.asyncio
async def test_send_now_does_not_cancel_a_replacement_turn_after_steer_refusal():
    calls = []
    original = SimpleNamespace(request_id="active-A")
    runner = SimpleNamespace(
        _active_context=original,
        _active_turn_id=12,
        _active_generation=7,
        session_id="session-A",
    )

    class ACP:
        active = True
        supports_image_input = False
        voice_rules_epochs = set()

        def __init__(self):
            self.runner = runner

        async def steer(self, *_args, **_kwargs):
            runner._active_context = SimpleNamespace(request_id="active-C")
            runner._active_turn_id = 14
            runner._active_generation = 8
            calls.append("steer")
            return False

        def admit(self, *_args, **_kwargs):
            calls.append("admit")
            return SimpleNamespace(request_id="client-B", turn_id=13)

        def prioritize_queued(self, _admission):
            calls.append("prioritize")
            return True

        def prepare_cancel(self, **_kwargs):
            calls.append("prepare_cancel")
            return True

        def schedule_prepared_cancel(self, **_kwargs):
            calls.append("schedule_cancel")

    result = await _service()._admit_acp(
        ACP(),
        {"message": "message B", "request_id": "client-B", "send_now": True},
        "state_rpc",
    )

    assert result == {"status": "rejected", "reason": "turn_changed"}
    assert calls == ["steer"]


@pytest.mark.asyncio
async def test_runner_prioritizes_exact_admission_without_dropping_waiting_turns():
    runner = ACPRunner(ACPProfile("priority-test", ("unused",)))
    first = SimpleNamespace(request_id="older", future=None)
    send_now = SimpleNamespace(request_id="send-now", future=None)
    runner._queue.put_nowait(first)
    runner._queue.put_nowait(send_now)

    assert runner.prioritize_queued(send_now) is True
    assert runner._queue.get_nowait() is send_now
    assert runner._queue.get_nowait() is first
    assert runner._queue.empty()
