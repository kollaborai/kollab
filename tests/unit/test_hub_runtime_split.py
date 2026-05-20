"""Hub runtime extraction tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from plugins.hub.runtime import HubRuntimeCallbacks, HubRuntimeServices


class FakeSocketServer:
    def __init__(self, agent_id: str, on_message: Any, **kwargs: Any) -> None:
        self.agent_id = agent_id
        self.on_message = on_message
        self.kwargs = kwargs
        self._display_tap = None
        self._identity_info = None
        self._rpc_server = None

    async def start(self) -> str:
        self.kwargs["events"].append("socket.start")
        return "/tmp/lapis.sock"


class FakeRpcServer:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.handlers: dict[str, Any] = {}

    def register(self, name: str, handler: Any) -> None:
        self.events.append(f"rpc.register:{name}")
        self.handlers[name] = handler


class FakeEventBus:
    def __init__(self, state_service: Any, events: list[str]) -> None:
        self.state_service = state_service
        self.events = events
        self.services: dict[str, Any] = {}

    def get_service(self, name: str) -> Any:
        if name == "state_service":
            return self.state_service
        return None

    def register_service(self, name: str, service: Any) -> None:
        self.events.append(f"event_bus.register:{name}")
        self.services[name] = service


class FakePresence:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def publish(self) -> None:
        self.events.append("presence.publish")


def test_runtime_startup_order_wires_rpc_state_daemon_ready_and_presence() -> None:
    events: list[str] = []
    identity = SimpleNamespace(agent_id="agent-lapis", identity="lapis", socket_path="")
    state_service = SimpleNamespace(
        set_context_identity=lambda identity_name: events.append(
            f"state.context_identity:{identity_name}"
        )
    )
    event_bus = FakeEventBus(state_service=state_service, events=events)

    def make_socket_server(agent_id: str, on_message: Any, **kwargs: Any) -> Any:
        events.append("socket.create")
        return FakeSocketServer(agent_id, on_message, events=events, **kwargs)

    def make_rpc_server() -> FakeRpcServer:
        events.append("rpc.create")
        return FakeRpcServer(events)

    def register_state_handlers(rpc_server: Any, service: Any) -> None:
        assert service is state_service
        assert "ping" in rpc_server.handlers
        events.append("rpc.state_handlers")

    def signal_daemon_ready(socket_path: str) -> None:
        events.append(f"daemon.ready:{socket_path}")

    async def on_message(_: Any) -> None:
        return None

    runtime = HubRuntimeServices(
        socket_server_factory=make_socket_server,
        rpc_server_factory=make_rpc_server,
        state_handler_registrar=register_state_handlers,
        daemon_ready_signal=signal_daemon_ready,
    )

    async def run() -> None:
        handles = await runtime.start_socket_runtime(
            identity=identity,
            display_tap=object(),
            event_bus=event_bus,
            callbacks=HubRuntimeCallbacks(
                on_message=on_message,
                on_get_output=lambda lines=200: [],
                on_shutdown=lambda reason="": None,
                on_input_inject=lambda text: None,
            ),
        )
        runtime.publish_presence(FakePresence(events))

        assert handles.socket_server._identity_info is identity
        assert handles.socket_server._rpc_server is handles.rpc_server
        assert identity.socket_path == "/tmp/lapis.sock"

    asyncio.run(run())

    assert events == [
        "socket.create",
        "rpc.create",
        "event_bus.register:rpc_server",
        "rpc.register:ping",
        "rpc.state_handlers",
        "state.context_identity:lapis",
        "socket.start",
        "daemon.ready:/tmp/lapis.sock",
        "presence.publish",
    ]
