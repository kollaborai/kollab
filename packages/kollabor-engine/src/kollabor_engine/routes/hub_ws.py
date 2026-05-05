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

    for ws in _connections:
        try:
            await ws.send_text(payload)
        except Exception:
            dead.append(ws)

    for ws in dead:
        _connections.discard(ws)
        logger.debug("Removed dead WebSocket connection")


def _snapshot_agents(agents: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Build agent_id -> {identity, state} snapshot for diffing."""
    return {
        a.get("agent_id", ""): {
            "agent_id": a.get("agent_id", ""),
            "identity": a.get("identity", ""),
            "state": a.get("state", ""),
            "capabilities": a.get("capabilities", []),
        }
        for a in agents
    }


async def _watcher_loop() -> None:
    """Background task that polls presence and broadcasts changes."""
    global _last_snapshot

    while True:
        try:
            await asyncio.sleep(POLL_INTERVAL)

            agents = _bridge.get_agents(use_cache=False)
            current = _snapshot_agents(agents)
            now = time.time()

            # Detect joins
            for aid, info in current.items():
                if aid not in _last_snapshot:
                    await _broadcast(
                        {
                            "type": "agent_joined",
                            "agent": info,
                            "ts": now,
                        }
                    )

            # Detect leaves
            for aid, info in _last_snapshot.items():
                if aid not in current:
                    await _broadcast(
                        {
                            "type": "agent_left",
                            "agent": info,
                            "ts": now,
                        }
                    )

            # Detect state changes
            for aid, info in current.items():
                old = _last_snapshot.get(aid)
                if old and old.get("state") != info.get("state"):
                    await _broadcast(
                        {
                            "type": "agent_state_changed",
                            "agent_id": aid,
                            "old_state": old.get("state"),
                            "new_state": info.get("state"),
                            "ts": now,
                        }
                    )

            _last_snapshot = current

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Hub watcher error: {e}")
            await asyncio.sleep(POLL_INTERVAL)


@router.websocket("/ws/hub/feed")
async def hub_feed_ws(ws: WebSocket) -> None:
    """WebSocket endpoint for real-time hub event stream.

    Sends initial snapshot on connect, then streams:
      agent_joined, agent_left, agent_state_changed
    """
    await ws.accept()
    _connections.add(ws)
    logger.info(f"WebSocket client connected (total: {len(_connections)})")

    # Start watcher if not running
    global _watcher_task
    if _watcher_task is None or _watcher_task.done():
        _watcher_task = asyncio.ensure_future(_watcher_loop())

    try:
        # Send initial snapshot
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
