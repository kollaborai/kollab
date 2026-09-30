import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from kollabor.cli import _handle_cli_hub
from plugins.hub.models import MessageScope


def _presence(path: Path, identity: str, *, is_coordinator: bool = False) -> None:
    (path / f"{identity}.json").write_text(
        json.dumps(
            {
                "identity": identity,
                "pid": os.getpid(),
                "last_heartbeat": __import__("time").time(),
                "socket_path": str(path / f"{identity}.sock"),
                "is_coordinator": is_coordinator,
            }
        )
    )


def test_cli_msg_is_direct_to_target_only(tmp_path, capsys):
    _presence(tmp_path, "lapis")
    _presence(tmp_path, "sapphire")
    send_to_agent = AsyncMock(return_value=True)

    with patch("plugins.hub.presence.get_presence_dir", return_value=tmp_path), patch(
        "plugins.hub.messenger.AgentMessenger.send_to_agent",
        send_to_agent,
    ):
        asyncio.run(_handle_cli_hub(["msg", "lapis", "check", "this"]))

    send_to_agent.assert_awaited_once()
    socket_path, message = send_to_agent.await_args.args
    assert socket_path.endswith("lapis.sock")
    assert message.to == "lapis"
    assert message.scope == MessageScope.DIRECT.value
    assert message.content == "check this"
    assert capsys.readouterr().out.startswith("sent to lapis\n")


# --------------------------------------------------------------------- #
# `kollab --hub msg agent@device` and `kollab --hub status` (docs/specs/
# agent-network-simple-flow.md, CLI bullet): a handle target routes through
# one online local agent's daemon over the new network_send/network_status
# socket frames instead of the plain local send_to_agent path above.
# --------------------------------------------------------------------- #


def _stream(*replies, end):
    """A request_network_send stand-in: hands each reply to on_reply, returns `end`."""

    async def request(socket_path, handle, content, wait_seconds=600, *, on_reply=None):
        for text in replies:
            on_reply(
                {"type": "network_reply", "from": "infra@alzan-prod-home", "content": text}
            )
        return end

    return AsyncMock(side_effect=request)


def test_cli_msg_to_handle_prints_every_reply_in_order_and_exits_zero(tmp_path, capsys):
    _presence(tmp_path, "koordinator", is_coordinator=True)
    request_network_send = _stream(
        "on it", "handshake ok", end={"type": "network_done", "replies": 2}
    )

    with patch("plugins.hub.presence.get_presence_dir", return_value=tmp_path), patch(
        "plugins.hub.messenger.AgentMessenger.request_network_send",
        request_network_send,
    ):
        asyncio.run(
            _handle_cli_hub(
                ["msg", "infra@alzan-prod-home", "check", "the", "tunnel"]
            )
        )

    request_network_send.assert_awaited_once()
    socket_path, handle, content = request_network_send.await_args.args
    assert socket_path.endswith("koordinator.sock")
    assert handle == "infra@alzan-prod-home"
    assert content == "check the tunnel"
    kwargs = request_network_send.await_args.kwargs
    assert kwargs["wait_seconds"] == 600 and callable(kwargs["on_reply"])
    assert capsys.readouterr().out == (
        "infra@alzan-prod-home: on it\ninfra@alzan-prod-home: handshake ok\n"
    )


def test_cli_msg_to_handle_says_so_when_the_remote_turn_ended_without_a_reply(
    tmp_path, capsys
):
    _presence(tmp_path, "koordinator", is_coordinator=True)
    request_network_send = _stream(end={"type": "network_done", "replies": 0})

    with patch("plugins.hub.presence.get_presence_dir", return_value=tmp_path), patch(
        "plugins.hub.messenger.AgentMessenger.request_network_send",
        request_network_send,
    ):
        asyncio.run(_handle_cli_hub(["msg", "infra@alzan-prod-home", "thanks"]))  # exits 0

    assert capsys.readouterr().out == "infra@alzan-prod-home finished without a reply\n"


def test_cli_msg_to_handle_prints_the_replies_then_the_error_of_a_failed_turn(
    tmp_path, capsys
):
    _presence(tmp_path, "koordinator", is_coordinator=True)
    request_network_send = _stream(
        "on it",
        end={
            "type": "error",
            "msg": "The receiving agent could not complete this request.",
        },
    )

    with patch("plugins.hub.presence.get_presence_dir", return_value=tmp_path), patch(
        "plugins.hub.messenger.AgentMessenger.request_network_send",
        request_network_send,
    ):
        with pytest.raises(SystemExit) as exc_info:
            asyncio.run(_handle_cli_hub(["msg", "infra@alzan-prod-home", "ping"]))

    assert exc_info.value.code == 1
    assert capsys.readouterr().out == (
        "infra@alzan-prod-home: on it\n"
        "The receiving agent could not complete this request.\n"
    )


def test_cli_msg_to_handle_no_wait_sends_zero_and_returns_immediately(tmp_path, capsys):
    _presence(tmp_path, "koordinator", is_coordinator=True)
    request_network_send = AsyncMock(
        return_value={"type": "network_sent", "to": "infra@alzan-prod-home"}
    )

    with patch("plugins.hub.presence.get_presence_dir", return_value=tmp_path), patch(
        "plugins.hub.messenger.AgentMessenger.request_network_send",
        request_network_send,
    ):
        asyncio.run(
            _handle_cli_hub(["msg", "infra@alzan-prod-home", "ping", "--no-wait"])
        )

    assert request_network_send.await_args.kwargs["wait_seconds"] == 0
    assert request_network_send.await_args.args[2] == "ping"
    assert capsys.readouterr().out.startswith("sent to infra@alzan-prod-home\n")


def test_cli_msg_to_handle_timeout_exits_nonzero(tmp_path, capsys):
    _presence(tmp_path, "koordinator", is_coordinator=True)
    request_network_send = AsyncMock(
        return_value={"type": "network_timeout", "replies": 0}
    )

    with patch("plugins.hub.presence.get_presence_dir", return_value=tmp_path), patch(
        "plugins.hub.messenger.AgentMessenger.request_network_send",
        request_network_send,
    ):
        with pytest.raises(SystemExit) as exc_info:
            asyncio.run(_handle_cli_hub(["msg", "infra@alzan-prod-home", "ping"]))

    assert exc_info.value.code == 1
    assert "no reply from infra@alzan-prod-home within 600 s" in capsys.readouterr().out


def test_cli_msg_to_handle_timeout_after_replies_does_not_claim_there_was_none(
    tmp_path, capsys
):
    _presence(tmp_path, "koordinator", is_coordinator=True)
    request_network_send = _stream(
        "on it", end={"type": "network_timeout", "replies": 1}
    )

    with patch("plugins.hub.presence.get_presence_dir", return_value=tmp_path), patch(
        "plugins.hub.messenger.AgentMessenger.request_network_send",
        request_network_send,
    ):
        with pytest.raises(SystemExit) as exc_info:
            asyncio.run(_handle_cli_hub(["msg", "infra@alzan-prod-home", "ping"]))

    assert exc_info.value.code == 1
    out = capsys.readouterr().out
    assert "infra@alzan-prod-home: on it" in out
    assert "infra@alzan-prod-home did not finish within 600 s" in out
    assert "no reply" not in out


def test_cli_msg_to_handle_error_prints_daemon_message(tmp_path, capsys):
    _presence(tmp_path, "koordinator", is_coordinator=True)
    request_network_send = AsyncMock(
        return_value={
            "type": "error",
            "msg": "unknown agent@device: run /connect status to see who is online",
        }
    )

    with patch("plugins.hub.presence.get_presence_dir", return_value=tmp_path), patch(
        "plugins.hub.messenger.AgentMessenger.request_network_send",
        request_network_send,
    ):
        with pytest.raises(SystemExit) as exc_info:
            asyncio.run(_handle_cli_hub(["msg", "ghost@nowhere", "hi"]))

    assert exc_info.value.code == 1
    assert "unknown agent@device" in capsys.readouterr().out


def test_cli_msg_to_handle_with_no_local_agent_exits(tmp_path, capsys):
    with patch("plugins.hub.presence.get_presence_dir", return_value=tmp_path):
        with pytest.raises(SystemExit):
            asyncio.run(_handle_cli_hub(["msg", "infra@alzan-prod-home", "hi"]))

    assert "no local agent online" in capsys.readouterr().out


def test_cli_msg_to_a_plain_local_target_is_unaffected_by_network_routing(
    tmp_path, capsys
):
    """A plain local target keeps today's behaviour exactly (constitution
    §7): it must not touch request_network_send at all."""
    _presence(tmp_path, "lapis")
    send_to_agent = AsyncMock(return_value=True)
    request_network_send = AsyncMock()

    with patch("plugins.hub.presence.get_presence_dir", return_value=tmp_path), patch(
        "plugins.hub.messenger.AgentMessenger.send_to_agent", send_to_agent
    ), patch(
        "plugins.hub.messenger.AgentMessenger.request_network_send",
        request_network_send,
    ):
        asyncio.run(_handle_cli_hub(["msg", "lapis", "hey"]))

    send_to_agent.assert_awaited_once()
    request_network_send.assert_not_awaited()
    assert capsys.readouterr().out.startswith("sent to lapis\n")


def test_cli_status_prints_network_section(tmp_path, capsys):
    _presence(tmp_path, "koordinator", is_coordinator=True)
    request_network_status = AsyncMock(
        return_value={
            "type": "network_status",
            "device": "mac-kollab",
            "trust": "open",
            "agents": [
                {"handle": "infra@alzan-prod-home", "state": "idle", "online": True},
                {
                    "handle": "ops@alzan-prod-home",
                    "state": "working",
                    "task": "rotating logs",
                    "online": True,
                },
            ],
        }
    )

    with patch("plugins.hub.presence.get_presence_dir", return_value=tmp_path), patch(
        "plugins.hub.messenger.AgentMessenger.request_network_status",
        request_network_status,
    ):
        asyncio.run(_handle_cli_hub(["status"]))

    request_network_status.assert_awaited_once()
    assert request_network_status.await_args.args[0].endswith("koordinator.sock")
    out = capsys.readouterr().out
    assert "network: mac-kollab  trust: open" in out
    assert "infra@alzan-prod-home - idle (online)" in out
    assert "ops@alzan-prod-home - working: rotating logs (online)" in out
    # Never a key or a relay: address.
    assert "relay:" not in out


def test_cli_status_prints_not_connected_when_no_answer(tmp_path, capsys):
    _presence(tmp_path, "koordinator", is_coordinator=True)
    request_network_status = AsyncMock(return_value=None)

    with patch("plugins.hub.presence.get_presence_dir", return_value=tmp_path), patch(
        "plugins.hub.messenger.AgentMessenger.request_network_status",
        request_network_status,
    ):
        asyncio.run(_handle_cli_hub(["status"]))

    assert "network: not connected" in capsys.readouterr().out


def test_cli_status_not_connected_when_no_local_agents_online(tmp_path, capsys):
    with patch("plugins.hub.presence.get_presence_dir", return_value=tmp_path):
        asyncio.run(_handle_cli_hub(["status"]))

    out = capsys.readouterr().out
    assert "no agents online" in out
    assert "network: not connected" in out
