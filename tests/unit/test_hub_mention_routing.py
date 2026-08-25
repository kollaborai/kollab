"""Tests for chat-to-Hub mention routing."""

import asyncio
from unittest.mock import AsyncMock

from kollabor.state.local import LocalStateService, parse_hub_mention


def test_parse_hub_mention_supports_direct_and_broadcast_syntax() -> None:
    assert parse_hub_mention("@lapis message here") == ("lapis", "message here")
    assert parse_hub_mention("@broadcast 'message to all'") == (
        "broadcast",
        "message to all",
    )
    assert parse_hub_mention("normal @mention prose") is None
    assert parse_hub_mention("@lapis") is None


def test_send_message_routes_mentions_without_invoking_llm() -> None:
    async def scenario() -> None:
        class FakeLlm:
            is_processing = False

            def __init__(self) -> None:
                self.messages: list[tuple[str, str, dict[str, str]]] = []
                self.task = None
                self.process_called = False

            def create_background_task(self, coro, *, name: str):
                self.task = asyncio.create_task(coro, name=name)
                return self.task

            def process_user_input(self, _text: str):
                self.process_called = True
                raise AssertionError("Hub mentions must not invoke the LLM")

            def _add_conversation_message(self, role, content, *, metadata):
                self.messages.append((role, content, metadata))

        llm = FakeLlm()
        service = LocalStateService(llm, object())
        service.hub_send_msg = AsyncMock(return_value="sent to lapis")

        response = await service.send_message("@lapis inspect the queue")
        assert response == {"accepted": True, "reason": "hub message"}
        await llm.task

        service.hub_send_msg.assert_awaited_once_with("lapis", "inspect the queue")
        assert llm.process_called is False
        assert [role for role, _, _ in llm.messages] == ["user", "assistant"]
        assert llm.messages[-1][1] == "sent to lapis"

    asyncio.run(scenario())
