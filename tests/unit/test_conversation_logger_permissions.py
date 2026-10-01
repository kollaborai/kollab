"""Conversation transcripts are private: 0700 dirs, 0600 session files."""

import asyncio
import json
import os
import stat

from kollabor_ai.conversation_logger import KollaborConversationLogger


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_new_logger_creates_private_dirs_and_files(tmp_path):
    conversations = tmp_path / "conversations"
    logger = KollaborConversationLogger(conversations)
    message = {"type": "conversation_start", "content": "hello"}
    asyncio.run(logger._append_to_jsonl(message))

    assert _mode(conversations) & 0o077 == 0
    assert _mode(conversations / "memory") & 0o077 == 0
    assert _mode(conversations / "raw") & 0o077 == 0
    assert _mode(logger.session_file) & 0o077 == 0
    assert json.loads(logger.session_file.read_text().splitlines()[0]) == message


def test_existing_loose_modes_are_tightened_on_init(tmp_path):
    conversations = tmp_path / "conversations"
    conversations.mkdir(mode=0o755)
    (conversations / "raw").mkdir(mode=0o755)
    (conversations / "memory").mkdir(mode=0o755)
    old_session = conversations / "session_old.jsonl"
    old_session.write_text("{}\n")
    old_session.chmod(0o644)
    old_raw = conversations / "raw" / "resp.jsonl"
    old_raw.write_text("{}\n")
    old_raw.chmod(0o644)

    KollaborConversationLogger(conversations)

    assert _mode(conversations) & 0o077 == 0
    assert _mode(conversations / "raw") & 0o077 == 0
    assert _mode(conversations / "memory") & 0o077 == 0
    assert _mode(old_session) & 0o077 == 0
    assert _mode(old_raw) & 0o077 == 0
