"""Open an agent on another computer of this folder's network, by agent@device.

This folder's network runs in one local daemon. Any live session's daemon here
forwards the open to it (plugins/hub/network_attach.py) and answers a one-shot
socket, which ``DaemonPool.adopt`` attaches on exactly as it attaches a local
agent's own socket: history, live turns and input work the same. The other
computer decides who may open its agents.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional


async def open_remote_agent(handle: str, registry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The adopt row for ``handle``, or None when it is not an agent@device handle.

    Asked of a chat here whose own network lists the agent, else of any live
    agent on this computer whose network does (a terminal, a detached daemon:
    after a restart the engine has no chat open). Device names are unique
    within one network only, so one on another folder's network could name a
    different computer. Raises RuntimeError with the reason the agent cannot
    be opened, in the other computer's own words when it refused.
    """
    from plugins.hub.device_names import format_handle, parse_handle
    from plugins.hub.messenger import AgentMessenger

    from .hub_bridge import network_daemons

    parsed = parse_handle(handle)
    if parsed is None:
        return None
    wanted = format_handle(*parsed)

    def lists(rows: Any) -> bool:
        return any(
            (row.get("handle") or format_handle(row.get("name", ""), row.get("device", ""))) == wanted
            for row in rows or []
        )

    chats = [
        session
        for session in registry.values()
        if session.alive
        and not getattr(session, "solo", False)
        and not getattr(session, "device", "")
        and getattr(session.daemon, "socket_path", "")
    ]
    asker = None  # (socket, identity, folder) of the agent that asks for us
    for session in chats:
        try:
            snapshot = await asyncio.wait_for(session.state.get_hub_state(), timeout=3)
        except Exception:
            continue
        if lists(snapshot.remote):
            asker = (session.daemon.socket_path, getattr(session.daemon, "identity", ""), session.workspace)
            break
    if asker is None:
        # The agents no chat has open, by the rule GET /sessions shows them by.
        skip = {
            key
            for session in chats
            for key in (getattr(session, "session_id", ""), getattr(session.daemon, "pid", 0))
            if key
        }
        on_network = False
        async for row, status in network_daemons(skip):
            on_network = True
            if lists(status.get("agents")):
                asker = (row["socket_path"], row.get("identity", ""), row.get("workspace", ""))
                break
        if asker is None and not chats and not on_network:
            raise RuntimeError("no agent on this computer is on a network right now")
    if asker is None:
        raise RuntimeError(f"{wanted} is not on the network right now")
    socket_path, identity, workspace = asker
    reply = await AgentMessenger.request_network_attach(socket_path, wanted)
    if reply.get("type") == "timeout":
        # One started before network attach existed ignores the request.
        who = (identity or "the agent").capitalize()
        raise RuntimeError(
            f"{who} in {workspace} runs this folder's network and did not answer. "
            "Restart it if it started before kollab was updated."
        )
    if reply.get("type") != "network_attach" or not reply.get("socket_path"):
        raise RuntimeError(str(reply.get("msg") or "the other computer did not answer"))
    name, device = parsed
    return {
        "session_id": handle,
        "identity": name,
        "agent": name,
        "name": name,
        "socket_path": reply["socket_path"],
        "daemon_pid": 0,
        "device": device,
    }
