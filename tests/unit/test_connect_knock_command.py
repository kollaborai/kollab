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


@pytest.mark.asyncio
async def test_knock_strips_one_pair_of_surrounding_quotes_and_sends():
    plugin = HubPlugin.__new__(HubPlugin)
    plugin._cli_args = SimpleNamespace(attach=False)
    plugin._identity = SimpleNamespace(agent_id="local-agent")
    plugin._rpc_server = object()
    plugin._start_relay_agent = AsyncMock()
    submit = AsyncMock(return_value={"status": "queued", "receipt_id": "a" * 32})
    plugin._relay_agent = SimpleNamespace(submit_contact_request=submit)

    result = await plugin._run_connect_knock(
        'kollabor.ai/c/8f3a2c1d9e4b5061 "Ana from Webceive. Can you help?"'
    )

    submit.assert_awaited_once_with(
        "kollabor.ai",
        "8f3a2c1d9e4b5061",
        "Ana from Webceive. Can you help?",
        source_agent="local-agent",
    )
    assert result == "knock sent to kollabor.ai/c/8f3a2c1d9e4b5061"


@pytest.mark.asyncio
async def test_knock_over_an_https_route_resolves_the_bare_domain():
    plugin = HubPlugin.__new__(HubPlugin)
    plugin._cli_args = SimpleNamespace(attach=False)
    plugin._identity = SimpleNamespace(agent_id="local-agent")
    plugin._rpc_server = object()
    plugin._start_relay_agent = AsyncMock()
    submit = AsyncMock(return_value={"status": "queued", "receipt_id": "a" * 32})
    plugin._relay_agent = SimpleNamespace(submit_contact_request=submit)

    await plugin._run_connect_knock(
        'https://kollabor.ai/c/8f3a2c1d9e4b5061 "hello there"'
    )

    submit.assert_awaited_once_with(
        "kollabor.ai", "8f3a2c1d9e4b5061", "hello there", source_agent="local-agent"
    )


@pytest.mark.asyncio
async def test_knock_unquoted_text_is_sent_as_is():
    plugin = HubPlugin.__new__(HubPlugin)
    plugin._cli_args = SimpleNamespace(attach=False)
    plugin._identity = SimpleNamespace(agent_id="local-agent")
    plugin._rpc_server = object()
    plugin._start_relay_agent = AsyncMock()
    submit = AsyncMock(return_value={"status": "queued", "receipt_id": "a" * 32})
    plugin._relay_agent = SimpleNamespace(submit_contact_request=submit)

    await plugin._run_connect_knock("kollabor.ai/c/8f3a2c1d9e4b5061 hello there")

    submit.assert_awaited_once_with(
        "kollabor.ai", "8f3a2c1d9e4b5061", "hello there", source_agent="local-agent"
    )


@pytest.mark.asyncio
async def test_knock_reports_an_unknown_route_without_keys_or_receipts():
    plugin = HubPlugin.__new__(HubPlugin)
    plugin._cli_args = SimpleNamespace(attach=False)
    plugin._identity = SimpleNamespace(agent_id="local-agent")
    plugin._rpc_server = object()
    plugin._start_relay_agent = AsyncMock()
    submit = AsyncMock(return_value={"error": "unknown_route"})
    plugin._relay_agent = SimpleNamespace(submit_contact_request=submit)

    result = await plugin._run_connect_knock('kollabor.ai/c/8f3a2c1d9e4b5061 "hi"')

    assert result == "connect: no one is registered at that route right now"


@pytest.mark.asyncio
async def test_knock_is_refused_when_attached_to_a_remote_daemon():
    plugin = HubPlugin.__new__(HubPlugin)
    plugin._cli_args = SimpleNamespace(attach=True)

    result = await plugin._run_connect_knock('kollabor.ai/c/8f3a2c1d9e4b5061 "hi"')

    assert result == "connect: attached daemon does not support private contact requests"


def test_removed_contact_names_redirect_to_knock():
    assert CONNECT_REMOVED["contact"] == "use /connect knock, /connect knocks"
    assert CONNECT_REMOVED["contacts"] == "use /connect knock, /connect knocks"


@pytest.mark.asyncio
async def test_connect_contact_command_redirects_to_knock():
    plugin = HubPlugin.__new__(HubPlugin)
    plugin._cli_args = SimpleNamespace(attach=True)

    result = await plugin._handle_connect_command("contact")

    assert result == "connect: use /connect knock, /connect knocks"
