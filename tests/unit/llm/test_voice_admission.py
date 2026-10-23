"""Tests for server-issued, binding-scoped voice admission capabilities."""

from kollabor_agent.voice_admission import VoiceAdmissionCapabilityRegistry


def _binding():
    return {
        "owner_id": "owner-1",
        "voice_session_id": "voice-1",
        "control_session_id": "session-1",
        "conversation_session_id": "session-1",
        "agent_id": "agent-1",
        "thread_id": "thread-1",
        "voice_provider_profile_id": "voice-profile-1",
        "voice_prompt_revision": "prompt-r1",
        "voice_tool_policy": "voice_terminal_hub_v1",
        "generation": 2,
        "service_lease_epoch": 8,
    }


def test_capability_requires_server_issuance_and_matches_full_binding():
    registry = VoiceAdmissionCapabilityRegistry()
    context = registry.issue(_binding(), "turn-1")

    assert context["source"] == "livekit_voice"
    assert registry.validate(context) is True
    assert registry.validate(
        {"source": "livekit_voice", "voice_session_id": "voice-1", "turn_id": "turn-1"}
    ) is False

    tampered = dict(context)
    tampered["conversation_session_id"] = "another-session"
    assert registry.validate(tampered) is False
    tampered_turn = dict(context)
    tampered_turn["turn_id"] = "another-turn"
    assert registry.validate(tampered_turn) is False

    registry.revoke(context)
    assert registry.validate(context) is False


def test_capability_expires_and_can_be_revoked_by_turn_or_generation():
    now = [10.0]
    registry = VoiceAdmissionCapabilityRegistry(ttl_seconds=2.0, clock=lambda: now[0])
    context = registry.issue(_binding(), "turn-2")
    assert registry.validate(context) is True

    registry.revoke_turn("voice-1", 2, "turn-2")
    assert registry.validate(context) is False

    context = registry.issue(_binding(), "turn-3")
    registry.revoke_generation("voice-1", 2)
    assert registry.validate(context) is False

    context = registry.issue(_binding(), "turn-4")
    now[0] = 13.0
    assert registry.validate(context) is False
