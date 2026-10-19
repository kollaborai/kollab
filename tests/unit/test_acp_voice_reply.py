"""A Claude Code (ACP) agent's reply to a mic turn is spoken, like a native agent's."""

import asyncio
from types import SimpleNamespace

import pytest

from kollabor.llm.acp_orchestrator import ACPOrchestrator
from kollabor_ai.response_channels import split_response_channels


def test_split_keeps_display_and_returns_spoken():
    text = "<display_text>On screen `<spoken_text>x</spoken_text>`</display_text><spoken_text>Say this.</spoken_text>"
    display, spoken = split_response_channels(text)
    assert spoken == "Say this."
    assert "Say this." not in display and "On screen" in display
    assert split_response_channels("plain answer") == ("plain answer", None)


@pytest.mark.asyncio
async def test_finished_mic_turn_is_handed_to_voice():
    emitted = []

    async def emit(event, data, source):
        emitted.append((event, data))

    orchestrator = ACPOrchestrator.__new__(ACPOrchestrator)
    orchestrator.event_bus = SimpleNamespace(emit_with_hooks=emit)
    orchestrator._delivery_tasks = set()
    voice = {"epoch": 1, "reply_id": "r1"}
    orchestrator._speak_reply(voice, "<display_text>Done.</display_text><spoken_text>All done.</spoken_text>")
    await asyncio.gather(*orchestrator._delivery_tasks)
    assert emitted == [
        ("voice_response_ready", {"voice": voice, "display_text": "Done.", "spoken_text": "All done."})
    ]


@pytest.mark.asyncio
async def test_active_profile_reports_the_harness_model():
    from kollabor.state.local import LocalStateService

    acp = SimpleNamespace(
        active=True,
        advertised_config_options=lambda: [{"id": "model", "category": "model", "currentValue": "sonnet"}],
    )
    state = LocalStateService.__new__(LocalStateService)
    state._event_bus = SimpleNamespace(get_service=lambda name: acp if name == "acp_orchestrator" else None)
    assert state._acp_harness_model() == "sonnet"
    acp.active = False
    assert state._acp_harness_model() == ""
