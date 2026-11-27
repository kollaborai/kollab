"""A queued web message's terminal outcome reaches its saved history row (#1451).

The daemon queue writes the admission row with the request id inside
``queue_handoff`` only. The turn's outcome update names the row by its uuid and
``request_id``; it must find that row, or a reload shows a cancelled message as
completed.
"""

import asyncio
import json
from pathlib import Path

from kollabor_ai.conversation_logger import KollaborConversationLogger


def _write_row(path: Path, row_uuid: str, metadata: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "type": "user",
        "uuid": row_uuid,
        "message": {"role": "user", "content": "hello", "metadata": metadata},
    }
    path.write_text(json.dumps(record) + "\n", encoding="utf-8")


def _metadata(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8").splitlines()[0])["message"]["metadata"]


def test_outcome_update_finds_a_queue_handoff_row(tmp_path):
    logger = KollaborConversationLogger(tmp_path)
    path = Path(logger.session_file)
    handoff = {"version": 1, "conversation_uid": "c", "conversation_revision": 0,
               "request_id": "web-1", "force_send": False,
               "source_attachment_refs": [], "media_refs": []}
    _write_row(path, "row-1", {"queue_handoff": handoff})

    ok = asyncio.run(logger.update_user_message_metadata(
        "row-1",
        expected_metadata={"request_id": "web-1"},
        updates={"chat_stream_outcome": "cancelled"},
    ))

    assert ok is True
    assert _metadata(path)["chat_stream_outcome"] == "cancelled"


def test_outcome_update_still_refuses_another_requests_handoff_row(tmp_path):
    logger = KollaborConversationLogger(tmp_path)
    path = Path(logger.session_file)
    _write_row(path, "row-1", {"queue_handoff": {"request_id": "web-other"}})

    ok = asyncio.run(logger.update_user_message_metadata(
        "row-1",
        expected_metadata={"request_id": "web-1"},
        updates={"chat_stream_outcome": "cancelled"},
    ))

    assert ok is False
    assert "chat_stream_outcome" not in _metadata(path)
