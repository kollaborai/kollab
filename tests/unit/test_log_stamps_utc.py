"""Logged stamps labeled "Z" must be true UTC (issue #454)."""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

from kollabor_ai.conversation_logger import KollaborConversationLogger, utc_stamp


def _assert_utc_now_stamp(stamp: str) -> None:
    assert stamp.endswith("Z")
    assert "+00:00" not in stamp
    parsed = datetime.fromisoformat(stamp)
    assert parsed.tzinfo is not None
    drift = abs((parsed - datetime.now(timezone.utc)).total_seconds())
    assert drift < 5, f"stamp {stamp!r} drifts {drift:.1f}s from UTC now"


def test_utc_stamp_helper_returns_z_suffixed_utc():
    _assert_utc_now_stamp(utc_stamp())


def test_utc_stamp_converts_naive_local_moment():
    local_wall = datetime.now().astimezone().replace(tzinfo=None)
    _assert_utc_now_stamp(utc_stamp(local_wall))


def test_logged_user_entry_timestamp_is_utc(tmp_path: Path):
    conversations = tmp_path / "conversations"
    conversation_logger = KollaborConversationLogger(conversations)

    asyncio.run(conversation_logger.log_user_message("hello stamps"))

    session_file = conversations / f"{conversation_logger.session_id}.jsonl"
    records = [
        json.loads(line)
        for line in session_file.read_text().splitlines()
        if line.strip()
    ]
    user_records = [record for record in records if record.get("type") == "user"]
    assert user_records, f"no user record in {session_file}"
    _assert_utc_now_stamp(user_records[0]["timestamp"])
