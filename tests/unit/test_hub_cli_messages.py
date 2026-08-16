import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

from kollabor.cli import _handle_cli_hub
from plugins.hub.models import MessageScope


def _presence(path: Path, identity: str) -> None:
    (path / f"{identity}.json").write_text(
        json.dumps(
            {
                "identity": identity,
                "pid": os.getpid(),
                "last_heartbeat": __import__("time").time(),
                "socket_path": str(path / f"{identity}.sock"),
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
