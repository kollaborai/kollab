"""Attach cleanup regression tests."""

from __future__ import annotations

import asyncio
import inspect

import pytest


def test_cli_attach_path_is_terminal_llm_chat_proxy_only() -> None:
    """--attach should route through TerminalLLMChat, not the legacy client."""
    import kollabor.cli as cli

    async_main_source = inspect.getsource(cli.async_main)
    cli_source = inspect.getsource(cli)

    assert "attach_to=_attach_to" in async_main_source
    assert not hasattr(cli, "_handle_cli_attach")
    assert "from kollabor.attach_client import AttachClient" not in cli_source


def test_legacy_attach_client_emits_deprecation_warning() -> None:
    """Direct AttachClient use should be explicitly marked as legacy."""
    from kollabor.attach_client import AttachClient

    with pytest.warns(DeprecationWarning, match="legacy standalone attach client"):
        AttachClient(socket_path="/tmp/test.sock", identity="test")


def test_remote_state_service_routes_hub_messages_over_rpc() -> None:
    """Attach-mode hub msg/broadcast calls must cross the daemon RPC boundary."""
    from kollabor.state import RemoteStateService

    class FakeRpcClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict, float]] = []

        async def call(self, method: str, params: dict, timeout: float | None = None):
            self.calls.append((method, params, timeout or 0.0))
            return {"text": f"ok:{method}"}

    async def run_case() -> None:
        rpc = FakeRpcClient()
        state = RemoteStateService(rpc_client=rpc, timeout=3.5)

        assert await state.hub_send_msg("lapis", "ping") == "ok:state.hub_send_msg"
        assert await state.hub_broadcast("all hands", force=True) == (
            "ok:state.hub_broadcast"
        )
        assert rpc.calls == [
            (
                "state.hub_send_msg",
                {"target": "lapis", "content": "ping"},
                3.5,
            ),
            (
                "state.hub_broadcast",
                {"content": "all hands", "force": True},
                3.5,
            ),
        ]

    asyncio.run(run_case())


def test_state_handlers_expose_hub_message_rpcs() -> None:
    """Daemon-side handlers should keep hub message writes behind state RPC."""
    from kollabor.state.handlers import register_state_handlers

    class FakeRpcServer:
        def __init__(self) -> None:
            self.handlers = {}

        def register(self, method: str, handler) -> None:
            self.handlers[method] = handler

    class FakeStateService:
        async def hub_send_msg(self, target: str, content: str) -> str:
            return f"sent {target}: {content}"

        async def hub_broadcast(self, content: str, force: bool = False) -> str:
            return f"broadcast {force}: {content}"

    async def run_case() -> None:
        server = FakeRpcServer()
        register_state_handlers(server, FakeStateService())

        msg_result = await server.handlers["state.hub_send_msg"](
            {"target": "lapis", "content": "ping"}
        )
        broadcast_result = await server.handlers["state.hub_broadcast"](
            {"content": "all hands", "force": True}
        )

        assert msg_result == {"text": "sent lapis: ping"}
        assert broadcast_result == {"text": "broadcast True: all hands"}

    asyncio.run(run_case())
