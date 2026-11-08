"""Synthetic ACP input receipt coverage; no compaction cutline is implied."""

import asyncio
from types import SimpleNamespace

import pytest

from kollabor.llm.acp_orchestrator import ACPOrchestrator
from kollabor_ai.conversation_logger import KollaborConversationLogger
from kollabor_ai.conversation_manager import ConversationManager


def make_rig(tmp_path, monkeypatch):
    monkeypatch.setattr("kollabor_config.config_utils.get_project_data_dir", lambda: tmp_path / "data")
    config = SimpleNamespace(
        _conversations_dir=tmp_path / "conversations",
        get=lambda key, default=None: False if key == "kollabor.llm.save_conversations" else default,
    )
    logger = KollaborConversationLogger(config._conversations_dir)
    logger._get_working_directory = lambda: "/synthetic"
    logger._get_git_branch = lambda: "synthetic"
    logger._get_session_context = lambda: {}
    logger._get_project_awareness = lambda: {}
    logger._analyze_user_context = lambda _: {}
    logger._save_conversation_memory = lambda: None
    manager = ConversationManager(config, logger)
    llm = SimpleNamespace(conversation_manager=manager, conversation_logger=logger, conversation_history=[])
    bus = SimpleNamespace(get_service=lambda name: llm if name == "llm_service" else None)
    return ACPOrchestrator(bus), manager, logger, llm


@pytest.mark.asyncio
async def test_acp_receipt_is_attached_after_log_and_matches_real_replay(tmp_path, monkeypatch):
    orchestrator, manager, logger, llm = make_rig(tmp_path, monkeypatch)
    started, release = asyncio.Event(), asyncio.Event()
    original = logger.log_user_message

    async def blocked(*args, **kwargs):
        started.set()
        await release.wait()
        return await original(*args, **kwargs)

    logger.log_user_message = blocked
    orchestrator._record_user("synthetic-request", "synthetic input", "rpc", None, track_completion=True)
    await started.wait()
    assert "durable_input_receipts" not in manager.messages[0]["metadata"]
    assert "durable_input_receipts" not in llm.conversation_history[0].metadata
    tasks = tuple(orchestrator._history_tasks)
    release.set()
    await asyncio.gather(*tasks)
    receipt = manager.messages[0]["metadata"]["durable_input_receipts"]
    assert llm.conversation_history[0].metadata["durable_input_receipts"] == receipt
    assert receipt[0]["session_id"] == logger.session_id
    reader = ConversationManager(manager.config)
    assert reader.load_session(logger.session_id)
    assert [row["uuid"] for row in reader.messages] == [receipt[0]["record_uuid"]]
    assert [row["content"] for row in reader.messages] == ["synthetic input"]


@pytest.mark.asyncio
async def test_acp_delayed_log_keeps_original_session_and_never_marks_new_history(tmp_path, monkeypatch):
    orchestrator, manager, logger, llm = make_rig(tmp_path, monkeypatch)
    started, release = asyncio.Event(), asyncio.Event()
    original = logger.log_user_message
    old_session = logger.session_id

    async def blocked(*args, **kwargs):
        started.set()
        await release.wait()
        return await original(*args, **kwargs)

    logger.log_user_message = blocked
    orchestrator._record_user("synthetic-request", "synthetic input", "rpc", None, track_completion=True)
    tasks = tuple(orchestrator._history_tasks)
    await started.wait()
    logger.reset_session("new-synthetic-session")
    manager.current_session_id = "new-synthetic-session"
    manager.messages = []
    llm.conversation_history = []
    release.set()
    await asyncio.gather(*tasks)
    assert not (manager.conversations_dir / "new-synthetic-session.jsonl").exists()
    reader = ConversationManager(manager.config)
    assert reader.load_session(old_session)
    assert [row["content"] for row in reader.messages] == ["synthetic input"]
    assert manager.messages == [] and llm.conversation_history == []
