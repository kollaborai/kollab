from __future__ import annotations

import json
from types import SimpleNamespace

from kollabor.state.chat_stream import ChatStreamService
from kollabor.state.chat_stream_rows import bounded_chat_stream_row


def _state():
    return SimpleNamespace(
        _llm_service=SimpleNamespace(
            conversation_manager=None,
            conversation_history=[],
        ),
        get_conversation_uid=lambda: "permission-budget-test",
    )


def test_large_permission_prompt_stays_actionable_and_full_payload_is_fetchable():
    service = ChatStreamService(_state())
    uid = "permission-budget-test"
    details = {
        "tool_id": "tool-large-command",
        "permission_id": "permission-large-command",
        "request_id": "request-large-command",
        "source": "acp",
        "parked": False,
        "tool_name": "request_permission",
        "tool_type": "terminal",
        "command": "run " + ("sensitive-command-argument " * 20_000),
        "file_path": "/workspace/important-file",
        "acp_options": [
            {"optionId": "allow", "name": "Allow once", "kind": "allow_once"},
            {"optionId": "deny", "name": "Deny", "kind": "reject_once"},
        ],
    }

    service._on_permission_prompt(
        uid,
        {
            "type": "permission_request",
            "tool_id": details["tool_id"],
            "permission_id": details["permission_id"],
            "details": details,
        },
    )
    event = service._log(uid)._events[-1]
    projected = event["data"]

    assert event["type"] == "row.upsert"
    assert projected["kind"] == "prompt"
    assert projected["status"] == "open"
    assert len(projected["parts"]) == 1
    prompt = projected["parts"][0]
    assert prompt["type"] == "prompt"
    assert prompt["prompt_id"] == details["permission_id"]
    assert prompt["data"]["permission_id"] == details["permission_id"]
    assert prompt["data"]["tool_id"] == details["tool_id"]
    assert prompt["data"]["request_id"] == details["request_id"]
    assert prompt["data"]["acp_options"] == details["acp_options"]
    assert len(prompt["data"]["command"]) < 16_384

    marker = prompt["chat_stream_part_preview"]
    response = service.get_content(
        {
            "row_id": projected["id"],
            "part_index": marker["part_index"],
            "field": "part",
            "output_revision": marker["output_revision"],
            "offset": 0,
            "limit": 32_768,
        }
    )
    assert response["output_type"] == "json"
    assert json.loads(response["content"])["data"]["command"] == details["command"]


def test_compact_fallback_preserves_non_string_text_parts_and_original_indexes():
    parts = [
        {"type": "text", "text": f"part {index}:" + ("x" * 10_000)}
        for index in range(30)
    ]
    unusual_part = {"type": "text", "text": {"opaque": "structured"}}
    parts.insert(17, unusual_part)
    row = {
        "id": "row-index-preservation",
        "seq": 1,
        "kind": "reply",
        "role": "assistant",
        "turn_id": "turn-index-preservation",
        "status": "completed",
        "parts": parts,
        "created_at": "2026-11-01T00:00:00Z",
    }

    projected = bounded_chat_stream_row(row)

    assert len(projected["parts"]) == len(parts)
    assert projected["parts"][17] == unusual_part
    assert projected["parts"][18]["text"].startswith("part 17:")

