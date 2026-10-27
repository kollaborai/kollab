"""The halt panels' context for one folder (spec "TUI and Web UI", R3-F7, R4-N7).

``connect-halt`` and ``connect-halt-new`` use only ``hub_halt_snapshot()``,
``hub_halt_act()`` and ``hub_halt_members()``. While the halt listener holds
``halt.lock`` and that folder is in a halt, the calls go to ``halt.sock``;
otherwise to the folder's network owner over its hub socket
(``connect_proxy``, the way ``kollab --hub`` reaches a daemon). No engine
session, no model.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import halt_runtime

NOT_HALTED = "no halted network holds that folder"


class HaltUnavailable(RuntimeError):
    """Neither the listener nor a network owner answers for this folder."""


class NoOwner(HaltUnavailable):
    """No process runs this folder's network (so the folder's own record is the only copy)."""


class HaltContext:
    def __init__(self, folder: str, root: Path | None = None):
        self.folder = folder
        self.root = root

    async def _listener(self, request: dict) -> dict | None:
        """The listener's reply, or None when no listener holds this folder."""
        if not halt_runtime.listener_running(self.root):
            return None
        reply = await halt_runtime.sock_call({**request, "folder": self.folder}, self.root)
        if not reply.get("ok") and reply.get("error") == NOT_HALTED:
            return None
        if not reply.get("ok"):
            raise HaltUnavailable(str(reply.get("error") or "the halt listener refused"))
        return reply

    async def _owner(self, op: str, args: dict) -> Any:
        from .messenger import AgentMessenger
        from .relay_owner import RelayOwnerError, WorkspaceRelayOwner

        try:
            owner = WorkspaceRelayOwner(Path(self.folder)).owner()
        except RelayOwnerError:
            owner = None
        path = (owner or {}).get("socket_path")
        if not path:
            raise NoOwner("nothing runs this folder's network")
        result = await AgentMessenger.request_connect_proxy(str(path), op, args)
        if isinstance(result, dict) and result.get("halt_error"):
            raise HaltUnavailable(str(result["halt_error"]))
        return result

    async def hub_halt_snapshot(self) -> dict:
        reply = await self._listener({"op": "describe"})
        return reply["snapshot"] if reply is not None else await self._owner("halt_snapshot", {})

    async def hub_halt_members(self) -> list[dict]:
        reply = await self._listener({"op": "members"})
        return reply["members"] if reply is not None else await self._owner("halt_members", {})

    async def hub_halt_act(self, action: str, payload: dict | None = None) -> dict:
        request = {"op": "act", "action": action, "payload": payload or {}}
        reply = await self._listener(request)
        if reply is not None:
            return {k: v for k, v in reply.items() if k != "ok"}
        return await self._owner("halt_act", {"action": action, "payload": payload or {}})
