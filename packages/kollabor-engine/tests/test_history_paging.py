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
    history = [
        _message(index) for index in range(HISTORY_DEFAULT_PAGE_SIZE - 1)
    ] + [{"role": "tool", "content": original, "metadata": {"tool_call_id": "call-1"}}]

    page = build_history_page("session-1", history)
    tool_message = page["history"][-1]

    assert HISTORY_MAX_PAGE_BYTES == 150_000
    assert _wire_size(page) <= HISTORY_MAX_PAGE_BYTES
    assert tool_message["metadata"]["history_output_truncated"] is True
    ref = tool_message["metadata"]["history_output_ref"]
    assert len(tool_message["content"]) < len(original)
    assert resolve_history_output("session-1", history, ref) == original


def test_tool_output_ref_is_session_bound():
    history = [{"role": "tool", "content": "secret output " + ("x" * 200_000), "metadata": {}}]
    page = build_history_page("session-1", history)
    ref = page["history"][-1]["metadata"].get("history_output_ref")
    assert ref is not None

    with pytest.raises(ValueError):
        resolve_history_output("another-session", history, ref)
