"""Hub runtime services.

Owns the socket/RPC startup path that lets command and tool handlers stay on
the HubPlugin while lifecycle plumbing moves behind a focused boundary.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Optional

from .messenger import AgentSocketServer

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HubRuntimeCallbacks:
    """Callbacks the hub socket needs from HubPlugin."""

    on_message: Callable[[Any], Any]
    on_get_output: Optional[Callable[..., Any]] = None
    on_shutdown: Optional[Callable[..., Any]] = None
    on_input_inject: Optional[Callable[..., Any]] = None


@dataclass(frozen=True)
class HubRuntimeHandles:
    """Runtime handles owned by HubPlugin after startup."""

    socket_server: Any
    rpc_server: Any
    socket_path: str


class HubRuntimeServices:
    """Start hub socket/RPC runtime services in a stable order."""

    def __init__(
        self,
        *,
        socket_server_factory: Callable[..., Any] = AgentSocketServer,
        rpc_server_factory: Optional[Callable[[], Any]] = None,
        state_handler_registrar: Optional[Callable[[Any, Any], None]] = None,
        daemon_ready_signal: Optional[Callable[[str], None]] = None,
        pid_provider: Callable[[], int] = os.getpid,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._socket_server_factory = socket_server_factory
        self._rpc_server_factory = rpc_server_factory
        self._state_handler_registrar = state_handler_registrar
        self._daemon_ready_signal = daemon_ready_signal
        self._pid_provider = pid_provider
        self._clock = clock

    async def start_socket_runtime(
        self,
        *,
        identity: Any,
        display_tap: Any,
        event_bus: Any,
        callbacks: HubRuntimeCallbacks,
    ) -> HubRuntimeHandles:
        """Create the socket server, wire RPC handlers, then signal readiness."""
        socket_server = self._socket_server_factory(
            identity.agent_id,
            callbacks.on_message,
            on_get_output=callbacks.on_get_output,
            on_shutdown=callbacks.on_shutdown,
            on_input_inject=callbacks.on_input_inject,
            socket_name=identity.identity,
        )
        socket_server._display_tap = display_tap  # type: ignore[attr-defined]
        socket_server._identity_info = identity  # type: ignore[attr-defined]

        rpc_server = self._install_rpc(
            socket_server=socket_server,
            identity=identity,
            event_bus=event_bus,
        )

        socket_path = await socket_server.start()
        identity.socket_path = socket_path
        self._signal_daemon_ready(socket_path)

        return HubRuntimeHandles(
            socket_server=socket_server,
            rpc_server=rpc_server,
            socket_path=socket_path,
        )

    def publish_presence(self, presence: Any) -> None:
        """Publish presence after runtime has a real socket path."""
        presence.publish()

    def _install_rpc(self, *, socket_server: Any, identity: Any, event_bus: Any) -> Any:
        rpc_factory = self._resolve_rpc_server_factory()
        if rpc_factory is None:
            logger.debug(
                "kollabor_rpc not installed, RPC disabled (attach mode unavailable)"
            )
            return None

        rpc_started_at = self._clock()
        rpc_server = rpc_factory()
        socket_server._rpc_server = rpc_server
        if event_bus:
            event_bus.register_service("rpc_server", rpc_server)

        async def _rpc_ping(params: dict) -> dict:
            """Proof-of-life handler for attach clients."""
            identity_str = getattr(identity, "identity", "") or ""
            return {
                "status": "ok",
                "daemon_pid": self._pid_provider(),
                "uptime": self._clock() - rpc_started_at,
                "identity": identity_str,
            }

        rpc_server.register("ping", _rpc_ping)
        logger.info("rpc server initialized, ping registered")
        self._register_state_rpc_handlers(rpc_server, identity, event_bus)
        return rpc_server

    def _register_state_rpc_handlers(
        self,
        rpc_server: Any,
        identity: Any,
        event_bus: Any,
    ) -> None:
        state_service = event_bus.get_service("state_service") if event_bus else None
        if state_service is None:
            logger.debug(
                "state_service not yet on event bus, state rpc handlers deferred"
            )
            return

        try:
            registrar = self._resolve_state_handler_registrar()
            registrar(rpc_server, state_service)
            logger.info("state rpc handlers registered from hub runtime path")
        except Exception as exc:
            logger.warning(f"failed to register state rpc handlers from hub: {exc}")

        try:
            if hasattr(state_service, "set_context_identity"):
                state_service.set_context_identity(identity.identity)
        except Exception as exc:
            logger.debug(f"failed to push context identity: {exc}")

    def _resolve_rpc_server_factory(self) -> Optional[Callable[[], Any]]:
        if self._rpc_server_factory is not None:
            return self._rpc_server_factory
        try:
            from kollabor_rpc import RpcServer
        except ImportError:
            return None
        return RpcServer

    def _resolve_state_handler_registrar(self) -> Callable[[Any, Any], None]:
        if self._state_handler_registrar is not None:
            return self._state_handler_registrar
        from kollabor.state import register_state_handlers

        return register_state_handlers

    def _signal_daemon_ready(self, socket_path: str) -> None:
        try:
            signal_ready = self._daemon_ready_signal
            if signal_ready is None:
                from kollabor.daemon import signal_daemon_ready

                signal_ready = signal_daemon_ready
            signal_ready(socket_path)
        except Exception:
            pass
