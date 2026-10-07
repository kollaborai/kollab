"""WebSocket feed for real-time hub event streaming.

Provides /ws/hub/feed endpoint that pushes live roster changes,
agent messages, and status updates to connected web clients.

Uses a background watcher that polls presence files for changes
and broadcasts diffs to all active WebSocket connections.
"""

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect  # type: ignore[import-not-found]

from ..auth import validate_token
from ..hub_bridge import HubBridge

logger = logging.getLogger(__name__)
router = APIRouter(tags=["hub-websocket"])

_bridge = HubBridge()

# Active WebSocket connections
_connections: Set[WebSocket] = set()

# Watcher state
_watcher_task: Optional[asyncio.Task] = None
_last_snapshot: Dict[str, Dict[str, Any]] = {}
POLL_INTERVAL = 2.0


async def _broadcast(event: Dict[str, Any]) -> None:
    """Send event to all connected WebSocket clients."""
    payload = json.dumps(event, default=str)
    dead: List[WebSocket] = []

    # Copy: a client connecting mid-broadcast must not resize the set we iterate.
    for ws in list(_connections):
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)

    for ws in dead:
        _connections.discard(ws)
        logger.debug("Removed dead WebSocket connection")


def _snapshot_agents(agents: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Build agent_id -> {identity, state, current_task} snapshot for diffing."""
    return {
        a.get("agent_id", ""): {
            "agent_id": a.get("agent_id", ""),
            "identity": a.get("identity", ""),
            "state": a.get("state", ""),
            "current_task": a.get("current_task") or "",
            "capabilities": a.get("capabilities", []),
        }
        for a in agents
    }


def _diff_snapshots(
    old: Dict[str, Dict[str, Any]],
    current: Dict[str, Dict[str, Any]],
    now: float,
) -> List[Dict[str, Any]]:
    """Return the events that turn ``old`` into ``current``.

    Order: joins, leaves, then state/task changes. ``agent_state_changed``
    fires when either the state or the current task moved; ``new_state`` and
    ``state`` carry the same value (``state`` is the field clients read).
    """
    events: List[Dict[str, Any]] = []

    for aid, info in current.items():
        if aid not in old:
            events.append({"type": "agent_joined", "agent": info, "ts": now})

    for aid, info in old.items():
        if aid not in current:
            events.append({"type": "agent_left", "agent": info, "ts": now})

    for aid, info in current.items():
        prev = old.get(aid)
        if prev and (
            prev.get("state") != info.get("state")
            or prev.get("current_task") != info.get("current_task")
        ):
            events.append(
                {
                    "type": "agent_state_changed",
                    "agent_id": aid,
                    "identity": info.get("identity"),
                    "state": info.get("state"),
                    "old_state": prev.get("state"),
                    "new_state": info.get("state"),
                    "current_task": info.get("current_task"),
                    "ts": now,
                }
            )

    return events


async def _watcher_loop() -> None:
    """Background task that polls presence and broadcasts changes."""
    global _last_snapshot

    while True:
        try:
            await asyncio.sleep(POLL_INTERVAL)

            agents = _bridge.get_agents(use_cache=False)
            current = _snapshot_agents(agents)

            for event in _diff_snapshots(_last_snapshot, current, time.time()):
                await _broadcast(event)

            _last_snapshot = current

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Hub watcher error: {e}")
            await asyncio.sleep(POLL_INTERVAL)


def _token_ok(ws: WebSocket) -> bool:
    """Check ``?token=``: browsers cannot set headers on a WebSocket.

    server.py's HTTP auth middleware never runs for websocket scope, so this is
    the only gate. ``validate_token`` carries the same pytest-only bypass.
    """
    try:
        return validate_token(ws.query_params.get("token", ""))
    except TypeError:  # secrets.compare_digest rejects non-ASCII str
        return False


@router.websocket("/ws/hub/feed")
async def hub_feed_ws(ws: WebSocket) -> None:
    """WebSocket endpoint for real-time hub event stream.

    Requires ``?token=<engine token>``; anything else is closed with 1008.
    Sends initial snapshot on connect, then streams:
      agent_joined, agent_left, agent_state_changed
    agent_state_changed fires on a state or current_task change and carries
    identity, state (new), old_state, new_state and current_task.
    """
    global _watcher_task, _last_snapshot

    await ws.accept()
    if not _token_ok(ws):
        # Accepted first so the browser sees code 1008; a refused handshake
        # surfaces as a bare 1006. Nothing is sent or registered before this.
        await ws.close(code=1008)
        return
    _connections.add(ws)
    logger.info(f"WebSocket client connected (total: {len(_connections)})")

    try:
        agents = _bridge.get_agents(use_cache=False)

        # Start watcher if not running. Its baseline is what this client is
        # about to be told; an empty or stale one would replay every live
        # agent as a join on the first poll.
        if _watcher_task is None or _watcher_task.done():
            _last_snapshot = _snapshot_agents(agents)
            _watcher_task = asyncio.ensure_future(_watcher_loop())

        # Send initial snapshot
        await ws.send_text(
            json.dumps(
                {
                    "type": "hub_snapshot",
                    "agents": agents,
                    "count": len(agents),
                }
            )
        )

        # Keep connection alive, listen for client commands
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            cmd = msg.get("command", "")

            if cmd == "refresh":
                agents = _bridge.get_agents(use_cache=False)
                await ws.send_text(
                    json.dumps(
                        {
                            "type": "hub_snapshot",
                            "agents": agents,
                            "count": len(agents),
                        }
                    )
                )

            elif cmd == "ping":
                await ws.send_text(json.dumps({"type": "pong"}))

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.debug(f"WebSocket error: {e}")
    finally:
        _connections.discard(ws)
        logger.info(f"WebSocket client disconnected (total: {len(_connections)})")

        # Stop watcher if no connections
        if not _connections and _watcher_task and not _watcher_task.done():
            _watcher_task.cancel()
            _watcher_task = None
