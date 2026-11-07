"""#588: a steer whose receipt is lost must never be resent as a queued turn."""

import asyncio
from types import SimpleNamespace

import acp
import pytest

from kollabor.llm.acp_bridge import ACPProfile, ACPRunner
from kollabor.llm.acp_orchestrator import ACPOrchestrator
from kollabor.llm.llm_coordinator import LLMService


class FakeExtension:
    """Records every `session/steering` call, then fails the way a test says."""

    def __init__(self, outcome):
        self.outcome = outcome
        self.received = []

    async def ext_method(self, method, params):
        assert method == "session/steering"
        self.received.append(params["prompt"][0]["text"])
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


def busy_admission(extension):
    runner = ACPRunner(ACPProfile("test", ("test",)))
    runner.session_id = "session"
    runner.initialize_metadata = {"steering": {"supported": True}}
    runner._active_turn_id = 1
    runner._active_context = SimpleNamespace(thread_id=None, origin={})
    runner._connection = extension
    orchestrator = object.__new__(ACPOrchestrator)
    orchestrator._runner = runner
    orchestrator._record_user = lambda *args, **kwargs: None
    orchestrator._parents = {}
    acp_view = SimpleNamespace(
        steer=orchestrator.steer,
        admit=runner.admit,
        supports_image_input=False,
        voice_rules_epochs=set(),
    )
    return runner, acp_view


async def admit(acp_view, text="please also run the tests"):
    service = object.__new__(LLMService)
    return await service._admit_acp(
        acp_view, {"message": text, "request_id": "req-1"}, "state_rpc"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [TimeoutError(), asyncio.TimeoutError(), ConnectionResetError("receipt lost"), EOFError()],
)
async def test_lost_steering_receipt_is_not_queued_as_a_second_copy(failure):
    extension = FakeExtension(failure)
    runner, acp_view = busy_admission(extension)

    result = await admit(acp_view)

    assert extension.received == ["please also run the tests"]
    assert runner._queue.empty(), "the same text was queued after the steer may have landed"
    assert result["status"] == "rejected"
    assert result["reason"] == "steering_unconfirmed"


@pytest.mark.asyncio
async def test_explicit_steering_refusal_still_falls_back_to_a_queued_turn():
    extension = FakeExtension(acp.RequestError(-32601, "Method not found"))
    runner, acp_view = busy_admission(extension)

    result = await admit(acp_view)

    assert extension.received == ["please also run the tests"]
    assert result["status"] == "queued"
    assert runner._queue.qsize() == 1


@pytest.mark.asyncio
async def test_confirmed_steer_is_injected_and_not_queued():
    extension = FakeExtension({})
    runner, acp_view = busy_admission(extension)

    result = await admit(acp_view)

    assert result["status"] == "injected"
    assert runner._queue.empty()


@pytest.mark.asyncio
async def test_cancelled_steer_does_not_queue_a_copy():
    extension = FakeExtension(asyncio.CancelledError())
    runner, acp_view = busy_admission(extension)

    with pytest.raises(asyncio.CancelledError):
        await admit(acp_view)

    assert runner._queue.empty()
