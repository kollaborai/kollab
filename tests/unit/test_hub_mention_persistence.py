"""Web @broadcast and @mention turns must persist to the session JSONL (#672).

The mention path skips the LLM pipeline that normally writes session records,
so a broadcast turn appeared in the UI snapshot and vault stream but never in
conversations/session_*.jsonl - silently dropped on resume or history reload.
"""

import asyncio
from unittest.mock import AsyncMock

from kollabor.state.local import LocalStateService


class FakeLogger:
    def __init__(self) -> None:
        self.user_records: list[dict] = []
        self.assistant_records: list[dict] = []

    async def log_user_message(self, content, parent_uuid=None, user_context=None):
        uuid = f"user-{len(self.user_records)}"
        self.user_records.append(
            {"content": content, "parent_uuid": parent_uuid, "user_context": user_context}
        )
        return uuid

    async def log_assistant_message(self, content, parent_uuid, **_kwargs):
        uuid = f"assistant-{len(self.assistant_records)}"
        self.assistant_records.append({"content": content, "parent_uuid": parent_uuid})
        return uuid


class Llm:
    is_processing = False
    task = None
    current_parent_uuid = "prev-turn-uuid"

    def __init__(self) -> None:
        self.conversation_logger = FakeLogger()

    def create_background_task(self, coro, *, name: str):
        self.task = asyncio.create_task(coro, name=name)
        return self.task

    def _add_conversation_message(self, role, content, *, metadata):
        pass


def test_broadcast_turn_is_persisted_to_the_session_jsonl() -> None:
    async def scenario() -> None:
        llm = Llm()
        service = LocalStateService(llm, object())
        service.send_hub_user_message = AsyncMock(return_value="broadcast to 2 agent(s)")

        assert await service.send_message("@broadcast hello all") == {
            "accepted": True,
            "reason": "hub message",
        }
        await llm.task

        user = llm.conversation_logger.user_records
        assistant = llm.conversation_logger.assistant_records
        assert [r["content"] for r in user] == ["@broadcast hello all"]
        # The record threads after the previous turn, like a typed prompt.
        assert user[0]["parent_uuid"] == "prev-turn-uuid"
        assert user[0]["user_context"]["hub_broadcast"] is True
        assert [r["content"] for r in assistant] == ["broadcast to 2 agent(s)"]
        # The reply record chains to this turn's user record.
        assert assistant[0]["parent_uuid"] == "user-0"
        # The next turn continues after this one.
        assert llm.current_parent_uuid == "assistant-0"

    asyncio.run(scenario())


def test_addressed_mention_turn_is_persisted_to_the_session_jsonl() -> None:
    async def scenario() -> None:
        llm = Llm()
        service = LocalStateService(llm, object())
        service.hub_send_msg = AsyncMock(return_value="sent to lapis")

        assert await service.send_message("@lapis inspect the queue") == {
            "accepted": True,
            "reason": "hub message",
        }
        await llm.task

        user = llm.conversation_logger.user_records
        assistant = llm.conversation_logger.assistant_records
        assert [r["content"] for r in user] == ["@lapis inspect the queue"]
        assert user[0]["user_context"]["hub_broadcast"] is False
        assert [r["content"] for r in assistant] == ["sent to lapis"]
        assert llm.current_parent_uuid == "assistant-0"

    asyncio.run(scenario())


def test_failed_send_still_persists_the_turn() -> None:
    async def scenario() -> None:
        llm = Llm()
        service = LocalStateService(llm, object())
        service.hub_send_msg = AsyncMock(return_value="hub: peer unreachable")

        assert await service.send_message("@lapis ping") == {
            "accepted": True,
            "reason": "hub message",
        }
        await llm.task

        assistant = llm.conversation_logger.assistant_records
        assert [r["content"] for r in assistant] == ["hub: peer unreachable"]

    asyncio.run(scenario())
