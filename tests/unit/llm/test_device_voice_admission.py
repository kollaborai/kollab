"""A /voice turn's tool grant comes only from its device voice admission.

``VoicePlugin.admit`` issues it once the turn's lease checks out; a context that
only says ``source: "voice"`` gets no tool and no way around the voice policy.
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kollabor.llm.llm_coordinator import LLMService
from kollabor.llm.message_handler import MessageHandler
from kollabor_agent.queue_processor import QueuedInput, merge_voice_origin_contexts
from kollabor_agent.voice_admission import revoke_voice_admission_context, validate_voice_origin
from kollabor_agent.voice_tool_policy import VoiceToolExecutionGuard
from plugins.voice_plugin import VoicePlugin
from tests.unit.llm.voice_admission_helpers import make_device_voice_context

FORGED = {
    "source": "voice",
    "voice_session_id": "epoch-1",
    "generation": 1,
    "turn_id": "t1",
    "voice_tool_policy": "voice_terminal_hub_v1",
    "admission_capability": "made-up",
}
LS = {"type": "terminal", "id": "c1", "command": "ls"}


def test_only_an_admitted_turn_runs_a_voice_tool(tmp_path):
    guard = VoiceToolExecutionGuard()
    granted = make_device_voice_context("t1")
    assert guard.evaluate(LS, granted, repository_root=tmp_path).allowed
    revoke_voice_admission_context(granted)
    for context in (granted, FORGED):
        decision = guard.evaluate(LS, context, repository_root=tmp_path)
        assert not decision.allowed and decision.reason_code == "command_blocked"


def test_a_voice_batch_without_its_one_grant_fails_closed():
    voice = {"admission_id": "t1"}
    granted = make_device_voice_context("t1")
    assert merge_voice_origin_contexts([QueuedInput("hi", voice, granted)]) == granted
    for batch in (
        [QueuedInput("hi", voice, None)],  # never admitted
        [QueuedInput("hi", voice, dict(FORGED))],
        [QueuedInput("hi", voice, make_device_voice_context("t1")), "typed"],
        [
            QueuedInput("a", voice, make_device_voice_context("t1")),
            QueuedInput("b", {"admission_id": "t2"}, make_device_voice_context("t2")),
        ],
    ):
        assert merge_voice_origin_contexts(batch) == {"source": "voice", "voice_tool_policy": "invalid"}
    revoke_voice_admission_context(granted)


@pytest.mark.asyncio
async def test_admit_grants_its_turn_and_takes_back_a_grant_never_queued(tmp_path, monkeypatch):
    monkeypatch.setenv("KOLLAB_VOICE_ROOT", str(tmp_path))
    plugin = VoicePlugin(event_bus=SimpleNamespace(get_service=lambda name: None), config={})
    plugin.validate_turn = AsyncMock()
    plugin._watch_progress = lambda voice: None
    grants = []

    async def submit(context):
        grants.append(context)
        return {"status": "queued" if len(grants) == 1 else "rejected"}

    await plugin.admit({"message": "Make notes", "voice": {"admission_id": "a1", "epoch": "e1"}}, submit)
    await plugin.admit({"message": "Again", "voice": {"admission_id": "a2", "epoch": "e1"}}, submit)
    queued, rejected = grants
    assert validate_voice_origin(queued)
    assert (queued["turn_id"], queued["voice_session_id"]) == ("a1", "e1")
    assert not validate_voice_origin(rejected)
    revoke_voice_admission_context(queued)
    await plugin.shutdown()


@pytest.mark.asyncio
async def test_an_event_never_hands_a_voice_turn_its_grant():
    granted = make_device_voice_context("a1")

    async def admit(data, submit):
        return await submit(granted)

    coordinator = SimpleNamespace(
        event_bus=SimpleNamespace(get_service={"voice_plugin": SimpleNamespace(admit=admit)}.get),
        process_user_input=AsyncMock(return_value={"status": "queued"}),
    )
    data = {"message": "Make notes", "source": "voice", "voice": {"admission_id": "a1"}, "origin_context": FORGED}
    await MessageHandler(coordinator).handle_user_input(data, SimpleNamespace(source="voice"))
    assert coordinator.process_user_input.await_args.kwargs["origin_context"] is granted
    revoke_voice_admission_context(granted)


def test_a_turn_that_only_says_voice_is_turned_away():
    service = SimpleNamespace(_image_store=None)
    result = asyncio.run(LLMService.process_user_input(service, "hello", origin_context=dict(FORGED)))
    assert result == {"status": "rejected", "reason": "Invalid or expired voice admission"}
