"""Regression tests for duplicate XML tool calls in one LLM response."""

from kollabor_ai.response_parser import ResponseParser


def test_identical_terminal_calls_execute_once() -> None:
    parser = ResponseParser()

    parsed = parser.parse_response(
        "<terminal>python -m pytest -q tests/unit/test_hub_wake_order.py</terminal>\n"
        "<terminal>python -m pytest -q tests/unit/test_hub_wake_order.py</terminal>"
    )

    tools = parser.get_all_tools(parsed)

    assert len(tools) == 1
    assert tools[0]["type"] == "terminal"
    assert parsed["metadata"]["total_tools"] == 1


def test_duplicate_calls_with_different_semantics_are_preserved() -> None:
    parser = ResponseParser()

    parsed = parser.parse_response(
        '<terminal timeout="30">pwd</terminal>\n'
        '<terminal timeout="60">pwd</terminal>\n'
        "<terminal>ls</terminal>\n"
        "<terminal>ls</terminal>"
    )

    tools = parser.get_all_tools(parsed)

    assert [tool["command"] for tool in tools] == ["pwd", "pwd", "ls"]
    assert [tool["timeout"] for tool in tools] == ["30", "60", None]


def test_identical_mcp_calls_are_deduplicated_but_different_arguments_remain() -> None:
    parser = ResponseParser()

    call = '{"name":"hub_msg","arguments":{"to":"zircon","content":"ack"}}'
    parsed = parser.parse_response(
        f"<tool_call>{call}</tool_call>\n"
        f"<tool_call>{call}</tool_call>\n"
        '<tool_call>{"name":"hub_msg","arguments":{"to":"zircon","content":"next"}}</tool_call>'
    )

    tools = parser.get_all_tools(parsed)

    assert len(tools) == 2
    assert [tool["arguments"]["content"] for tool in tools] == ["ack", "next"]


def test_xml_entities_are_decoded_before_terminal_execution() -> None:
    parser = ResponseParser()

    parsed = parser.parse_response(
        "<terminal>jq 'select(.cache_read_tokens &gt; 0) | .session_id'</terminal>"
    )

    tools = parser.get_all_tools(parsed)

    assert tools[0]["command"] == "jq 'select(.cache_read_tokens > 0) | .session_id'"
