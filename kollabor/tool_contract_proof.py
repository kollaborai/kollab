"""Stable proof surface for tool-call normalization used by /doctor."""

from __future__ import annotations

from types import SimpleNamespace

from kollabor_agent.tool_call_contract import normalize_native_tool_call
from kollabor_ai.response_parser import ResponseParser


def collect_tool_contract_proofs() -> list[tuple[str, str]]:
    """Validate XML, mock MCP, and native tool-call normalization contracts."""
    parser = ResponseParser()
    parsed = parser.parse_response(
        "<read><file>pyproject.toml</file></read>\n"
        '<tool name="doctor_ping" mode="mock">ok</tool>'
    )
    tools = parser.get_all_tools(parsed)
    by_type = {tool["type"]: tool for tool in tools}
    if by_type.get("file_read", {}).get("file") != "pyproject.toml":
        raise ValueError("xml file_read contract did not normalize")

    mock_mcp = by_type.get("mcp_tool", {})
    if (
        mock_mcp.get("name") != "doctor_ping"
        or mock_mcp.get("arguments", {}).get("mode") != "mock"
    ):
        raise ValueError("mock MCP contract did not normalize")

    native = normalize_native_tool_call(
        SimpleNamespace(
            id="doctor_native",
            name="state_update",
            input={"state": "doctor-ok"},
        ),
        plugin_handler_names={"state_update"},
    )
    if (
        native.get("type") != "state_update"
        or native.get("arguments", {}).get("state") != "doctor-ok"
    ):
        raise ValueError("native tool contract did not normalize")

    return [
        ("proof xml", "file_read normalized"),
        ("proof mock-mcp", "doctor_ping normalized"),
        ("proof native", "state_update normalized"),
    ]
