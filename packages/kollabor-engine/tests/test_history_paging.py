"""Contract tests for bounded, resumable session history pages."""

import json

import pytest
from kollabor_engine import history_paging
from kollabor_engine.history_paging import (
    HISTORY_DEFAULT_PAGE_SIZE,
    HISTORY_MAX_PAGE_BYTES,
    HISTORY_MAX_PAGE_SIZE,
    StaleHistoryCursor,
    build_history_page,
    load_history_cursor_secret,
    resolve_history_output,
)


@pytest.fixture(autouse=True)
def _isolate_cursor_secret(monkeypatch):
    monkeypatch.setattr(history_paging, "_CURSOR_SECRET", b"x" * 32)


def _message(index: int, role: str = "user", content: str | None = None):
    return {
        "role": role,
        "content": content if content is not None else f"message-{index}",
        "metadata": {"source": "synthetic"},
    }


def _wire_size(value: object) -> int:
    return len(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )


def test_history_cursor_key_persists_with_private_file_permissions(tmp_path):
    key_path = tmp_path / ".kollab" / "history-cursor.key"

    first = load_history_cursor_secret(key_path)
    second = load_history_cursor_secret(key_path)

    assert len(first) == 32
    assert second == first
    assert key_path.read_bytes() == first
    assert key_path.stat().st_mode & 0o777 == 0o600


def test_history_cursor_key_rejects_corruption_instead_of_rotating(tmp_path):
    key_path = tmp_path / "history-cursor.key"
    key_path.write_bytes(b"short")

    with pytest.raises(RuntimeError, match="exactly 32 bytes"):
        load_history_cursor_secret(key_path)

    assert key_path.read_bytes() == b"short"


def test_default_page_is_newest_fifty_in_chronological_order():
    history = [_message(index) for index in range(105)]

    page = build_history_page("session-1", history)

    assert HISTORY_DEFAULT_PAGE_SIZE == 50
    assert [item["content"] for item in page["history"]] == [
        f"message-{index}" for index in range(55, 105)
    ]
    assert page["has_more_older"] is True
    assert page["older_cursor"]
    assert page["newer_cursor"]


def test_page_size_is_capped_at_one_hundred():
    history = [_message(index) for index in range(130)]

    page = build_history_page("session-1", history, limit=10_000)

    assert HISTORY_MAX_PAGE_SIZE == 100
    assert len(page["history"]) == 100
    assert page["history"][0]["content"] == "message-30"


def test_older_cursor_returns_adjacent_chronological_page():
    history = [_message(index) for index in range(105)]
    recent = build_history_page("session-1", history)

    older = build_history_page("session-1", history, before=recent["older_cursor"])

    assert [item["content"] for item in older["history"]] == [
        f"message-{index}" for index in range(5, 55)
    ]
    assert older["has_more_older"] is True


def test_newer_cursor_fetches_only_messages_appended_since_cache():
    history = [_message(index) for index in range(4)]
    cached_page = build_history_page("session-1", history)
    history.extend([_message(4), _message(5)])

    newer = build_history_page("session-1", history, after=cached_page["newer_cursor"])

    assert [item["content"] for item in newer["history"]] == ["message-4", "message-5"]
    assert newer["has_more_newer"] is False


def test_after_cursor_returns_latest_new_messages_with_older_cursor_for_backlog():
    history = [_message(index) for index in range(100)]
    cached_page = build_history_page("session-1", history)
    history.extend(_message(index) for index in range(100, 220))

    newer = build_history_page(
        "session-1", history, after=cached_page["newer_cursor"], limit=50
    )

    assert [item["content"] for item in newer["history"]] == [
        f"message-{index}" for index in range(170, 220)
    ]
    assert newer["has_more_older"] is True
    assert newer["older_cursor"]
    assert newer["has_more_newer"] is False


def test_cursor_rejects_rewritten_history_with_actionable_stale_error():
    history = [_message(index) for index in range(105)]
    first = build_history_page("session-1", history)
    history[0] = _message(0, content="compacted replacement")

    with pytest.raises(StaleHistoryCursor):
        build_history_page("session-1", history, before=first["older_cursor"])


def test_150kb_budget_truncates_a_huge_tool_result_and_show_more_fetches_it():
    original = "tool-output-" + ("x" * 600_000)
    history = [_message(index) for index in range(HISTORY_DEFAULT_PAGE_SIZE - 1)] + [
        {"role": "tool", "content": original, "metadata": {"tool_call_id": "call-1"}}
    ]

    page = build_history_page("session-1", history)
    tool_message = page["history"][-1]

    assert HISTORY_MAX_PAGE_BYTES == 150_000
    assert _wire_size(page) <= HISTORY_MAX_PAGE_BYTES
    assert tool_message["metadata"]["history_output_truncated"] is True
    ref = tool_message["metadata"]["history_output_ref"]
    assert len(tool_message["content"]) < len(original)
    assert resolve_history_output("session-1", history, ref) == original


def test_page_copy_drops_full_file_diffs_from_tool_batches():
    huge = "x" * 600_000
    entry = {
        "id": "tool-1",
        "content": "1 file edited",
        "tool_execution_time": 0.5,
        "metadata": {
            "diff_info": {"count": 1, "lines": [42]},
            "diffs": [
                {
                    "path": "/repo/big_file.py",
                    "old_text": huge,
                    "new_text": huge,
                }
            ],
        },
    }
    message = {
        "role": "user",
        "content": "Tool result: 1 file edited",
        "metadata": {"tool_output_batch": True, "xml_tool_results": [entry]},
    }
    history = [_message(index) for index in range(HISTORY_DEFAULT_PAGE_SIZE - 1)] + [
        message
    ]

    page = build_history_page("session-1", history)

    assert _wire_size(page) <= HISTORY_MAX_PAGE_BYTES
    paged = page["history"][-1]
    paged_entry = paged["metadata"]["xml_tool_results"][0]
    assert "diffs" not in paged_entry["metadata"]
    assert paged_entry["metadata"]["diff_info"] == {"count": 1, "lines": [42]}
    assert paged_entry["content"] == "1 file edited"
    # The mirror itself keeps the full diffs for the model and later turns.
    assert (
        history[-1]["metadata"]["xml_tool_results"][0]["metadata"]["diffs"][0][
            "old_text"
        ]
        == huge
    )
    # Dropping diffs from the copy cannot invalidate cursors: they digest
    # the original history, so the older cursor keeps paging.
    older = build_history_page("session-1", history, before=page["older_cursor"])
    assert older["history"]


def test_page_copy_drops_diffs_from_tool_result_metadata():
    huge = "y" * 600_000
    message = {
        "role": "system",
        "content": "Executed file_edit",
        "metadata": {
            "tool_result": {
                "id": "tool-2",
                "content": "1 file edited",
                "metadata": {
                    "diff_info": {"count": 1, "lines": [7]},
                    "diffs": [
                        {"path": "/repo/other.py", "old_text": huge, "new_text": huge}
                    ],
                },
            }
        },
    }
    history = [_message(index) for index in range(HISTORY_DEFAULT_PAGE_SIZE - 1)] + [
        message
    ]

    page = build_history_page("session-1", history)

    assert _wire_size(page) <= HISTORY_MAX_PAGE_BYTES
    tool_result = page["history"][-1]["metadata"]["tool_result"]
    assert "diffs" not in tool_result["metadata"]
    assert tool_result["metadata"]["diff_info"] == {"count": 1, "lines": [7]}
    assert (
        history[-1]["metadata"]["tool_result"]["metadata"]["diffs"][0]["new_text"]
        == huge
    )


@pytest.mark.parametrize("role", ["user", "assistant", "system"])
def test_150kb_budget_truncates_oversized_message_of_any_role(role):
    original = f"{role} wall of text " + ("z" * 600_000)
    history = [_message(index) for index in range(HISTORY_DEFAULT_PAGE_SIZE - 1)] + [
        {"role": role, "content": original, "metadata": {"source": "synthetic"}}
    ]

    page = build_history_page("session-1", history)
    paged = page["history"][-1]

    assert _wire_size(page) <= HISTORY_MAX_PAGE_BYTES
    assert paged["metadata"]["history_output_truncated"] is True
    assert len(paged["content"]) < len(original)
    assert (
        resolve_history_output(
            "session-1", history, paged["metadata"]["history_output_ref"]
        )
        == original
    )


def test_10kb_tool_result_pages_as_preview_with_ref_even_when_page_has_room():
    original = "x" * 10_000
    history = [
        _message(0),
        _message(1),
        {"role": "tool", "content": original, "metadata": {"tool_call_id": "call-9"}},
    ]

    page = build_history_page("session-1", history, tool_output_preview=True)

    tool_message = page["history"][-1]
    assert _wire_size(page) < HISTORY_MAX_PAGE_BYTES
    assert tool_message["metadata"]["history_output_truncated"] is True
    assert tool_message["metadata"]["history_output_bytes"] == 10_000
    assert len(tool_message["content"]) < len(original)
    assert (
        resolve_history_output(
            "session-1", history, tool_message["metadata"]["history_output_ref"]
        )
        == original
    )
    # Without the preview switch (Trajectory), the same tool result stays whole.
    full_page = build_history_page("session-1", history)
    assert full_page["history"][-1]["content"] == original
    # A small tool result stays whole: only over-budget output is deferred.
    small = {"role": "tool", "content": "x" * 100, "metadata": {"tool_call_id": "c"}}
    small_page = build_history_page(
        "session-2", [_message(0), small], tool_output_preview=True
    )
    assert small_page["history"][-1]["content"] == "x" * 100
    assert "history_output_truncated" not in small_page["history"][-1]["metadata"]


def test_oversized_batched_results_message_loads_light_with_ref():
    # The other shape the web UI shows as tool results: role=user batches.
    original = "y" * 10_000
    history = [
        _message(0),
        {
            "role": "user",
            "content": f"Tool result: {original}",
            "metadata": {"tool_output_batch": True, "xml_tool_results": []},
        },
    ]

    page = build_history_page("session-1", history, tool_output_preview=True)

    batched = page["history"][-1]
    assert batched["metadata"]["history_output_truncated"] is True
    assert len(batched["content"]) < len(original)
    assert (
        resolve_history_output(
            "session-1", history, batched["metadata"]["history_output_ref"]
        )
        == f"Tool result: {original}"
    )
    # A plain oversized user message is not a tool result: it stays whole
    # unless the page budget forces the over-cap truncation.
    plain = build_history_page(
        "session-2",
        [_message(0), {"role": "user", "content": original, "metadata": {}}],
        tool_output_preview=True,
    )
    assert plain["history"][-1]["content"] == original


def test_tool_output_ref_is_session_bound():
    history = [
        {"role": "tool", "content": "secret output " + ("x" * 200_000), "metadata": {}}
    ]
    page = build_history_page("session-1", history)
    ref = page["history"][-1]["metadata"].get("history_output_ref")
    assert ref is not None

    with pytest.raises(ValueError):
        resolve_history_output("another-session", history, ref)


def _acp_reply_message(acp_updates):
    """The Claude ACP reply shape: small text, huge streaming-update list."""
    return {
        "role": "assistant",
        "content": "done",
        "metadata": {
            "provider": "acp",
            "profile": "claude-code",
            "acp_updates": acp_updates,
            "stop_reason": "end_turn",
        },
    }


def test_472kb_acp_updates_page_light_with_dropped_marker():
    updates = [{"kind": "text", "text": "x" * 118}] * 4_000  # ~472 KB
    history = [
        _message(0),
        {"role": "assistant", "content": "earlier", "metadata": {}},
        _acp_reply_message(updates),
    ]

    page = build_history_page("session-1", history, tool_output_preview=True)

    reply = page["history"][-1]
    assert _wire_size(page) <= HISTORY_MAX_PAGE_BYTES
    assert reply["metadata"]["history_output_truncated"] is True
    assert reply["metadata"]["acp_updates_dropped"] == 4_000
    assert "acp_updates" not in reply["metadata"]
    assert reply["metadata"]["provider"] == "acp"
    assert reply["content"].startswith("done")


def test_oversized_acp_message_pages_to_the_top_without_page_too_large():
    updates = [{"kind": "text", "text": "y" * 118}] * 4_000
    history = [
        _acp_reply_message([{"kind": "text", "text": f"m{index}"}]) if index % 2
        else _message(index)
        for index in range(40)
    ] + [_acp_reply_message(updates), _message(999)]

    cursor = None
    pages = 0
    while True:
        page = build_history_page("session-1", history, before=cursor)
        pages += 1
        assert _wire_size(page) <= HISTORY_MAX_PAGE_BYTES
        if not page["has_more_older"]:
            break
        cursor = page["older_cursor"]
    assert pages > 1
    assert pages < 20  # bounded walk, no runaway


def test_small_acp_updates_stay_whole_in_the_page_copy():
    updates = [{"kind": "text", "text": "hello"}]
    history = [_acp_reply_message(updates)]

    page = build_history_page("session-1", history, tool_output_preview=True)

    reply = page["history"][-1]
    assert reply["metadata"]["acp_updates"] == updates
    assert "acp_updates_dropped" not in reply["metadata"]


def test_472kb_acp_updates_truncates_in_default_form_too():
    updates = [{"kind": "text", "text": "x" * 118}] * 4_000
    history = [_acp_reply_message(updates)]

    page = build_history_page("session-1", history)

    reply = page["history"][-1]
    assert reply["metadata"]["history_output_truncated"] is True
    assert reply["metadata"]["acp_updates_dropped"] == 4_000
    assert _wire_size(page) <= HISTORY_MAX_PAGE_BYTES
