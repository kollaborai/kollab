"""Durable queue handoff uses the canonical conversation and media stores."""

from __future__ import annotations

import json

import pytest
from kollabor_engine import media_store
from kollabor_engine.history_store import _wire_message
from kollabor_engine.media_store import save
from kollabor_ai.conversation_logger import KollaborConversationLogger


PNG = "data:image/png;base64,AQID"
JPEG = "data:image/jpeg;base64,BAUF"


@pytest.mark.asyncio
async def test_queue_handoff_metadata_survives_jsonl_and_history_reload(tmp_path):
    logger = KollaborConversationLogger(tmp_path / "conversations")
    logger.reset_session("conversation-a")
    handoff = {
        "version": 1,
        "conversation_uid": "conversation-a",
        "conversation_revision": 9,
        "request_id": "queue-request-a",
        "source_attachment_refs": ["stage-a"],
        "media_refs": [{"source_ref": "stage-a", "ref": "durable-a"}],
    }

    row_uuid = await logger.log_user_message(
        "inspect this image [image1]",
        message_metadata={"queue_handoff": handoff},
    )
    raw_row = next(
        json.loads(line)
        for line in logger.session_file.read_text().splitlines()
        if json.loads(line).get("uuid") == row_uuid
    )

    assert raw_row["sessionId"] == "conversation-a"
    assert raw_row["uuid"] == row_uuid
    assert raw_row["message"]["metadata"]["queue_handoff"] == handoff
    history_row = _wire_message(raw_row)
    assert history_row is not None
    assert history_row["metadata"]["queue_handoff"] == handoff


def test_media_history_binds_queue_refs_by_request_identity_not_append_order(tmp_path):
    first = save(tmp_path, "conversation-a", PNG)
    second = save(tmp_path, "conversation-a", JPEG)
    media_store.bind_queue_handoff(
        tmp_path,
        "conversation-a",
        request_id="queue-request-a",
        history_row_uuid="row-a",
        source_media_refs=(("stage-a", second.ref),),
    )
    media_store.bind_queue_handoff(
        tmp_path,
        "conversation-a",
        request_id="queue-request-b",
        history_row_uuid="row-b",
        source_media_refs=(("stage-b", first.ref),),
    )
    rows = [
        {
            "uuid": "row-b",
            "sessionId": "conversation-a",
            "role": "user",
            "content": "second [image1]",
            "metadata": {"queue_handoff": {"request_id": "queue-request-b"}},
        },
        {
            "uuid": "row-a",
            "sessionId": "conversation-a",
            "role": "user",
            "content": "first [image1]",
            "metadata": {"queue_handoff": {"request_id": "queue-request-a"}},
        },
    ]

    media_store.attach_media_to_rows(tmp_path, "conversation-a", rows)

    assert [item["ref"] for item in rows[0]["media"]] == [first.ref]
    assert [item["ref"] for item in rows[1]["media"]] == [second.ref]


def test_queue_handoff_refuses_legacy_media_without_a_digest(tmp_path):
    record = save(tmp_path, "conversation-a", PNG)
    index_path = tmp_path / "media" / "conversation-a" / "index.json"
    entries = json.loads(index_path.read_text())
    entries[-1].pop("sha256")
    index_path.write_text(json.dumps(entries))

    with pytest.raises(ValueError, match="digest|integrity"):
        media_store.bind_queue_handoff(
            tmp_path,
            "conversation-a",
            request_id="queue-request-a",
            history_row_uuid="row-a",
            source_media_refs=(("stage-a", record.ref),),
        )
