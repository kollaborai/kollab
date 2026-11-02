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


def test_parse_hub_mention_takes_an_agent_on_another_computer() -> None:
    assert parse_hub_mention("@lapis@devbox ship it") == ("lapis@devbox", "ship it")
    assert parse_hub_mention("@lapis@ ship it") is None


def test_web_broadcasts_take_the_terminal_path() -> None:
    async def scenario() -> None:
        class Llm:
            is_processing = False
            task = None

            def create_background_task(self, coro, *, name: str):
                self.task = asyncio.create_task(coro, name=name)
                return self.task

            def _add_conversation_message(self, role, content, *, metadata):
                pass

        llm = Llm()
        service = LocalStateService(llm, object())
        service.send_hub_user_message = AsyncMock(return_value="broadcast to 2 agent(s)")
        service.hub_send_msg = AsyncMock()

        for text in ("@global-broadcast ship it", "@broadcast hi", "@local-broadcast yo"):
            assert await service.send_message(text) == {"accepted": True, "reason": "hub message"}
            await llm.task

        assert [call.args for call in service.send_hub_user_message.await_args_list] == [
            ("global-broadcast", "ship it"),
            ("broadcast", "hi"),
            ("local-broadcast", "yo"),
        ]
        service.hub_send_msg.assert_not_awaited()

    asyncio.run(scenario())


def test_hub_mentions_persist_to_the_session_jsonl() -> None:
    async def scenario() -> None:
        class FakeLogger:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, object]] = []

            async def log_user_message(self, content, parent_uuid=None):
                self.calls.append(("user", content, parent_uuid))
                return "u1"

            async def log_assistant_message(self, content, parent_uuid=None):
                self.calls.append(("assistant", content, parent_uuid))
                return "a1"

        class FakeLlm:
            is_processing = False
            current_parent_uuid = "root"

            def __init__(self) -> None:
                self.logger = FakeLogger()
                self.conversation_logger = self.logger
                self.task = None

            def create_background_task(self, coro, *, name: str):
                self.task = asyncio.create_task(coro, name=name)
                return self.task

            def _add_conversation_message(self, role, content, *, metadata):
                pass

        llm = FakeLlm()
        service = LocalStateService(llm, object())
        service.hub_send_msg = AsyncMock(return_value="sent to lapis")

        await service.send_message("@lapis inspect the queue")
        await llm.task

        assert [(role, content) for role, content, _ in llm.logger.calls] == [
            ("user", "@lapis inspect the queue"),
            ("assistant", "sent to lapis"),
        ]
        assert llm.logger.calls[0][2] == "root"
        assert llm.logger.calls[1][2] == "u1"
        # The pointer advances after the assistant record so ordinary turns
        # continue the chain; the per-turn capture is what keeps concurrent
        # mentions from reparenting each other mid-turn.
        assert llm.current_parent_uuid == "a1"

    asyncio.run(scenario())


def test_broadcasts_persist_to_the_session_jsonl() -> None:
    async def scenario() -> None:
        class FakeLogger:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []

            async def log_user_message(self, content, parent_uuid=None):
                self.calls.append(("user", content))
                return "u1"

            async def log_assistant_message(self, content, parent_uuid=None):
                self.calls.append(("assistant", content))
                return "a1"

        class FakeLlm:
            is_processing = False
            current_parent_uuid = None

            def __init__(self) -> None:
                self.logger = FakeLogger()
                self.conversation_logger = self.logger
                self.task = None

            def create_background_task(self, coro, *, name: str):
                self.task = asyncio.create_task(coro, name=name)
                return self.task

            def _add_conversation_message(self, role, content, *, metadata):
                pass

        llm = FakeLlm()
        service = LocalStateService(llm, object())
        service.send_hub_user_message = AsyncMock(return_value="broadcast to 2 agent(s)")

        await service.send_message("@broadcast hi")
        await llm.task

        assert llm.logger.calls == [
            ("user", "@broadcast hi"),
            ("assistant", "broadcast to 2 agent(s)"),
        ]

    asyncio.run(scenario())


def test_failed_hub_mention_still_persists_the_typed_turn() -> None:
    async def scenario() -> None:
        class FakeLogger:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str]] = []

            async def log_user_message(self, content, parent_uuid=None):
                self.calls.append(("user", content))
                return "u1"

            async def log_assistant_message(self, content, parent_uuid=None):
                self.calls.append(("assistant", content))
                return "a1"

        class FakeLlm:
            is_processing = False
            current_parent_uuid = None

            def __init__(self) -> None:
                self.logger = FakeLogger()
                self.conversation_logger = self.logger
                self.task = None

            def create_background_task(self, coro, *, name: str):
                self.task = asyncio.create_task(coro, name=name)
                return self.task

            def _add_conversation_message(self, role, content, *, metadata):
                pass

        llm = FakeLlm()
        service = LocalStateService(llm, object())
        service.hub_send_msg = AsyncMock(side_effect=RuntimeError("socket gone"))

        await service.send_message("@lapis inspect the queue")
        await llm.task

        assert llm.logger.calls[0] == ("user", "@lapis inspect the queue")
        assert llm.logger.calls[1][0] == "assistant"

    asyncio.run(scenario())


def test_overlapping_mentions_do_not_reparent_each_other() -> None:
    async def scenario() -> None:
        class FakeLogger:
            def __init__(self) -> None:
                self.records: list[tuple[str, str, object, str]] = []
                self._n = 0

            async def log_user_message(self, content, parent_uuid=None):
                self._n += 1
                uid = f"u{self._n}"
                self.records.append(("user", content, parent_uuid, uid))
                return uid

            async def log_assistant_message(self, content, parent_uuid=None):
                self._n += 1
                uid = f"a{self._n}"
                self.records.append(("assistant", content, parent_uuid, uid))
                return uid

        class FakeLlm:
            is_processing = False
            current_parent_uuid = "root"

            def __init__(self) -> None:
                self.logger = FakeLogger()
                self.conversation_logger = self.logger
                self.tasks: list[asyncio.Task] = []

            def create_background_task(self, coro, *, name: str):
                task = asyncio.create_task(coro, name=name)
                self.tasks.append(task)
                return task

            def _add_conversation_message(self, role, content, *, metadata):
                pass

        llm = FakeLlm()
        service = LocalStateService(llm, object())
        release_lapis = asyncio.Event()

        async def slow_send(target, content):
            if target == "lapis":
                await release_lapis.wait()
            return f"sent to {target}"

        service.hub_send_msg = AsyncMock(side_effect=slow_send)

        first = asyncio.create_task(service.send_message("@lapis slow one"))
        while len(llm.logger.records) < 1:
            await asyncio.sleep(0)
        second = asyncio.create_task(service.send_message("@peridot fast one"))
        await second
        await llm.tasks[1]
        release_lapis.set()
        await first
        await llm.tasks[0]

        records = {
            (role, content): (parent, uid)
            for role, content, parent, uid in llm.logger.records
        }
        assert records[("assistant", "sent to lapis")][0] == "u1"
        assert records[("assistant", "sent to peridot")][0] == "u2"

    asyncio.run(scenario())


def test_ordinary_turn_continues_after_a_hub_mention() -> None:
    async def scenario() -> None:
        class FakeLogger:
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, object]] = []

            async def log_user_message(self, content, parent_uuid=None):
                self.calls.append(("user", content, parent_uuid))
                return "u1"

            async def log_assistant_message(self, content, parent_uuid=None):
                self.calls.append(("assistant", content, parent_uuid))
                return "a1"

        class FakeLlm:
            is_processing = False
            current_parent_uuid = "root"

            def __init__(self) -> None:
                self.logger = FakeLogger()
                self.conversation_logger = self.logger
                self.task = None

            def create_background_task(self, coro, *, name: str):
                self.task = asyncio.create_task(coro, name=name)
                return self.task

            def _add_conversation_message(self, role, content, *, metadata):
                pass

        llm = FakeLlm()
        service = LocalStateService(llm, object())
        service.hub_send_msg = AsyncMock(return_value="sent to lapis")

        await service.send_message("@lapis hi")
        await llm.task

        # The shared pointer must advance to the hub assistant record so the
        # next ordinary LLM turn (which reads the pointer, pipeline parity)
        # chains AFTER the mention instead of branching from before it.
        assert llm.current_parent_uuid == "a1"
        await llm.conversation_logger.log_user_message(
            "ordinary follow-up", parent_uuid=llm.current_parent_uuid
        )
        assert llm.logger.calls[-1] == ("user", "ordinary follow-up", "a1")

    asyncio.run(scenario())
