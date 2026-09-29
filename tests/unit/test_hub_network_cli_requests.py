"""Slice D (CLI) of the agent network: docs/specs/agent-network-simple-flow.md.

`kollab --hub status` and `kollab --hub msg agent@device text` talk to one
local agent's daemon over its socket (new `network_status`/`network_send`
frames in plugins/hub/messenger.py). These tests exercise the daemon side
directly -- the plugin handlers wired as AgentSocketServer.on_network_status
/on_network_send -- and the CLI-waiter fulfillment hook in
_on_message_received. Slice A owns the real relay bridge
(plugins/hub/relay_agent.py); everything here mocks its documented
interface (device_name, trust_level, remote_agents, resolve_handle) the
same way test_hub_network_surface.py does, rather than importing it.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from plugins.hub.messenger import AgentMessenger, AgentSocketServer
from plugins.hub.models import HubMessage
from plugins.hub.plugin import HubPlugin
from plugins.hub.relay_state import RelayError


def _make_plugin(*, relay_agent=None) -> HubPlugin:
    """A minimal HubPlugin, same stubbing pattern as test_hub_content_dedup.py."""
    plugin = HubPlugin(event_bus=MagicMock())
    plugin._vault = None
    plugin._task_ledger = None
    plugin._nudge_engine = None
    plugin._identity = SimpleNamespace(identity="lapis", agent_id="lapis-1")
    plugin._election = None
    plugin._roster = []
    plugin._announce_presence = AsyncMock()
    plugin._relay_agent = relay_agent
    plugin._start_relay_agent = AsyncMock()
    return plugin


# --------------------------------------------------------------------- #
# network_status
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_network_status_returns_device_trust_and_remote_rows():
    rows = [{"handle": "infra@alzan-prod-home", "state": "idle", "online": True}]
    relay = SimpleNamespace(
        device_name=lambda: "mac-kollab",
        trust_level=lambda: "open",
        remote_agents=AsyncMock(return_value=rows),
    )
    plugin = _make_plugin(relay_agent=relay)

    result = await plugin._handle_network_status_request()

    assert result == {"device": "mac-kollab", "trust": "open", "agents": rows}
    plugin._start_relay_agent.assert_not_awaited()


@pytest.mark.asyncio
async def test_network_status_without_a_relay_agent_is_empty_not_an_error():
    plugin = _make_plugin(relay_agent=None)

    result = await plugin._handle_network_status_request()

    assert result == {"device": "", "trust": "", "agents": []}
    plugin._start_relay_agent.assert_awaited_once()


# --------------------------------------------------------------------- #
# network_send
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_network_send_resolves_routes_and_waits_for_the_reply():
    relay = SimpleNamespace(
        resolve_handle=lambda handle: f"relay:resolved:{handle}",
    )
    plugin = _make_plugin(relay_agent=relay)
    plugin._route_message = AsyncMock(return_value=[])
    plugin._display_outgoing_message = MagicMock()

    async def _reply_soon() -> None:
        await asyncio.sleep(0.01)
        future = next(iter(plugin._cli_waiters.values()))
        future.set_result(("infra@alzan-prod-home", "handshake ok"))

    asyncio.create_task(_reply_soon())
    result = await plugin._handle_network_send_request(
        "infra@alzan-prod-home", "check the tunnel", 5
    )

    assert result == {
        "type": "network_reply",
        "from": "infra@alzan-prod-home",
        "content": "handshake ok",
    }
    plugin._route_message.assert_awaited_once()
    sent_msg = plugin._route_message.await_args.args[0]
    assert isinstance(sent_msg, HubMessage)
    assert sent_msg.to == "infra@alzan-prod-home"
    assert sent_msg.content == "check the tunnel"
    plugin._display_outgoing_message.assert_called_once_with(
        "infra@alzan-prod-home", "check the tunnel"
    )
    # Waiters are cleaned up once the reply lands -- nothing leaks.
    assert plugin._cli_waiters == {}
    assert plugin._cli_waiters_by_handle == {}


@pytest.mark.asyncio
async def test_network_send_times_out_and_cleans_up_waiters():
    relay = SimpleNamespace(resolve_handle=lambda handle: f"relay:resolved:{handle}")
    plugin = _make_plugin(relay_agent=relay)
    plugin._route_message = AsyncMock(return_value=[])
    plugin._display_outgoing_message = MagicMock()

    result = await plugin._handle_network_send_request("infra@alzan-prod-home", "ping", 0.05)

    assert result == {"type": "network_timeout"}
    assert plugin._cli_waiters == {}
    assert plugin._cli_waiters_by_handle == {}


@pytest.mark.asyncio
async def test_network_send_no_wait_returns_immediately_without_waiting():
    relay = SimpleNamespace(resolve_handle=lambda handle: f"relay:resolved:{handle}")
    plugin = _make_plugin(relay_agent=relay)
    plugin._route_message = AsyncMock(return_value=[])
    plugin._display_outgoing_message = MagicMock()

    result = await plugin._handle_network_send_request("infra@alzan-prod-home", "ping", 0)

    assert result == {"type": "network_sent", "to": "infra@alzan-prod-home"}
    assert plugin._cli_waiters == {}
    assert plugin._cli_waiters_by_handle == {}


@pytest.mark.asyncio
async def test_network_send_reports_a_relay_error_from_resolve_handle():
    def raise_unknown(handle):
        raise RelayError("unknown agent@device: run /connect status to see who is online")

    relay = SimpleNamespace(resolve_handle=raise_unknown)
    plugin = _make_plugin(relay_agent=relay)
    plugin._route_message = AsyncMock(
        side_effect=AssertionError("must not route when resolve_handle fails")
    )

    result = await plugin._handle_network_send_request("ghost@nowhere", "hi", 5)

    assert result == {
        "type": "error",
        "msg": "unknown agent@device: run /connect status to see who is online",
    }
    plugin._route_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_network_send_rejects_a_target_that_is_not_a_handle():
    plugin = _make_plugin(relay_agent=SimpleNamespace(resolve_handle=lambda h: h))

    result = await plugin._handle_network_send_request("lapis", "hi", 5)

    assert result["type"] == "error"
    assert "agent@device" in result["msg"]


@pytest.mark.asyncio
async def test_network_send_without_a_relay_agent_is_a_clean_error():
    plugin = _make_plugin(relay_agent=None)

    result = await plugin._handle_network_send_request("infra@alzan-prod-home", "hi", 5)

    assert result == {
        "type": "error",
        "msg": "network messaging is not available on this build",
    }


@pytest.mark.asyncio
async def test_network_send_surfaces_a_routing_rejection():
    relay = SimpleNamespace(resolve_handle=lambda handle: f"relay:resolved:{handle}")
    plugin = _make_plugin(relay_agent=relay)
    plugin._route_message = AsyncMock(
        return_value=[("infra@alzan-prod-home", "remote task rejected")]
    )
    plugin._display_outgoing_message = MagicMock()

    result = await plugin._handle_network_send_request("infra@alzan-prod-home", "ping", 5)

    assert result == {"type": "error", "msg": "remote task rejected"}
    assert plugin._cli_waiters == {}
    assert plugin._cli_waiters_by_handle == {}


# --------------------------------------------------------------------- #
# _on_message_received fulfils a pending CLI waiter (hook added right
# after the msg_id dedup check)
# --------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_on_message_received_fulfils_the_waiter_by_handle():
    plugin = _make_plugin(relay_agent=None)
    future = asyncio.get_running_loop().create_future()
    plugin._cli_waiters_by_handle["infra@alzan-prod-home"] = future

    reply = HubMessage(
        action="message",
        from_agent="infra-1",
        from_identity="infra@alzan-prod-home",
        to="lapis",
        content="handshake ok",
    )
    await plugin._on_message_received(reply)

    assert future.done()
    assert future.result() == ("infra@alzan-prod-home", "handshake ok")
    assert plugin._cli_waiters_by_handle == {}


@pytest.mark.asyncio
async def test_on_message_received_fulfils_the_waiter_by_thread_id_first():
    plugin = _make_plugin(relay_agent=None)
    future = asyncio.get_running_loop().create_future()
    plugin._cli_waiters["thread-xyz"] = future
    plugin._cli_waiters_by_handle["infra@alzan-prod-home"] = future

    reply = HubMessage(
        action="message",
        from_agent="infra-1",
        from_identity="infra@alzan-prod-home",
        to="lapis",
        content="handshake ok",
        thread_id="thread-xyz",
    )
    await plugin._on_message_received(reply)

    assert future.result() == ("infra@alzan-prod-home", "handshake ok")
    assert plugin._cli_waiters == {}
    assert plugin._cli_waiters_by_handle == {}


@pytest.mark.asyncio
async def test_on_message_received_leaves_unrelated_messages_alone():
    plugin = _make_plugin(relay_agent=None)
    future = asyncio.get_running_loop().create_future()
    plugin._cli_waiters_by_handle["infra@alzan-prod-home"] = future

    other = HubMessage(
        action="message",
        from_agent="sapphire-1",
        from_identity="sapphire",
        to="lapis",
        content="unrelated chatter",
    )
    await plugin._on_message_received(other)

    assert not future.done()
    assert plugin._cli_waiters_by_handle == {"infra@alzan-prod-home": future}


# --------------------------------------------------------------------- #
# Wire protocol: AgentSocketServer <-> AgentMessenger client helpers, over
# a real unix socket (messenger.py frame plumbing).
# --------------------------------------------------------------------- #


def test_network_status_frame_round_trips_over_a_real_socket():
    async def run():
        async def on_status():
            return {
                "device": "mac-kollab",
                "trust": "open",
                "agents": [
                    {"handle": "infra@alzan-prod-home", "state": "idle", "online": True}
                ],
            }

        server = AgentSocketServer(
            "status-id",
            lambda _message: None,
            on_network_status=on_status,
            socket_name=f"net-status-{os.getpid()}",
        )
        sock_path = await server.start()
        try:
            result = await AgentMessenger.request_network_status(sock_path)
        finally:
            await server.stop()

        assert result == {
            "type": "network_status",
            "device": "mac-kollab",
            "trust": "open",
            "agents": [
                {"handle": "infra@alzan-prod-home", "state": "idle", "online": True}
            ],
        }

    asyncio.run(run())


def test_network_status_with_no_handler_is_not_connected():
    async def run():
        server = AgentSocketServer(
            "status-none-id",
            lambda _message: None,
            socket_name=f"net-status-none-{os.getpid()}",
        )
        sock_path = await server.start()
        try:
            return await AgentMessenger.request_network_status(sock_path)
        finally:
            await server.stop()

    result = asyncio.run(run())
    assert result == {"type": "network_status", "device": "", "trust": "", "agents": []}


def test_network_send_frame_round_trips_over_a_real_socket():
    async def run():
        seen = {}

        async def on_send(to, content, wait_seconds):
            seen["to"] = to
            seen["content"] = content
            seen["wait_seconds"] = wait_seconds
            return {"type": "network_reply", "from": to, "content": "handshake ok"}

        server = AgentSocketServer(
            "send-id",
            lambda _message: None,
            on_network_send=on_send,
            socket_name=f"net-send-{os.getpid()}",
        )
        sock_path = await server.start()
        try:
            result = await AgentMessenger.request_network_send(
                sock_path, "infra@alzan-prod-home", "check the tunnel", wait_seconds=5
            )
        finally:
            await server.stop()

        assert result == {
            "type": "network_reply",
            "from": "infra@alzan-prod-home",
            "content": "handshake ok",
        }
        assert seen == {
            "to": "infra@alzan-prod-home",
            "content": "check the tunnel",
            "wait_seconds": 5,
        }

    asyncio.run(run())


def test_network_send_with_no_handler_is_a_clean_error():
    async def run():
        server = AgentSocketServer(
            "send-none-id",
            lambda _message: None,
            socket_name=f"net-send-none-{os.getpid()}",
        )
        sock_path = await server.start()
        try:
            return await AgentMessenger.request_network_send(
                sock_path, "infra@alzan-prod-home", "hi", wait_seconds=1
            )
        finally:
            await server.stop()

    result = asyncio.run(run())
    assert result == {
        "type": "error",
        "msg": "network messaging is not available on this build",
    }
