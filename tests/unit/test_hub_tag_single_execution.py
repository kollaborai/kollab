"""One hub XML tag in a model response is one execution, not two.

The LLM core bridges every ToolRegistry tool into the response parser before
any plugin starts. The hub plugin then registered its own tag for the same name,
both entries matched the same text, and a single `<hub_msg>` ran its handler
twice: the live run showed `sent to ...` followed by `not sent again: ...` for
one tag. A native tool call never saw it because it does not go through the tag
parser. These tests drive the real parser and the real tool executor in the
order the app starts them.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import AsyncMock, MagicMock

import pytest

from kollabor.llm.llm_coordinator import LLMService
from kollabor_agent.runtime import AgentRuntime
from kollabor_agent.tool_executor import ToolExecutor
from kollabor_ai.response_parser import ResponseParser
from plugins.hub.plugin import HubPlugin

PEER = "koordinator@laptop-kollab-m1-mac6"


class _Bus:
    """Just enough event bus for ToolExecutor.execute_tool."""

    def get_service(self, name):
        return None

    async def emit_with_hooks(self, *args, **kwargs) -> Dict[str, Any]:
        return {}


def _wire(order: str, tmp_path):
    """Real parser, real executor, real hub plugin, in the given start order.

    "core-first" is how the app boots: llm_service.initialize() registers the
    registry tags, then the plugins initialize and register theirs.
    """
    parser = ResponseParser()
    executor = ToolExecutor(
        mcp_integration=None,
        event_bus=_Bus(),
        terminal_timeout=15,
        workspace=str(tmp_path),
    )
    services = {"response_parser": parser, "tool_executor": executor}
    bus = MagicMock()
    bus.get_service.side_effect = services.get

    sent: list = []

    async def send(address, content, **kwargs):
        sent.append({"address": address, "content": content, **kwargs})
        return {"id": "a" * 32, "state": "queued", "duplicate": False}

    plugin = HubPlugin(event_bus=bus)
    plugin._identity = AgentRuntime(
        name="coordinator",
        identity="koordinator",
        agent_id="koordinator-id",
        is_coordinator=True,
    )
    plugin._relay_agent = SimpleNamespace(
        _turn=SimpleNamespace(get=lambda: None),
        resolve_handle=AsyncMock(return_value=f"relay:{PEER}"),
        send=send,
    )
    plugin._vault = None
    plugin._task_ledger = None
    plugin._presence = MagicMock()
    plugin._display_outgoing_message = MagicMock()
    plugin._bridge_forward = AsyncMock()

    # The registration binds the handler, so the counter goes in before it.
    calls: list = []
    real = plugin._handle_hub_msg_tool

    async def counting(tool_data):
        calls.append(tool_data)
        return await real(tool_data)

    plugin._handle_hub_msg_tool = counting
    plugin.calls = calls

    core = SimpleNamespace(response_parser=parser)
    if order == "core-first":
        LLMService._register_registry_tool_tags(core)
        plugin._register_pipeline_tools()
    else:
        plugin._register_pipeline_tools()
        LLMService._register_registry_tool_tags(core)
    return parser, executor, plugin, sent


async def _run_response(parser, executor, text: str):
    """What queue_processor does with a response's XML tools."""
    parsed = parser.parse_response(text)
    return [await executor.execute_tool(t) for t in parser.get_all_tools(parsed)]


@pytest.mark.asyncio
@pytest.mark.parametrize("order", ["core-first", "plugins-first"])
async def test_one_hub_msg_tag_is_one_handler_call_and_one_send(order, tmp_path):
    parser, executor, plugin, sent = _wire(order, tmp_path)

    results = await _run_response(
        parser,
        executor,
        f'Sending it.\n<hub_msg to="{PEER}">Exact output:\nserver</hub_msg>',
    )

    assert len(plugin.calls) == 1
    assert len(sent) == 1
    assert [r.tool_type for r in results] == ["hub_msg"]
    assert results[0].success
    assert results[0].output.startswith(f"sent to {PEER}")
    assert "not sent again" not in results[0].output


@pytest.mark.asyncio
@pytest.mark.parametrize("order", ["core-first", "plugins-first"])
async def test_two_different_hub_msg_tags_are_two_calls_not_four(order, tmp_path):
    parser, executor, plugin, sent = _wire(order, tmp_path)

    results = await _run_response(
        parser,
        executor,
        f'<hub_msg to="{PEER}">first</hub_msg> then <hub_msg to="{PEER}">second</hub_msg>',
    )

    assert len(results) == 2
    assert [s["content"] for s in sent] == ["first", "second"]


@pytest.mark.parametrize("order", ["core-first", "plugins-first"])
def test_no_tag_name_or_tool_type_is_registered_twice_by_the_hub(order, tmp_path):
    """Every hub tag shares hub_msg's registration path; none may stack."""
    parser, *_ = _wire(order, tmp_path)

    names = [t["name"] for t in parser._plugin_tags]
    types = [t["tool_type"] for t in parser._plugin_tags]

    assert len(names) == len(set(names)), sorted(n for n in names if names.count(n) > 1)
    assert len(types) == len(set(types)), sorted(t for t in types if types.count(t) > 1)


@pytest.mark.parametrize("order", ["core-first", "plugins-first"])
@pytest.mark.parametrize(
    "tag, tool_type",
    [
        ("<hub_broadcast>hello all</hub_broadcast>", "hub_broadcast"),
        ('<hub_reply to="lapis">on it</hub_reply>', "hub_reply"),
        ("<hub_status />", "hub_status"),
        ("<hub_stop>lapis</hub_stop>", "hub_stop"),
        ("<scratchpad>note</scratchpad>", "scratchpad"),
        ("<scratchpad_append>more</scratchpad_append>", "scratchpad_append"),
        ("<vault_write keywords=\"a,b\">an insight</vault_write>", "vault_write"),
    ],
)
def test_sibling_hub_tags_parse_to_exactly_one_tool(order, tag, tool_type, tmp_path):
    parser, *_ = _wire(order, tmp_path)

    tools = parser.get_all_tools(parser.parse_response(f"ok\n{tag}"))

    assert [t["type"] for t in tools] == [tool_type]


def test_registering_a_name_again_replaces_it_instead_of_stacking():
    import re

    parser = ResponseParser()
    parser.register_plugin_tag("ping", re.compile(r"<ping>(.*?)</ping>"), "ping", lambda m: {"v": 1})
    parser.register_plugin_tag("ping", re.compile(r"<ping>(.*?)</ping>"), "ping", lambda m: {"v": 2})

    tools = parser.get_all_tools(parser.parse_response("<ping>x</ping>"))

    assert [t["v"] for t in tools] == [2]


def test_two_names_may_share_one_tool_type():
    """<notifications/> and <notifications clear/> are separate tags, one handler."""
    import re

    parser = ResponseParser()
    clear_tag = re.compile(r"<notifications\s+clear\s*/>")
    query_tag = re.compile(r"<notifications\s*/>")
    parser.register_plugin_tag("notifications_clear", clear_tag, "notifications", lambda m: {"action": "clear"})
    parser.register_plugin_tag("notifications", query_tag, "notifications", lambda m: {"action": "query"})

    clear = parser.get_all_tools(parser.parse_response("<notifications clear/>"))
    query = parser.get_all_tools(parser.parse_response("<notifications/>"))

    assert [t["action"] for t in clear] == ["clear"]
    assert [t["action"] for t in query] == ["query"]
