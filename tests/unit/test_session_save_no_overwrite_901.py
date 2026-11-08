"""Synthetic stale-save checks while session admission remains unproven."""

import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from kollabor_ai.conversation_manager import ConversationManager


def manager(root):
    return ConversationManager(
        SimpleNamespace(
            _conversations_dir=root,
            get=lambda key, default=None: False if key == "kollabor.llm.save_conversations" else default,
        )
    )


def test_stale_save_never_replaces_an_existing_stream(tmp_path):
    first = manager(tmp_path)
    first.add_message("user", "initial synthetic")
    assert first.save_session("synthetic")
    target = tmp_path / "synthetic.jsonl"
    with target.open("a") as stream:
        stream.write(json.dumps({"type": "user", "message": {"role": "user", "content": "newer synthetic"}}) + "\n")
    before = target.read_bytes()
    first.add_message("assistant", "stale synthetic")
    assert not first.save_session("synthetic")
    assert target.read_bytes() == before


def test_competing_new_saves_install_one_complete_snapshot(tmp_path):
    writers = [manager(tmp_path), manager(tmp_path)]
    for index, writer in enumerate(writers):
        writer.add_message("user", f"synthetic {index}")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda writer: writer.save_session("same"), writers))
    assert sorted(results) == [False, True]
    saved = json.loads((tmp_path / "same.jsonl").read_text())
    assert saved["messages"][0]["content"] in {"synthetic 0", "synthetic 1"}


def test_failed_branch_preserves_existing_target_and_loaded_source(tmp_path, monkeypatch):
    source = manager(tmp_path)
    source.add_message("user", "source synthetic")
    assert source.save_session("source")
    target = tmp_path / "taken.jsonl"
    target.write_text("existing synthetic data\n")
    monkeypatch.setattr("kollabor_ai.conversation_manager.generate_branch_name", lambda: "taken")
    result = source.branch_session("source", 0)
    assert not result["success"]
    assert target.read_text() == "existing synthetic data\n"
    assert source.current_session_id == "source"
    assert source.messages[0]["content"] == "source synthetic"


def test_failed_install_never_exposes_a_partial_snapshot(tmp_path, monkeypatch):
    writer = manager(tmp_path)
    writer.add_message("user", "synthetic")

    def fail_link(*_args, **_kwargs):
        raise OSError("synthetic install failure")

    monkeypatch.setattr("kollabor_ai.conversation_manager.os.link", fail_link)
    assert not writer.save_session("new")
    assert not (tmp_path / "new.jsonl").exists()


def test_append_during_new_save_survives_after_atomic_install(tmp_path, monkeypatch):
    import asyncio
    import os
    import threading

    from kollabor_ai.conversation_logger import KollaborConversationLogger

    writer = manager(tmp_path)
    writer.add_message("user", "base synthetic")
    logger = KollaborConversationLogger(tmp_path)
    logger.session_id = "same"
    logger.session_file = tmp_path / "same.jsonl"
    entered = threading.Event()
    release = threading.Event()
    append_started = threading.Event()
    original_link = os.link

    def blocked_link(source, destination):
        entered.set()
        assert release.wait(5)
        return original_link(source, destination)

    monkeypatch.setattr("kollabor_ai.conversation_manager.os.link", blocked_link)

    def append():
        append_started.set()
        asyncio.run(logger._append_to_jsonl({"type": "user", "message": {"role": "user", "content": "tail synthetic"}}))

    with ThreadPoolExecutor(max_workers=2) as pool:
        saved = pool.submit(writer.save_session, "same")
        assert entered.wait(5)
        appended = pool.submit(append)
        assert append_started.wait(5)
        release.set()
        assert saved.result(timeout=5)
        appended.result(timeout=5)

    rows = [json.loads(line) for line in (tmp_path / "same.jsonl").read_text().splitlines()]
    assert rows[0]["messages"][0]["content"] == "base synthetic"
    assert rows[1]["message"]["content"] == "tail synthetic"
    assert len(rows) == 2


def test_refused_resume_preserves_live_owners(tmp_path, monkeypatch):
    import asyncio
    from unittest.mock import MagicMock

    from kollabor.state.local import LocalStateService
    from kollabor_events.data_models import ConversationMessage

    writer = manager(tmp_path)
    writer.add_message("user", "target synthetic")
    assert writer.save_session("target")
    current = manager(tmp_path)
    current.current_session_id = "current"
    current.add_message("user", "current synthetic")
    original_messages = current.messages
    logger = SimpleNamespace(reset_session=MagicMock())
    api = SimpleNamespace(set_session_id=MagicMock())
    history = [ConversationMessage(role="user", content="current synthetic")]
    llm = SimpleNamespace(
        conversation_manager=current, conversation_logger=logger, api_service=api, conversation_history=history
    )
    state = LocalStateService(llm, None)
    monkeypatch.setattr(current, "save_session", lambda _: False)
    with pytest.raises(ValueError, match="persist resumed session"):
        asyncio.run(state.resume_conversation("target"))
    assert current.current_session_id == "current"
    assert current.messages is original_messages
    assert [message.content for message in history] == ["current synthetic"]
    logger.reset_session.assert_not_called()
    api.set_session_id.assert_not_called()


def test_refused_resume_does_not_close_live_acp(tmp_path, monkeypatch):
    import asyncio
    from contextlib import nullcontext
    from unittest.mock import AsyncMock, MagicMock

    from kollabor.state.local import LocalStateService

    writer = manager(tmp_path)
    writer.add_message("user", "target synthetic")
    assert writer.save_session("target")
    current = manager(tmp_path)
    current.current_session_id = "current"
    current.add_message("user", "current synthetic")
    acp = SimpleNamespace(active=True, transition=lambda: nullcontext(), close=AsyncMock())
    bus = SimpleNamespace(get_service=lambda name: acp if name == "acp_orchestrator" else None)
    llm = SimpleNamespace(conversation_manager=current, conversation_history=[])
    monkeypatch.setattr(current, "save_session", MagicMock(return_value=False))
    with pytest.raises(ValueError, match="persist resumed session"):
        asyncio.run(LocalStateService(llm, None, event_bus=bus).resume_conversation("target"))
    assert current.current_session_id == "current"
    acp.close.assert_not_awaited()


def test_branch_modal_displays_save_failure():
    import asyncio
    from unittest.mock import MagicMock

    from plugins.resume_conversation_plugin import ResumeConversationPlugin

    plugin = ResumeConversationPlugin(event_bus=MagicMock(), config={})
    plugin.conversation_manager = SimpleNamespace(
        branch_session=lambda *_: {"success": False, "error": "failed to persist branched session"}
    )
    plugin._ensure_conversation_manager = lambda: True
    data = {"command": {"action": "branch_execute", "metadata": {"session_id": "synthetic", "message_index": 0}}}
    result = asyncio.run(plugin._handle_modal_command(data, None))
    assert "failed to persist branched session" in result["display_messages"][0][1]
