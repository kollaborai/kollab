"""Tests for compact tool result summaries."""

from types import SimpleNamespace

from kollabor_tui.tool_display import extract_tool_name_args, get_tool_result_summary


def test_successful_tool_output_that_starts_with_error_summarizes_as_error():
    result = SimpleNamespace(
        success=True,
        tool_type="mcp_tool",
        output="Error: MENTIKO_SESSION_TOKEN not set -- session auth required",
        error="",
    )

    assert get_tool_result_summary(result) == (
        "Error: MENTIKO_SESSION_TOKEN not set -- session auth required"
    )


def test_native_read_file_args_display_as_file_path():
    result = SimpleNamespace(
        success=True,
        tool_type="mcp_tool",
        tool_id="call_123",
        output="contents",
        error="",
    )
    tool_data = {
        "name": "read_file",
        "arguments": {"path": "packages/kollabor-engine/src/kollabor_engine/auth.py"},
    }

    assert extract_tool_name_args(result, tool_data) == (
        "read_file",
        "packages/kollabor-engine/src/kollabor_engine/auth.py",
    )
