"""#1079: a LiveKit room's spoken ACP turn carries the voice tag and queues, not steers.

Marco speaks to a Claude Code (ACP) session through the web UI mic. Those turns
arrive with `origin_context.source == "livekit_voice"` and no `voice` dict, so
`_admit_acp` used to send them down the `acp.steer` branch like typed text:
the agent got no voice tag and each spoken line interrupted the running turn.

The trust rule: the origin is treated as a voice turn only after
`validate_voice_admission_context` passes — the registry holds a capability
the voice runtime issued for exactly this session, generation, and turn.
"""

from types import SimpleNamespace

import pytest
from kollabor_voice.voice_context import VOICE_NUDGE, VOICE_RULES_MARK

from kollabor.llm.acp_bridge import ACPProfile, ACPRunner
from kollabor.llm.acp_orchestrator import ACPOrchestrator
from kollabor.llm.llm_coordinator import LLMService
from kollabor_agent.voice_admission import (
    issue_voice_admission_context,
    validate_voice_admission_context,
)

BINDING = {
    "owner_id": "room-owner",
    "voice_session_id": "vs-1",
    "control_session_id": "cs-1",
    "conversation_session_id": "conv-1",
    "agent_id": "agent-1",
    "thread_id": None,
    "voice_provider_profile_id": "pp-1",
    "voice_prompt_revision": "rev-1",
    "voice_tool_policy": "standard",
    "generation": 3,
    "service_lease_epoch": 7,
}


class FakeExtension:
    """Records every `session/steering` call; one raises for typed turns."""

    def __init__(self):
        self.received = []

    async def ext_method(self, method, params):
        assert method == "session/steering"
        self.received.append(params["prompt"][0]["text"])
        return {}


def busy_room():
    """A harness with a turn running: a typed line would steer it (#1079)."""
    extension = FakeExtension()
    runner = ACPRunner(ACPProfile("test", ("test",)))
    runner.session_id = "session"
    runner.generation = 1
    runner.initialize_metadata = {"steering": {"supported": True}}
    runner._active_turn_id = 1
    runner._active_generation = 1
    runner._active_context = SimpleNamespace(
        request_id="active-request",
        turn_id=1,
        thread_id=None,
        origin={},
    )
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
        voice_turns={},
    )
    return runner, acp_view, extension


async def admit(acp_view, *, origin=None, text="please also run the tests"):
    service = object.__new__(LLMService)
    data = {"message": text, "request_id": "req-1"}
    if origin is not None:
        data["origin_context"] = origin
    return await service._admit_acp(acp_view, data, "state_rpc")


@pytest.mark.asyncio
async def test_spoken_livekit_turn_gets_the_voice_tag_and_queues():
    runner, acp_view, extension = busy_room()
    origin = issue_voice_admission_context(BINDING, "lv-turn-1")
    assert validate_voice_admission_context(origin)

    result = await admit(acp_view, origin=origin)

    assert result["status"] == "queued"
    assert extension.received == [], "a spoken line steered the running turn"
    assert runner._queue.qsize() == 1
    queued = runner._queue.get_nowait()
    assert queued.text.startswith(VOICE_RULES_MARK), "the room session's first turn lacks the rules"
    assert VOICE_NUDGE in queued.text
    assert acp_view.voice_turns == {}, "a room turn must not be handed to /voice device speech"
    assert acp_view.voice_rules_epochs == {f"{BINDING['voice_session_id']}:{BINDING['generation']}"}


@pytest.mark.asyncio
async def test_second_spoken_line_queues_with_nudge_only():
    runner, acp_view, extension = busy_room()
    first = issue_voice_admission_context(BINDING, "lv-turn-1")
    second = issue_voice_admission_context(BINDING, "lv-turn-2")

    assert (await admit(acp_view, origin=first))["status"] == "queued"
    runner._queue.get_nowait()  # the harness picks the first queued turn up
    assert (await admit(acp_view, origin=second, text="and then deploy"))["status"] == "queued"

    assert extension.received == [], "spoken lines steered the running turn"
    assert runner._queue.qsize() == 1
    queued = runner._queue.get_nowait()
    assert queued.text.startswith(VOICE_NUDGE)
    assert VOICE_RULES_MARK not in queued.text, "the rules were sent twice in one room session"
    assert acp_view.voice_turns == {}, "a room turn must not be handed to /voice device speech"


@pytest.mark.asyncio
async def test_typed_turn_still_steers_with_no_voice_tag():
    runner, acp_view, extension = busy_room()

    result = await admit(acp_view)

    assert result["status"] == "injected"
    assert extension.received == ["please also run the tests"]
    assert runner._queue.empty()
    assert not acp_view.voice_rules_epochs


@pytest.mark.asyncio
async def test_voice_source_without_a_capability_is_treated_as_typed():
    """Only a validated origin is trusted: a bare livekit marker stays typed."""
    runner, acp_view, extension = busy_room()
    origin = {
        "source": "livekit_voice",
        "voice_session_id": "vs-forged",
        "generation": 1,
    }
    assert not validate_voice_admission_context(origin)

    result = await admit(acp_view, origin=origin)

    assert result["status"] == "injected"
    assert extension.received == ["please also run the tests"]
    assert runner._queue.empty()
    assert not acp_view.voice_rules_epochs
