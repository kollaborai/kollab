"""Synthetic file and parent-directory sync checks for input receipts."""

import os
import stat

import pytest

from kollabor_ai.conversation_logger import KollaborConversationLogger


def synthetic_logger(tmp_path):
    logger = KollaborConversationLogger(tmp_path)
    logger.session_id = "synthetic-durability"
    logger.session_file = tmp_path / "synthetic-durability.jsonl"
    logger._get_working_directory = lambda: "/synthetic"
    logger._get_git_branch = lambda: "synthetic"
    logger._get_session_context = lambda: {}
    logger._get_project_awareness = lambda: {}
    logger._analyze_user_context = lambda _: {}
    logger._save_conversation_memory = lambda: None
    return logger


@pytest.mark.asyncio
async def test_new_user_log_syncs_file_and_parent_before_receipt(tmp_path, monkeypatch):
    logger = synthetic_logger(tmp_path)
    real_fsync = os.fsync
    calls = []

    def observed(fd):
        calls.append("directory" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", observed)
    row = await logger.log_user_message("synthetic")
    assert calls == ["file", "directory"]
    assert row.sync_confirmed is True and row.session_id == logger.session_id


@pytest.mark.asyncio
async def test_directory_sync_unsupported_withholds_receipt_not_log(tmp_path, monkeypatch):
    logger = synthetic_logger(tmp_path)
    real_fsync = os.fsync

    def unsupported_directory(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode):
            raise OSError("synthetic unsupported directory sync")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", unsupported_directory)
    row = await logger.log_user_message("synthetic")
    assert row.sync_confirmed is False
    assert "synthetic" in logger.session_file.read_text()


@pytest.mark.asyncio
async def test_failed_file_sync_rolls_back_and_syncs_original(tmp_path, monkeypatch):
    logger = synthetic_logger(tmp_path)
    logger.session_file.write_bytes(b"existing synthetic\n")
    before = logger.session_file.read_bytes()
    real_fsync = os.fsync
    count = 0

    def fail_once(fd):
        nonlocal count
        count += 1
        if count == 1:
            raise OSError("synthetic append sync failure")
        return real_fsync(fd)

    monkeypatch.setattr(os, "fsync", fail_once)
    with pytest.raises(OSError, match="synthetic append sync failure"):
        await logger.log_user_message("synthetic")
    assert count == 2 and logger.session_file.read_bytes() == before


@pytest.mark.asyncio
async def test_failed_rollback_sync_reports_indeterminate(tmp_path, monkeypatch):
    logger = synthetic_logger(tmp_path)
    logger.session_file.write_bytes(b"existing synthetic\n")
    before = logger.session_file.read_bytes()

    def fail_sync(_fd):
        raise OSError("synthetic fsync failure")

    monkeypatch.setattr(os, "fsync", fail_sync)
    with pytest.raises(OSError, match="rollback is indeterminate"):
        await logger.log_user_message("synthetic")
    assert logger.session_file.read_bytes() == before


@pytest.mark.asyncio
async def test_same_session_reset_rejects_waiting_append(tmp_path):
    import asyncio

    logger = synthetic_logger(tmp_path)
    session_id = logger.session_id
    await logger._write_lock.acquire()
    task = asyncio.create_task(logger.log_user_message("synthetic waiting"))
    await asyncio.sleep(0)
    logger.reset_session(session_id)
    logger._write_lock.release()
    with pytest.raises(OSError, match="session replaced"):
        await task
    assert not logger.session_file.exists()
