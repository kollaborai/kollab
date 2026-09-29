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


def test_cli_msg_to_handle_sends_network_frame_and_prints_reply(tmp_path, capsys):
    _presence(tmp_path, "koordinator", is_coordinator=True)
    request_network_send = AsyncMock(
        return_value={
            "type": "network_reply",
            "from": "infra@alzan-prod-home",
            "content": "handshake ok",
        }
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
    assert request_network_send.await_args.kwargs == {"wait_seconds": 600}
    assert capsys.readouterr().out.startswith("infra@alzan-prod-home: handshake ok\n")


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

    assert request_network_send.await_args.kwargs == {"wait_seconds": 0}
    assert request_network_send.await_args.args[2] == "ping"
    assert capsys.readouterr().out.startswith("sent to infra@alzan-prod-home\n")


def test_cli_msg_to_handle_timeout_exits_nonzero(tmp_path, capsys):
    _presence(tmp_path, "koordinator", is_coordinator=True)
    request_network_send = AsyncMock(return_value={"type": "network_timeout"})

    with patch("plugins.hub.presence.get_presence_dir", return_value=tmp_path), patch(
        "plugins.hub.messenger.AgentMessenger.request_network_send",
        request_network_send,
    ):
        with pytest.raises(SystemExit) as exc_info:
            asyncio.run(_handle_cli_hub(["msg", "infra@alzan-prod-home", "ping"]))

    assert exc_info.value.code == 1
    assert "no reply from infra@alzan-prod-home within 600 s" in capsys.readouterr().out


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
