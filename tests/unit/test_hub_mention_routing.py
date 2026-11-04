"""Tests for chat-to-Hub mention routing."""

import asyncio
import json
from unittest.mock import AsyncMock

from kollabor.state.local import LocalStateService, parse_hub_mention
from kollabor_ai.conversation_logger import KollaborConversationLogger


class _CursorSerial:
    """Mirror the coordinator's admission-serial cursor protocol (#676).

    ``claim_turn_serial`` hands out one monotonic ticket per admitted turn
    (mention spawn / ordinary pipeline admission). ``publish_cursor`` rejects
    untracked writes and writes older than a cursor already published.
    Every FakeLlm below speaks this protocol so the routing tests exercise
    the same ordering contract the production pipeline does.
    """

    _turn_serial = 0
    _cursor_winner_serial = -1

    def claim_turn_serial(self) -> int:
        self._turn_serial += 1
        return self._turn_serial

    def publish_cursor(self, uuid, serial) -> bool:
        if serial is None or serial < self._cursor_winner_serial:
            return False
        self._cursor_winner_serial = serial
        self.current_parent_uuid = uuid
        return True


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
        class FakeLlm(_CursorSerial):
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


def test_hub_mentions_log_the_typed_turn_through_the_logger() -> None:
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

        class FakeLlm(_CursorSerial):
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


def test_broadcasts_log_the_typed_turn_through_the_logger() -> None:
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

        class FakeLlm(_CursorSerial):
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


def test_failed_hub_mention_still_logs_the_typed_turn() -> None:
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

        class FakeLlm(_CursorSerial):
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

        class FakeLlm(_CursorSerial):
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

        class FakeLlm(_CursorSerial):
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


def test_mixed_turn_overlap_does_not_misparent_or_clobber_the_cursor() -> None:
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

        class FakeLlm(_CursorSerial):
            is_processing = False
            current_parent_uuid = "root"

            def __init__(self) -> None:
                self.logger = FakeLogger()
                self.conversation_logger = self.logger
                self.coros: list[object] = []

            def create_background_task(self, coro, *, name: str):
                # Spawn without starting: the ordinary turn below completes
                # while the mention task is still queued, so any pointer read
                # inside the task body would grab the wrong lineage.
                self.coros.append(coro)

            def _add_conversation_message(self, role, content, *, metadata):
                pass

        llm = FakeLlm()
        service = LocalStateService(llm, object())

        async def overlapping_send(target, content):
            # An ordinary LLM turn completes while the hub send awaits: the
            # pointer moves root -> ordinary-1 -> ordinary-2 around the task.
            llm.publish_cursor("ordinary-2", llm.claim_turn_serial())
            return "sent to lapis"

        service.hub_send_msg = AsyncMock(side_effect=overlapping_send)

        send_task = asyncio.create_task(service.send_message("@lapis hi"))
        while not llm.coros:
            await asyncio.sleep(0)
        # ordinary turn done before task start: admission claim + completion
        # write, exactly what the pipeline does around its user row (#676).
        llm.publish_cursor("ordinary-1", llm.claim_turn_serial())
        await send_task
        await asyncio.create_task(llm.coros[0])

        records = {
            (role, content): (parent, uid)
            for role, content, parent, uid in llm.logger.records
        }
        # The mention's typed turn must chain from the lineage captured at
        # spawn (root), not from the ordinary turn that completed meanwhile.
        assert records[("user", "@lapis hi")][0] == "root"
        assert records[("assistant", "sent to lapis")][0] == (
            records[("user", "@lapis hi")][1]
        )
        # The newer ordinary turn owns the cursor: the mention completion must
        # never overwrite a pointer that moved past its captured value.
        assert llm.current_parent_uuid == "ordinary-2"

    asyncio.run(scenario())


def test_mixed_turn_overlap_failure_keeps_the_newer_cursor() -> None:
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

        class FakeLlm(_CursorSerial):
            is_processing = False
            current_parent_uuid = "root"

            def __init__(self) -> None:
                self.logger = FakeLogger()
                self.conversation_logger = self.logger
                self.coros: list[object] = []

            def create_background_task(self, coro, *, name: str):
                self.coros.append(coro)

            def _add_conversation_message(self, role, content, *, metadata):
                pass

        llm = FakeLlm()
        service = LocalStateService(llm, object())

        async def failing_send(target, content):
            # ordinary turn wins mid-await (admission claim + completion write)
            llm.publish_cursor("ordinary-2", llm.claim_turn_serial())
            raise RuntimeError("hub down")

        service.hub_send_msg = AsyncMock(side_effect=failing_send)

        send_task = asyncio.create_task(service.send_message("@lapis hi"))
        while not llm.coros:
            await asyncio.sleep(0)
        # ordinary turn done before task start: admission claim + completion
        # write, exactly what the pipeline does around its user row (#676).
        llm.publish_cursor("ordinary-1", llm.claim_turn_serial())
        await send_task
        await asyncio.create_task(llm.coros[0])

        records = {
            (role, content): (parent, uid)
            for role, content, parent, uid in llm.logger.records
        }
        assert records[("user", "@lapis hi")][0] == "root"
        assert records[("assistant", "hub down")][0] == records[("user", "@lapis hi")][1]
        # The failure path is held to the same compare-and-swap rule.
        assert llm.current_parent_uuid == "ordinary-2"

    asyncio.run(scenario())




def _garnet_llm():
    """FakeLlm + FakeLogger speaking the admission-serial cursor protocol."""

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

    class FakeLlm(_CursorSerial):
        is_processing = False
        current_parent_uuid = "root"

        def __init__(self) -> None:
            self.logger = FakeLogger()
            self.conversation_logger = self.logger
            self.coros: list[object] = []

        def create_background_task(self, coro, *, name: str):
            self.coros.append(coro)

        def _add_conversation_message(self, role, content, *, metadata):
            pass

    return FakeLlm()


def test_mention_completion_order_fast_first_later_admitted_owns_cursor() -> None:
    """Garnet's r7 repro, completion order 1 (fast-first/slow-second).

    Two mentions admit back-to-back with the cursor at root: @peridot fast
    (serial 1), @lapis slow (serial 2). Fast completes first and owns the
    cursor for now; slow completes second and, by admission order, must
    STILL own the cursor at the end. The r6 value-compare CAS dropped this
    write (cursor was a2, not the stale captured root), skipping a4 from
    the continuation chain.
    """

    async def scenario() -> None:
        llm = _garnet_llm()
        service = LocalStateService(llm, object())
        service.hub_send_msg = AsyncMock(side_effect=lambda t, c: f"sent to {t}")

        fast = asyncio.create_task(service.send_message("@peridot fast one"))
        while len(llm.coros) < 1:
            await asyncio.sleep(0)
        slow = asyncio.create_task(service.send_message("@lapis slow one"))
        while len(llm.coros) < 2:
            await asyncio.sleep(0)
        await fast
        await slow

        # Completion order: fast (serial 1) first, slow (serial 2) second.
        await asyncio.create_task(llm.coros[0])
        await asyncio.create_task(llm.coros[1])

        records = {
            (role, content): (parent, uid)
            for role, content, parent, uid in llm.logger.records
        }
        # Lineage chains stay intact for both mentions.
        assert records[("assistant", "sent to peridot")][0] == (
            records[("user", "@peridot fast one")][1]
        )
        assert records[("assistant", "sent to lapis")][0] == (
            records[("user", "@lapis slow one")][1]
        )
        # The later-admitted mention owns the cursor at the end.
        assert llm.current_parent_uuid == records[("assistant", "sent to lapis")][1]

    asyncio.run(scenario())


def test_mention_completion_order_slow_first_later_admitted_owns_cursor() -> None:
    """Garnet's r7 repro, completion order 2 (slow-first/fast-second).

    Same admission order as order 1 (@peridot fast serial 1, @lapis slow
    serial 2) but reversed completions. The later-admitted mention still
    owns the cursor: the first-admitted completion must not jump the queue
    just because it happened to finish last in wall-clock time.
    """

    async def scenario() -> None:
        llm = _garnet_llm()
        service = LocalStateService(llm, object())
        service.hub_send_msg = AsyncMock(side_effect=lambda t, c: f"sent to {t}")

        fast = asyncio.create_task(service.send_message("@peridot fast one"))
        while len(llm.coros) < 1:
            await asyncio.sleep(0)
        slow = asyncio.create_task(service.send_message("@lapis slow one"))
        while len(llm.coros) < 2:
            await asyncio.sleep(0)
        await fast
        await slow

        # Completion order reversed: slow (serial 2) first, fast (serial 1)
        # second. Admission order — not completion order — owns the cursor.
        await asyncio.create_task(llm.coros[1])
        await asyncio.create_task(llm.coros[0])

        records = {
            (role, content): (parent, uid)
            for role, content, parent, uid in llm.logger.records
        }
        assert llm.current_parent_uuid == records[("assistant", "sent to lapis")][1]

    asyncio.run(scenario())


def test_ordinary_between_mentions_loses_only_to_later_admitted_turns() -> None:
    """The r7 trap, order A: ordinary completes between the two mentions.

    m1 admits (serial 1), an ordinary turn admits after it (serial 2), m2
    admits last (serial 3). Completions: m1, ordinary, m2. The ordinary
    write beats the earlier-admitted m1, and the later-admitted m2 beats
    both — the final cursor is m2's assistant record.
    """

    async def scenario() -> None:
        llm = _garnet_llm()
        service = LocalStateService(llm, object())
        service.hub_send_msg = AsyncMock(side_effect=lambda t, c: f"sent to {t}")

        first = asyncio.create_task(service.send_message("@peridot first one"))
        while len(llm.coros) < 1:
            await asyncio.sleep(0)
        # An ordinary turn admits after m1 (pipeline admission + completion
        # write around its user row) before m2 is even typed.
        ordinary_serial = llm.claim_turn_serial()
        llm.publish_cursor("ordinary-user-row", ordinary_serial)
        second = asyncio.create_task(service.send_message("@lapis second one"))
        while len(llm.coros) < 2:
            await asyncio.sleep(0)
        await first
        await second

        # Completions: m1, then ordinary (already done), then m2.
        await asyncio.create_task(llm.coros[0])
        llm.publish_cursor("ordinary-assistant-row", ordinary_serial)
        await asyncio.create_task(llm.coros[1])

        records = {
            (role, content): (parent, uid)
            for role, content, parent, uid in llm.logger.records
        }
        assert records[("user", "@peridot first one")][0] == "root"
        # m2 admitted after the ordinary row published, so its lineage
        # legitimately continues from that row — the stale-parent problem
        # is only about the CURSOR, not the lineage capture.
        assert records[("user", "@lapis second one")][0] == "ordinary-user-row"
        # The later-admitted mention owns the cursor over everything.
        assert llm.current_parent_uuid == records[("assistant", "sent to lapis")][1]

    asyncio.run(scenario())


def test_ordinary_between_mentions_cannot_reclaim_from_later_admitted() -> None:
    """The r7 trap, order B: ordinary completes last in wall-clock time.

    Same admission order as order A (m1 serial 1, ordinary serial 2, m2
    serial 3) but completions m1, m2, ordinary. The ordinary turn was
    admitted BEFORE m2, so its late completion must not reclaim the cursor
    from the later-admitted mention — admission order, not completion
    order, resolves the race.
    """

    async def scenario() -> None:
        llm = _garnet_llm()
        service = LocalStateService(llm, object())
        service.hub_send_msg = AsyncMock(side_effect=lambda t, c: f"sent to {t}")

        first = asyncio.create_task(service.send_message("@peridot first one"))
        while len(llm.coros) < 1:
            await asyncio.sleep(0)
        ordinary_serial = llm.claim_turn_serial()
        llm.publish_cursor("ordinary-user-row", ordinary_serial)
        second = asyncio.create_task(service.send_message("@lapis second one"))
        while len(llm.coros) < 2:
            await asyncio.sleep(0)
        await first
        await second

        # Completions: m1, m2, then the ordinary turn LAST.
        await asyncio.create_task(llm.coros[0])
        await asyncio.create_task(llm.coros[1])
        llm.publish_cursor("ordinary-assistant-row", ordinary_serial)

        records = {
            (role, content): (parent, uid)
            for role, content, parent, uid in llm.logger.records
        }
        # m2 (admitted after the ordinary turn) keeps the cursor.
        assert llm.current_parent_uuid == records[("assistant", "sent to lapis")][1]

    asyncio.run(scenario())


def test_execute_hub_message_persists_one_jsonl_pair_on_success_and_failure(
    tmp_path,
) -> None:
    async def scenario() -> None:
        text = "@lapis inspect the queue"
        parent_uuid = "conversation-root"

        for case, fails, expected_assistant_text in (
            ("success", False, "sent to lapis"),
            ("failure", True, "hub unavailable"),
        ):
            logger = KollaborConversationLogger(tmp_path / case)

            class FakeLlm(_CursorSerial):
                def __init__(self) -> None:
                    self.current_parent_uuid = parent_uuid
                    self.conversation_logger = logger
                    self.messages = []

                def _add_conversation_message(self, role, content, *, metadata):
                    self.messages.append((role, content, metadata))

            llm = FakeLlm()
            service = LocalStateService(llm, object())
            if fails:
                service.hub_send_msg = AsyncMock(
                    side_effect=RuntimeError(expected_assistant_text)
                )
            else:
                service.hub_send_msg = AsyncMock(return_value=expected_assistant_text)

            await service._execute_hub_message(
                text,
                "lapis",
                "inspect the queue",
                parent_uuid=parent_uuid,
                turn_serial=1,
            )

            records = [
                json.loads(line)
                for line in logger.session_file.read_text().splitlines()
                if line.strip()
            ]
            assert len(records) == 2
            assert [record["type"] for record in records] == ["user", "assistant"]

            user_record, assistant_record = records
            assert user_record["message"]["role"] == "user"
            assert user_record["message"]["content"] == text
            assert user_record["parentUuid"] == parent_uuid
            assert assistant_record["message"]["role"] == "assistant"
            assert assistant_record["parentUuid"] == user_record["uuid"]
            assert any(
                item.get("text") == expected_assistant_text
                for item in assistant_record["message"]["content"]
            )
            assert llm.current_parent_uuid == assistant_record["uuid"]
            service.hub_send_msg.assert_awaited_once_with("lapis", "inspect the queue")

    asyncio.run(scenario())
