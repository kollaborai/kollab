"""`/connect knock <route> "text"` -- parsing, no keys, no private form."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from plugins.hub.plugin import CONNECT_REMOVED, HubPlugin, _parse_knock_route

USAGE = 'connect: use /connect knock <route> "text"'


def test_parse_knock_route_accepts_a_bare_domain_and_lowercases_the_hex():
    domain, route = _parse_knock_route("kollabor.ai/c/8F3A2C1D9E4B5061")
    assert domain == "kollabor.ai"
    assert route == "8f3a2c1d9e4b5061"


def test_parse_knock_route_accepts_an_https_prefix():
    domain, route = _parse_knock_route("https://kollabor.ai/c/8f3a2c1d9e4b5061")
    assert domain == "kollabor.ai"
    assert route == "8f3a2c1d9e4b5061"


@pytest.mark.parametrize(
    "value",
    [
        "kollabor.ai/c/8f3a2c1d9e4b506",  # 15 hex chars
        "kollabor.ai/c/8f3a2c1d9e4b50611",  # 17 hex chars
        "kollabor.ai/c/not-hex-at-all-1",
        "kollabor.ai",
        "/c/8f3a2c1d9e4b5061",
    ],
)
def test_parse_knock_route_rejects_bad_shapes(value):
    with pytest.raises(ValueError):
        _parse_knock_route(value)


@pytest.mark.asyncio
async def test_knock_with_no_arguments_is_a_usage_error():
    plugin = HubPlugin.__new__(HubPlugin)
    assert await plugin._run_connect_knock("") == USAGE


@pytest.mark.asyncio
async def test_knock_with_route_but_no_text_is_a_usage_error():
    plugin = HubPlugin.__new__(HubPlugin)
    assert await plugin._run_connect_knock("kollabor.ai/c/8f3a2c1d9e4b5061") == USAGE


@pytest.mark.asyncio
async def test_knock_with_a_bad_route_is_a_usage_error():
    plugin = HubPlugin.__new__(HubPlugin)
    assert await plugin._run_connect_knock('not-a-route "hello"') == USAGE


def _owner_plugin(answer: str = "knocking on kollabor.ai/c/8f3a2c1d9e4b5061, rings for 5:00"):
    plugin = HubPlugin.__new__(HubPlugin)
    plugin._cli_args = SimpleNamespace(attach=False)
    plugin._identity = SimpleNamespace(agent_id="local-agent")
    plugin._rpc_server = object()
    plugin._start_relay_agent = AsyncMock()
    plugin._relay_agent = SimpleNamespace(knocks=AsyncMock(return_value={"text": answer}))
    return plugin


@pytest.mark.asyncio
async def test_knock_strips_one_pair_of_surrounding_quotes_and_sends():
    plugin = _owner_plugin()

    result = await plugin._run_connect_knock(
        'kollabor.ai/c/8f3a2c1d9e4b5061 "Ana from Acme. Can you help?"'
    )

    plugin._relay_agent.knocks.assert_awaited_once_with(
        "knock",
        {"domain": "kollabor.ai", "route": "8f3a2c1d9e4b5061", "text": "Ana from Acme. Can you help?"},
        source_agent="local-agent",
    )
    assert result == "knocking on kollabor.ai/c/8f3a2c1d9e4b5061, rings for 5:00"


@pytest.mark.asyncio
async def test_knock_over_an_https_route_resolves_the_bare_domain():
    plugin = _owner_plugin()

    await plugin._run_connect_knock('https://kollabor.ai/c/8f3a2c1d9e4b5061 "hello there"')

    args = plugin._relay_agent.knocks.await_args.args
    assert args == ("knock", {"domain": "kollabor.ai", "route": "8f3a2c1d9e4b5061", "text": "hello there"})


@pytest.mark.asyncio
async def test_knock_unquoted_text_is_sent_as_is():
    plugin = _owner_plugin()

    await plugin._run_connect_knock("kollabor.ai/c/8f3a2c1d9e4b5061 hello there")

    assert plugin._relay_agent.knocks.await_args.args[1]["text"] == "hello there"


@pytest.mark.asyncio
async def test_knock_text_with_control_characters_is_a_usage_error():
    plugin = _owner_plugin()

    assert await plugin._run_connect_knock('kollabor.ai/c/8f3a2c1d9e4b5061 "hi\x1b[2J"') == USAGE
    plugin._relay_agent.knocks.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_knock_line_is_what_the_owner_says():
    plugin = _owner_plugin(
        "kollabor.ai/c/8f3a2c1d9e4b5061 unavailable. redialing for 1h, next in 1:00. /connect knocks to stop"
    )

    result = await plugin._run_connect_knock('kollabor.ai/c/8f3a2c1d9e4b5061 "hi"')

    assert result.startswith("kollabor.ai/c/8f3a2c1d9e4b5061 unavailable. redialing for 1h")


@pytest.mark.asyncio
async def test_an_attached_window_asks_its_daemon_and_an_old_daemon_says_so():
    from kollabor_rpc import RpcMethodNotFound

    plugin = HubPlugin.__new__(HubPlugin)
    plugin._cli_args = SimpleNamespace(attach=True)
    state = SimpleNamespace(hub_knocks=AsyncMock(return_value={"text": "knocking on x, rings for 5:00"}))
    plugin.event_bus = SimpleNamespace(get_service=lambda name: state if name == "state_service" else None)

    assert await plugin._run_connect_knock('kollabor.ai/c/8f3a2c1d9e4b5061 "hi"') == "knocking on x, rings for 5:00"
    state.hub_knocks.assert_awaited_once_with(
        "knock", {"domain": "kollabor.ai", "route": "8f3a2c1d9e4b5061", "text": "hi"}
    )

    state.hub_knocks = AsyncMock(side_effect=RpcMethodNotFound("state.hub_knocks"))
    assert await plugin._run_connect_knock('kollabor.ai/c/8f3a2c1d9e4b5061 "hi"') == (
        "connect: the attached daemon needs an update for knocks"
    )


def test_removed_contact_names_redirect_to_knock():
    assert CONNECT_REMOVED["contact"] == "use /connect knock, /connect knocks"
    assert CONNECT_REMOVED["contacts"] == "use /connect knock, /connect knocks"


@pytest.mark.asyncio
async def test_connect_contact_command_redirects_to_knock():
    plugin = HubPlugin.__new__(HubPlugin)
    plugin._cli_args = SimpleNamespace(attach=True)

    result = await plugin._handle_connect_command("contact")

    assert result == "connect: use /connect knock, /connect knocks"
