"""#1371: a steered input is recorded under the id its sender chose."""

from types import SimpleNamespace

import pytest

from kollabor.llm.acp_bridge import ACPProfile, ACPRunner
from kollabor.llm.acp_orchestrator import ACPOrchestrator
from kollabor.llm.llm_coordinator import LLMService


class FakeExtension:
    """Records every `session/steering` call, then answers the way a test says."""

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
    """A running turn that a steer can be correlated with."""
    runner = ACPRunner(ACPProfile("test", ("test",)))
    runner.session_id = "session"
    runner.initialize_metadata = {"steering": {"supported": True}}
    runner._active_turn_id = 1
    runner._active_generation = 1
    runner._active_context = SimpleNamespace(
        thread_id=None, origin={}, request_id="active-req", turn_id=1
    )
    runner._connection = extension
    orchestrator = object.__new__(ACPOrchestrator)
    orchestrator._runner = runner
    recorded = []
    orchestrator._record_user = lambda *args, **kwargs: recorded.append(args)
    orchestrator._parents = {}
    acp_view = SimpleNamespace(
        steer=orchestrator.steer,
        admit=runner.admit,
        supports_image_input=False,
        voice_rules_epochs=set(),
    )
    return orchestrator, runner, acp_view, recorded


async def admit(acp_view, text="please also run the tests", request_id=None):
    service = object.__new__(LLMService)
    data = {"message": text}
    if request_id is not None:
        data["request_id"] = request_id
    return await service._admit_acp(acp_view, data, "state_rpc")


@pytest.mark.asyncio
async def test_sender_id_is_the_recorded_id():
    extension = FakeExtension({})
    _, runner, acp_view, recorded = busy_admission(extension)

    result = await admit(acp_view, request_id="client-req-A")

    assert result == {"status": "injected", "request_id": "client-req-A"}
    assert runner._queue.empty()
    assert [args[0] for args in recorded] == ["client-req-A"]


@pytest.mark.asyncio
async def test_without_sender_id_the_recorded_id_is_the_admission_id():
    extension = FakeExtension({})
    _, runner, acp_view, recorded = busy_admission(extension)

    result = await admit(acp_view)

    assert result["status"] == "injected"
    assert runner._queue.empty()
    assert [args[0] for args in recorded] == [result["request_id"]]
    assert not recorded[0][0].startswith("steering-")


@pytest.mark.asyncio
async def test_orchestrator_steer_records_under_record_request_id():
    extension = FakeExtension({})
    orchestrator, _, _, recorded = busy_admission(extension)

    result = await orchestrator.steer(
        "please also run the tests",
        origin={"operator": True},
        record_request_id="minted-1",
    )

    assert result
    assert [args[0] for args in recorded] == ["minted-1"]


@pytest.mark.asyncio
async def test_orchestrator_steer_records_sender_id():
    extension = FakeExtension({})
    orchestrator, _, _, recorded = busy_admission(extension)

    result = await orchestrator.steer(
        "please also run the tests",
        origin={"operator": True},
        request_id="client-req-A",
        record_request_id="client-req-A",
    )

    assert result
    assert [args[0] for args in recorded] == ["client-req-A"]
