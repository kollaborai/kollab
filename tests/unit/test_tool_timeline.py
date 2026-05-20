"""Golden tests for tool timeline event contract."""

from kollabor_agent.tool_timeline import ToolTimeline, ToolTimelineEvent


def test_tool_timeline_event_shape_is_stable():
    event = ToolTimelineEvent(
        phase="started",
        tool_id="tool-1",
        tool_type="mcp_tool",
        detail="doctor_ping",
        success=None,
        timestamp=123.4,
        metadata={"server": "doctor-mock"},
    )

    assert event.to_dict() == {
        "phase": "started",
        "tool_id": "tool-1",
        "tool_type": "mcp_tool",
        "detail": "doctor_ping",
        "timestamp": 123.4,
        "metadata": {"server": "doctor-mock"},
    }


def test_tool_timeline_records_replayable_sequence():
    timeline = ToolTimeline()

    timeline.record_phase(
        "registered",
        tool_id="doctor_ping",
        tool_type="mcp_tool",
        detail="doctor-mock",
    )
    timeline.record_phase(
        "started",
        tool_id="call-1",
        tool_type="mcp_tool",
        detail="doctor_ping",
    )
    timeline.record_phase(
        "result",
        tool_id="call-1",
        tool_type="mcp_tool",
        detail="ok",
        success=True,
    )

    events = timeline.to_dicts()
    assert [event["phase"] for event in events] == [
        "registered",
        "started",
        "result",
    ]
    assert events[-1]["success"] is True
