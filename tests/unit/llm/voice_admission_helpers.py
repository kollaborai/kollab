"""Shared fixture factory for server-issued voice admission contexts."""

from typing import Any

from kollabor_agent.voice_admission import (
    issue_voice_admission_context,
    revoke_voice_admission_context,
)


def make_voice_origin_context(
    turn_id: str = "turn-1",
    **binding_overrides: Any,
) -> dict[str, Any]:
    binding: dict[str, Any] = {
        "owner_id": "owner-1",
        "voice_session_id": "voice-session-1",
        "control_session_id": "session-1",
        "conversation_session_id": "session-1",
        "agent_id": "agent-1",
        "thread_id": "thread-1",
        "voice_provider_profile_id": "voice-profile-1",
        "voice_prompt_revision": "prompt-r1",
        "voice_tool_policy": "voice_terminal_hub_v1",
        "generation": 1,
        "service_lease_epoch": 1,
    }
    binding.update(binding_overrides)
    return issue_voice_admission_context(binding, turn_id)


__all__ = ["make_voice_origin_context", "revoke_voice_admission_context"]
