"""Synthetic issue #901 acceptance for session save/replay behavior."""

from __future__ import annotations

import asyncio
import json
import subprocess
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from kollabor_ai import conversation_manager as conversation_manager_module
from kollabor_ai.conversation_logger import (
    KollaborConversationLogger,
)
from kollabor_ai.conversation_manager import ConversationManager

SESSION_ID = "issue-901-save-persistence"
BASE_USER = "BASE-CANARY: synthetic existing user turn"
BASE_ASSISTANT = "BASE-ANSWER-CANARY: synthetic existing reply"
SECOND_SAVE = "SECOND-SAVE-CANARY: locally managed later turn"
INTERLEAVED_TAIL = "INTERLEAVED-TAIL-CANARY: appended during stale save"
SECOND_ANSWER = "SECOND-ANSWER-CANARY"


@pytest.fixture(autouse=True)
def _guard_unexpected_subprocesses(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    """Block and record process launches in these synthetic persistence tests."""
    attempts: list[str] = []

    def deny(api: str):
        def _deny(*args, **kwargs):
            attempts.append(api)
            raise AssertionError(f"unexpected child-process attempt via {api}")

        return _deny

    monkeypatch.setattr(subprocess, "run", deny("subprocess.run"))
    monkeypatch.setattr(subprocess, "Popen", deny("subprocess.Popen"))
    yield
    assert not attempts, f"blocked unexpected child-process attempt(s): {attempts}"


def _assert_exactly_once_in_order(actual: list[str], expected: list[str]) -> None:
    assert [actual.count(content) for content in expected] == [1] * len(expected)
    assert actual == expected


def _jsonl_canary_contents(session_file: Path) -> list[str]:
    canaries = {
        BASE_USER,
        BASE_ASSISTANT,
        SECOND_SAVE,
        SECOND_ANSWER,
        INTERLEAVED_TAIL,
    }
    contents: list[str] = []
    for line in session_file.read_text().splitlines():
        record = json.loads(line)
        snapshot_messages = record.get("messages")
        if isinstance(snapshot_messages, list):
            for message in snapshot_messages:
                content = message.get("content") if isinstance(message, dict) else None
                if isinstance(content, str) and content in canaries:
                    contents.append(content)
        message = record.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if isinstance(content, str) and content in canaries:
            contents.append(content)
    return contents


def _config(conversations_dir: Path) -> SimpleNamespace:
    return SimpleNamespace(
        _conversations_dir=conversations_dir,
        get=lambda key, default=None: False if key == "kollabor.llm.save_conversations" else default,
    )


def _manager(
    conversations_dir: Path,
    conversation_logger: KollaborConversationLogger | None = None,
) -> ConversationManager:
    return ConversationManager(_config(conversations_dir), conversation_logger=conversation_logger)


def test_second_save_of_existing_local_session_round_trips(tmp_path: Path) -> None:
    """A normal second save updates the existing session and reloads fully."""
    conversations_dir = tmp_path / "conversations"
    logger = KollaborConversationLogger(conversations_dir)
    logger.reset_session(SESSION_ID)
    writer = _manager(conversations_dir, logger)

    first_user = writer.add_message("user", BASE_USER)
    first_answer = writer.add_message("assistant", BASE_ASSISTANT, parent_uuid=first_user)
    assert writer.save_session(SESSION_ID) is True

    second_user = writer.add_message("user", SECOND_SAVE, parent_uuid=first_answer)
    writer.add_message("assistant", SECOND_ANSWER, parent_uuid=second_user)
    assert writer.save_session(SESSION_ID) is True

    expected = [BASE_USER, BASE_ASSISTANT, SECOND_SAVE, SECOND_ANSWER]
    _assert_exactly_once_in_order(_jsonl_canary_contents(logger.session_file), expected)

    reader = _manager(conversations_dir)
    assert reader.load_session(SESSION_ID) is True
    _assert_exactly_once_in_order([message["content"] for message in reader.messages], expected)


def test_stale_snapshot_save_does_not_lose_interleaved_logger_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stale save must refuse or merge when a real JSONL append races it."""
    conversations_dir = tmp_path / "conversations"
    logger = KollaborConversationLogger(conversations_dir)
    logger.reset_session(SESSION_ID)
    writer = _manager(conversations_dir, logger)

    first_user = writer.add_message("user", BASE_USER)
    writer.add_message("assistant", BASE_ASSISTANT, parent_uuid=first_user)
    assert writer.save_session(SESSION_ID) is True

    # This manager now holds a later local snapshot that is not yet on disk.
    writer.add_message("user", SECOND_SAVE)
    session_file = logger.session_file
    before_interleaved_append = session_file.read_bytes()
    rewrite_lock = conversation_manager_module._session_file_rewrite_lock_path(session_file)
    rewrite_attempted = Event()
    allow_rewrite = Event()
    real_locked_file = conversation_manager_module.locked_file

    def pause_before_rewrite_lock(lock_path: str | Path, mode: str = "a+", *, private: bool = False):
        if Path(lock_path) == rewrite_lock:
            # save_session has already built its snapshot, but hasn't acquired
            # either lock or truncated the file yet.
            rewrite_attempted.set()
            if not allow_rewrite.wait(timeout=10):
                raise TimeoutError("test did not release the snapshot save")
        return real_locked_file(lock_path, mode, private=private)

    monkeypatch.setattr(conversation_manager_module, "locked_file", pause_before_rewrite_lock)

    with ThreadPoolExecutor(max_workers=1) as executor:
        save_future = executor.submit(writer.save_session, SESSION_ID)
        if not rewrite_attempted.wait(timeout=10):
            allow_rewrite.set()
            try:
                save_future.result(timeout=10)
            except Exception as exc:
                pytest.fail(f"save failed before rewrite barrier: {exc!r}")
            pytest.fail("save_session never reached the rewrite-lock barrier")

        try:
            # Only optional branch metadata is synthetic; the real logger append
            # still runs. The autouse fixture blocks any other child process.
            monkeypatch.setattr(logger, "_get_git_branch", lambda: "synthetic-test-branch")
            asyncio.run(logger.log_user_message(INTERLEAVED_TAIL))
            bytes_with_interleaved_append = session_file.read_bytes()
            assert bytes_with_interleaved_append != before_interleaved_append
            assert any(
                json.loads(line).get("message", {}).get("content") == INTERLEAVED_TAIL
                for line in bytes_with_interleaved_append.decode().splitlines()
            )
        finally:
            allow_rewrite.set()

        save_result = save_future.result(timeout=10)

    assert isinstance(save_result, bool)
    if not save_result:
        # A refusal is safe only when it leaves the logger's append intact.
        assert session_file.read_bytes() == bytes_with_interleaved_append

    if save_result:
        # The local snapshot row predates the logger append; success must keep
        # both exactly once in that order.
        expected = [BASE_USER, BASE_ASSISTANT, SECOND_SAVE, INTERLEAVED_TAIL]
    else:
        # Refusal leaves the appended JSONL intact and does not claim the local
        # candidate was saved.
        expected = [BASE_USER, BASE_ASSISTANT, INTERLEAVED_TAIL]

    _assert_exactly_once_in_order(_jsonl_canary_contents(session_file), expected)

    reader = _manager(conversations_dir)
    assert reader.load_session(SESSION_ID) is True
    _assert_exactly_once_in_order([message["content"] for message in reader.messages], expected)
