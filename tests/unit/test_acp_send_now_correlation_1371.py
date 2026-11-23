import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kollabor.llm import acp_orchestrator as orchestrator_module
from kollabor.llm.acp_bridge import (
    ACPSteeringUnconfirmed,
    ACPAdmission,
    ACPProfile,
    ACPRunner,
)
from kollabor.llm.acp_orchestrator import ACPOrchestrator


class _SessionUpdate:
    def model_dump(self, *, by_alias: bool) -> dict:
        assert by_alias is True
        return {
            "sessionUpdate": "agent_message_chunk",
            "content": {"text": "partial reply"},
        }


def _active_runner() -> tuple[ACPRunner, list[dict]]:
    updates: list[dict] = []
    runner = ACPRunner(
        ACPProfile("send-now-test", ("unused",)),
        update_callback=updates.append,
        remember_options=False,
    )
    runner.session_id = "session-A"
    runner.generation = 7
    runner._active_generation = 7
    runner._active_turn_id = 12
    runner._active_context = SimpleNamespace(
        request_id="active-request-A",
        turn_id=12,
        source="state_rpc",
        thread_id="thread-A",
        origin={"operator": True},
    )
    return runner, updates


@pytest.mark.asyncio
async def test_session_update_has_the_active_request_turn_and_generation():
    runner, updates = _active_runner()

    await runner._session_update("session-A", _SessionUpdate())

    assert updates == [{
        "kind": "text",
        "text": "partial reply",
        "request_id": "active-request-A",
        "turn_id": 12,
        "generation": 7,
        "source": "state_rpc",
        "thread_id": "thread-A",
    }]


@pytest.mark.asyncio
async def test_finished_turn_update_keeps_its_generation():
    runner, updates = _active_runner()
    runner._connection = SimpleNamespace(
        prompt=AsyncMock(return_value=SimpleNamespace(stopReason="end_turn", usage=None)),
    )
    admission = ACPAdmission(
        "active-request-A",
        "state_rpc",
        "thread-A",
        7,
        12,
        "hello",
        asyncio.get_running_loop().create_future(),
    )

    await runner._run_prompt(admission, 7)

    assert updates[-1] == {
        "kind": "turn_finished",
        "request_id": "active-request-A",
        "turn_id": 12,
        "generation": 7,
        "source": "state_rpc",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("owner_change", ["request", "turn", "generation", "session"])
async def test_steering_rejects_receipt_if_the_active_owner_changes_in_flight(owner_change):
    runner, _ = _active_runner()
    entered = asyncio.Event()
    release = asyncio.Event()

    async def delayed_steering(_method: str, _params: dict) -> None:
        entered.set()
        await release.wait()

    runner.initialize_metadata = {"steering": {"supported": True}}
    runner._connection = SimpleNamespace(ext_method=delayed_steering)
    task = asyncio.create_task(
        runner.steer("message B", origin={"operator": True}, thread_id="thread-A")
    )
    await entered.wait()
    if owner_change == "request":
        runner._active_context = SimpleNamespace(
            request_id="active-request-C",
            turn_id=12,
            source="state_rpc",
            thread_id="thread-A",
            origin={"operator": True},
        )
    elif owner_change == "turn":
        runner._active_context = SimpleNamespace(
            request_id="active-request-C",
            turn_id=13,
            source="state_rpc",
            thread_id="thread-A",
            origin={"operator": True},
        )
        runner._active_turn_id = 13
    elif owner_change == "generation":
        runner._active_generation = 8
        runner.generation = 8
    else:
        runner.session_id = "session-B"
    release.set()

    with pytest.raises(ACPSteeringUnconfirmed, match="turn changed"):
        await task


@pytest.mark.asyncio
async def test_orchestrator_steering_preserves_the_client_id_and_joined_owner():
    runner, _ = _active_runner()
    runner.steer = AsyncMock(return_value=True)
    orchestrator = object.__new__(ACPOrchestrator)
    orchestrator._runner = runner
    orchestrator._parents = {}
    recorded: list[tuple] = []
    orchestrator._record_user = lambda *args: recorded.append(args)

    result = await orchestrator.steer(
        "message B",
        origin={"operator": True},
        thread_id="thread-B",
        request_id="client-request-B",
    )

    assert recorded == [("client-request-B", "message B", "steering", "thread-B")]
    assert result == {
        "joined_request_id": "active-request-A",
        "joined_turn_id": 12,
        "joined_generation": 7,
    }


@pytest.mark.asyncio
async def test_orchestrator_does_not_record_steering_after_runner_replacement():
    runner, _ = _active_runner()
    orchestrator = object.__new__(ACPOrchestrator)
    orchestrator._runner = runner
    orchestrator._parents = {}
    recorded: list[tuple] = []
    orchestrator._record_user = lambda *args: recorded.append(args)

    async def replace_runner(*_args, **_kwargs):
        orchestrator._runner = SimpleNamespace()
        return True

    runner.steer = replace_runner

    with pytest.raises(ACPSteeringUnconfirmed, match="turn changed"):
        await orchestrator.steer(
            "message B",
            origin={"operator": True},
            request_id="client-request-B",
        )

    assert recorded == []


@pytest.mark.parametrize(
    ("kind", "payload", "projected_events"),
    [
        (
            "agent_thought_chunk",
            {"content": {"text": "thinking"}},
            {"acp_update", "thinking"},
        ),
        (
            "tool_call",
            {"toolCallId": "tool-A", "title": "Run A", "status": "in_progress"},
            {"acp_update", "tool_start"},
        ),
        (
            "tool_call_update",
            {"toolCallId": "tool-A", "title": "Run A", "status": "completed", "rawOutput": "done"},
            {"acp_update", "tool_result"},
        ),
        ("permission_request", {"title": "Approval"}, {"acp_waiting"}),
        ("turn_finished", {}, {"acp_waiting"}),
    ],
)
def test_orchestrator_forwards_generation_to_projected_acp_events(
    monkeypatch, kind, payload, projected_events,
):
    published: list[tuple[str, dict]] = []

    class EventBus:
        def get_service(self, _name: str):
            return None

    def publish(_bus, event_type: str, **payload) -> None:
        published.append((event_type, payload))

    monkeypatch.setattr(orchestrator_module, "publish_semantic", publish)
    orchestrator = object.__new__(ACPOrchestrator)
    orchestrator.event_bus = EventBus()
    orchestrator._observed_turns = {}
    orchestrator._runner = SimpleNamespace(session_id="session-A")
    orchestrator._display_turn = None
    orchestrator.voice_turns = {}
    orchestrator.hub_plugin = None
    orchestrator._profile_name = "send-now-test"

    orchestrator._on_runner_update({
        "kind": kind,
        "payload": payload,
        "request_id": "active-request-A",
        "turn_id": 12,
        "generation": 7,
        "source": "state_rpc",
        "thread_id": "thread-A",
    })

    for event_type in projected_events:
        event_payloads = [payload for published_type, payload in published if published_type == event_type]
        assert event_payloads, f"missing {event_type} projection for {kind}"
        assert all(payload.get("generation") == 7 for payload in event_payloads)
