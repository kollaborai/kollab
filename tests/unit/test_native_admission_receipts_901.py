"""Native log-to-batch receipt membership; compaction stays disabled."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from kollabor_ai.conversation_logger import KollaborConversationLogger
from kollabor_ai.conversation_manager import ConversationManager
from tests.unit.llm.test_human_input_pipeline import _native_route_service


@pytest.mark.parametrize("with_hud", [False, True])
def test_native_receipts_cross_queue_batch_and_replay(tmp_path, with_hud):
    from tests.unit.llm.test_queue_processor import TestQueueProcessor

    fixture = TestQueueProcessor(methodName="test_cancelled_pre_request_never_calls_provider")
    fixture.setUp()

    async def run():
        service, _ = await _native_route_service("native", acp_active=False)
        log = KollaborConversationLogger(tmp_path)
        log.session_id = "synthetic-901-native"
        log.session_file = tmp_path / "synthetic-901-native.jsonl"
        log._get_working_directory = lambda: "/synthetic"
        log._get_git_branch = lambda: "synthetic"
        log._get_session_context = lambda: {}
        log._get_project_awareness = lambda: {}
        log._analyze_user_context = lambda _: {}
        log._save_conversation_memory = lambda: None
        service.conversation_logger = log
        service._turn_serial = 0
        service._cursor_winner_serial = -1
        service._batch_tail_serial = None
        service._batch_tail_uuid = None
        service._continue_serial = None
        items = []

        async def enqueue(item):
            items.append(item)
            return True

        service._enqueue_with_overflow_strategy = enqueue
        assert (await service.process_user_input("first synthetic"))["status"] == "queued"
        assert (await service.process_user_input("second synthetic"))["status"] == "queued"
        assert len(items) == 2
        receipts = [item.durable_receipts[0] for item in items]
        assert len({receipt.record_uuid for receipt in receipts}) == 2
        assert all(receipt.session_id == log.session_id for receipt in receipts)

        service._queue_processor = fixture.processor
        fixture.processor._execute_llm_turn = AsyncMock(return_value="synthetic-parent")
        service._pending_agent_hud = [object()] if with_hud else []
        service.merge_pending_agent_hud = lambda content: content
        await service._process_message_batch(items)
        batch = fixture.add_message_fn.call_args.args[0]
        expected = [{"session_id": r.session_id, "record_uuid": r.record_uuid} for r in receipts]
        assert batch.metadata["durable_input_receipts"] == expected

        config = SimpleNamespace(_conversations_dir=tmp_path, get=lambda _key, default=None: default)
        replay = ConversationManager(config)
        assert replay.load_session(log.session_id)
        rows = [row for row in replay.messages if row["uuid"] in {r.record_uuid for r in receipts}]
        assert [row["uuid"] for row in rows] == [r.record_uuid for r in receipts]
        assert [row["content"] for row in rows] == ["first synthetic", "second synthetic"]

    try:
        fixture.loop.run_until_complete(run())
    finally:
        fixture.tearDown()


def test_failed_native_log_never_yields_a_receipt(tmp_path, monkeypatch):
    import os
    from unittest.mock import MagicMock

    from tests.unit.llm.test_queue_processor import TestQueueProcessor

    fixture = TestQueueProcessor(methodName="test_cancelled_pre_request_never_calls_provider")
    fixture.setUp()

    async def run():
        service, _ = await _native_route_service("native", acp_active=False)
        log = KollaborConversationLogger(tmp_path)
        log.session_id = "synthetic-failed"
        log.session_file = tmp_path / "synthetic-failed.jsonl"
        log._get_working_directory = lambda: "/synthetic"
        log._get_git_branch = lambda: "synthetic"
        log._get_session_context = lambda: {}
        log._get_project_awareness = lambda: {}
        log._analyze_user_context = lambda _: {}
        log._save_conversation_memory = lambda: None
        service.conversation_logger = log
        service.message_display_service.display_system_message = MagicMock()
        service._turn_serial = 0
        service._cursor_winner_serial = -1
        service._continue_serial = None
        items = []

        async def enqueue(item):
            items.append(item)
            return True

        service._enqueue_with_overflow_strategy = enqueue
        monkeypatch.setattr(os, "fsync", lambda _: (_ for _ in ()).throw(OSError("synthetic flush failure")))
        assert (await service.process_user_input("failed synthetic"))["status"] == "queued"
        assert items[0].durable_receipts == ()
        assert log.session_file.read_bytes() == b""
        service.message_display_service.display_system_message.assert_called_once()

    try:
        fixture.loop.run_until_complete(run())
    finally:
        fixture.tearDown()


def test_native_receipt_is_not_issued_if_logger_session_changes():
    import asyncio

    async def run():
        service, _ = await _native_route_service("native", acp_active=False)
        logger = SimpleNamespace(session_id="first-session")

        async def log_user_message(_content, *, parent_uuid=None):
            logger.session_id = "second-session"
            return "synthetic-row"

        logger.log_user_message = log_user_message
        service.conversation_logger = logger
        service._turn_serial = 0
        service._cursor_winner_serial = -1
        service._continue_serial = None
        items = []

        async def enqueue(item):
            items.append(item)
            return True

        service._enqueue_with_overflow_strategy = enqueue
        assert (await service.process_user_input("synthetic"))["status"] == "queued"
        assert items[0].durable_receipts == ()

    asyncio.run(run())
