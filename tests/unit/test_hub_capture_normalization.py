from plugins.hub.capture import normalize_records, normalize_text, paginate


def test_normalize_text_strips_ansi_osc_controls_and_cr_redraw():
    assert normalize_text("old\rnew\x1b[31m red\x1b[0m\x1b]0;title\x07\x07") == "new red"


def test_structured_tool_records_and_unknown_fallback():
    records = normalize_records([
        {"type": "tool_start", "ts": 1700000000, "tool": "file_read", "text": "\x1b[32mRead x"},
        {"type": "tool_result", "tool": "file_read", "text": "Read 2 lines", "status": "ok"},
        {"type": "future_event", "text": "hello"},
    ])
    assert [item["kind"] for item in records] == ["tool-call", "tool-result", "text"]
    assert records[0]["tool"] == "file_read"
    assert records[0]["timestamp"].endswith("Z")


def test_pagination_is_before_char_limit_and_returns_cursor():
    records = normalize_records(["one", "two", "three"])
    page, truncated, cursor = paginate(records, max_chars=7)
    assert [item["text"] for item in page] == ["one", "two"]
    assert truncated is True
    assert cursor == "2"
