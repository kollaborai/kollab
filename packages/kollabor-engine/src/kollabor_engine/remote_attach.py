"""Open an agent on another computer of this folder's network, by agent@device.

This folder's network runs in one local daemon. Any live session's daemon here
forwards the open to it (plugins/hub/network_attach.py) and answers a one-shot
socket, which ``DaemonPool.adopt`` attaches on exactly as it attaches a local
agent's own socket: history, live turns and input work the same. The other
computer decides who may open its agents.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


async def open_remote_agent(handle: str, registry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The adopt row for ``handle``, or None when it is not an agent@device handle.

    Raises RuntimeError with the reason the agent cannot be opened, in the
    other computer's own words when it refused.
    """
    from plugins.hub.device_names import format_handle, parse_handle
    from plugins.hub.messenger import AgentMessenger

    parsed = parse_handle(handle)
    if parsed is None:
        return None
    asker = next(
        (
            session
            for session in registry.values()
            if session.alive
            and not getattr(session, "solo", False)
            and not getattr(session, "device", "")
            and getattr(session.daemon, "socket_path", "")
        ),
        None,
    )
    if asker is None:
        raise RuntimeError("no chat here runs this folder's network yet; open one first")
    reply = await AgentMessenger.request_network_attach(
        asker.daemon.socket_path, format_handle(*parsed)
    )
    if reply.get("type") != "network_attach" or not reply.get("socket_path"):
        raise RuntimeError(str(reply.get("msg") or "the other computer did not answer"))
    name, device = parsed
    return {
        "session_id": handle,
        "identity": name,
        "agent": name,
        "name": name,
        "workspace": getattr(asker, "workspace", "") or "",
        "socket_path": reply["socket_path"],
        "daemon_pid": 0,
        "device": device,
    }
