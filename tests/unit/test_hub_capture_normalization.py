from kollabor_tui.display_tap import DisplayTap
from plugins.hub.capture import normalize_records, normalize_text, paginate


def test_normalize_text_strips_ansi_osc_controls_and_cr_redraw():
    assert normalize_text("old\rnew\x1b[31m red\x1b[0m\x1b]0;title\x07\x07") == "new red"


def test_structured_tool_records_preserve_payload_and_status():
    records = normalize_records([
        {
            "type": "tool_start",
            "seq": 9,
            "ts": 1700000000,
            "tool_name": "file_read",
            "input": {"path": "x\x1b[31m"},
        },
        {
            "type": "tool_result",
            "seq": 10,
            "tool_name": "file_read",
            "output": "Read 2 lines",
            "success": True,
        },
        {"type": "future_event", "text": "hello"},
    ])
    assert [item["kind"] for item in records] == ["tool-call", "tool-result", "text"]
    assert records[0]["tool"] == "file_read"
    assert records[0]["payload"]["input"]["path"] == "x"
    assert records[1]["payload"]["output"] == "Read 2 lines"
    assert records[1]["status"] == "ok"
    assert records[0]["timestamp"].endswith("Z")


def test_display_tap_assigns_monotonic_sequence_numbers():
    tap = DisplayTap()
    tap.publish({"type": "token", "text": "one"})
    tap.publish({"type": "token", "text": "two", "seq": 999})
    snapshot = tap.get_snapshot()
    assert [event["seq"] for event in snapshot] == [1, 2]


def test_pagination_cursor_continuity_and_serialized_char_limit():
    records = normalize_records([
        {"seq": 10, "type": "text", "text": "one", "metadata": "x" * 40},
        {"seq": 20, "type": "text", "text": "two", "metadata": "y" * 40},
        {"seq": 30, "type": "text", "text": "three", "metadata": "z" * 40},
    ])
    page, truncated, cursor = paginate(records, max_chars=130)
    assert [item["seq"] for item in page] == [10]
    assert truncated is True
    assert cursor == "10"
    page, truncated, cursor = paginate(records, cursor=cursor, max_chars=130)
    assert [item["seq"] for item in page] == [20]
    assert truncated is True
    assert cursor == "20"
