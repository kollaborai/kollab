"""Persisted model context is not part of the visible chat transcript."""

from __future__ import annotations

from types import SimpleNamespace

from kollabor.state.chat_stream import ChatStreamService
from kollabor_events.data_models import ConversationMessage


class _State:
    def __init__(self, history):
        self._event_bus = SimpleNamespace(get_service=lambda _name: None)
        self._llm_service = SimpleNamespace(conversation_history=history)

    def get_conversation_uid(self) -> str:
        return "conversation-projection"


def test_system_initialization_prompt_is_not_projected_as_a_chat_row():
    state = _State(
        [
            ConversationMessage(
                role="system",
                content="private system instructions that must not be displayed",
                metadata={"system_initialization": True},
            )
        ]
    )

    snapshot = ChatStreamService(state).subscribe({})["events"][0]

    assert snapshot["data"]["rows"] == []
