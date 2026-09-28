import asyncio
from types import SimpleNamespace

import pytest

from kollabor.application import TerminalLLMChat


@pytest.mark.asyncio
async def test_attach_without_a_listening_daemon_exits_instead_of_idling(tmp_path):
    shown, scheduled = [], []
    app = TerminalLLMChat.__new__(TerminalLLMChat)
    app._attach_to = "koordinator"
    app._attach_socket = str(tmp_path / "koordinator.sock")  # nothing listens here
    app._startup_ready = asyncio.Event()
    app.running = True
    app.renderer = SimpleNamespace(
        message_coordinator=SimpleNamespace(display_message_sequence=shown.extend)
    )

    async def shutdown():
        return None

    app.shutdown = shutdown
    app.create_background_task = lambda coro, name: scheduled.append(name) or coro.close()

    await app._initialize_attach_proxy()

    assert app.running is False
    assert scheduled == ["attach_client_shutdown"]
    assert "Start it by running kollab in its project." in shown[-1][1]
