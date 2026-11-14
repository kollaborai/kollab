"""Engine access to the shared plugin inventory."""

import asyncio
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/plugins", tags=["plugins"])


def remote_plugin_route_allowed(method: str, path: str) -> bool:
    """Allow only the explicitly remote-readable plugin surface.

    Plugin API methods need a second endpoint-level local-only check in the
    engine; callers must mark the request as remote when forwarding them.
    """
    parts = path.strip("/").split("/")
    if not parts or parts[0] != "plugins":
        return True
    if len(parts) == 1:
        return method == "GET"
    if len(parts) == 2:
        return method == "GET" and parts[1] == "events"
    if parts[1] in {"install", "jobs", "dev", "catalog", "update"}:
        return False
    tail = parts[2:]
    if tail[0] == "api" and len(tail) >= 2:
        return True
    if tail == ["events"]:
        return method in {"GET", "POST"}
    if method != "GET":
        return False
    return tail in (["settings"], ["agents"], ["usage"]) or (
        len(tail) == 3 and tail[0] == "services" and tail[2] == "log"
    )


class PluginEventHub:
    """Bounded, per-engine in-memory streams; disconnected subscribers are removed."""

    def __init__(self):
        self._lock = threading.Lock()
        self._subscribers: dict[str | None, dict[asyncio.Queue, asyncio.AbstractEventLoop]] = {}
        self._heartbeats: dict[tuple[str, str], float] = {}

    def subscribe(self, plugin_id: str | None) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        loop = asyncio.get_running_loop()
        with self._lock:
            self._subscribers.setdefault(plugin_id, {})[queue] = loop
        return queue

    def unsubscribe(self, plugin_id: str | None, queue: asyncio.Queue) -> None:
        with self._lock:
            subscribers = self._subscribers.get(plugin_id, {})
            subscribers.pop(queue, None)
            if not subscribers:
                self._subscribers.pop(plugin_id, None)

    def subscriber_count(self, plugin_id: str | None) -> int:
        with self._lock:
            return len(self._subscribers.get(plugin_id, {}))

    @staticmethod
    def _enqueue(queue: asyncio.Queue, event: dict) -> None:
        if queue.full():
            queue.get_nowait()
        queue.put_nowait(event)

    def publish(self, plugin_id: str | None, topic: str, data: Any) -> dict:
        event = {"topic": topic, "data": data, "ts": time.time()}
        with self._lock:
            if plugin_id is not None and topic == "heartbeat" and isinstance(data, dict):
                service = data.get("service")
                if isinstance(service, str):
                    self._heartbeats[(plugin_id, service)] = event["ts"]
            subscribers = tuple(self._subscribers.get(plugin_id, {}).items())
        for queue, loop in subscribers:
            try:
                loop.call_soon_threadsafe(self._enqueue, queue, event)
            except RuntimeError:
                self.unsubscribe(plugin_id, queue)
                logger.debug("Removed plugin event subscriber with a closed loop")
        return event

    def publish_change(self) -> dict:
        return self.publish(None, "plugins-changed", {})

    def heartbeat(self, plugin_id: str, service: str) -> float | None:
        with self._lock:
            return self._heartbeats.get((plugin_id, service))


def _event_response(hub: PluginEventHub, plugin_id: str | None) -> EventSourceResponse:
    async def events():
        queue = hub.subscribe(plugin_id)
        try:
            while True:
                yield {"data": json.dumps(await queue.get(), ensure_ascii=False)}
        finally:
            hub.unsubscribe(plugin_id, queue)

    return EventSourceResponse(events())


@router.get("")
def list_plugins(request: Request):
    from kollabor_plugins.registry import plugin_entries

    entries = plugin_entries()
    for entry in entries:
        plugin_id = entry.get("id")
        for service in entry.get("services", []):
            if isinstance(service, dict) and isinstance(service.get("name"), str):
                service["heartbeat"] = request.app.state.plugin_events.heartbeat(plugin_id, service["name"])
    return {"schema": 1, "plugins": entries}


@router.get("/events")
async def plugin_changes(request: Request):
    return _event_response(request.app.state.plugin_events, None)


class PluginEvent(BaseModel):
    topic: str = Field(min_length=1, max_length=200)
    data: Any


@router.get("/{plugin_id}/events")
async def plugin_events(request: Request, plugin_id: str):
    if _package_context(plugin_id) is None:
        raise HTTPException(status_code=404, detail="Plugin not found")
    return _event_response(request.app.state.plugin_events, plugin_id)


@router.post("/{plugin_id}/events")
async def post_plugin_event(request: Request, plugin_id: str, event: PluginEvent):
    context = _package_context(plugin_id)
    if context is None:
        raise HTTPException(status_code=404, detail="Plugin not found")
    if event.topic == "heartbeat":
        services = context.manifest.get("services", {})
        service = event.data.get("service") if isinstance(event.data, dict) else None
        if not isinstance(services, dict) or not isinstance(service, str) or service not in services:
            raise HTTPException(status_code=422, detail="Unknown heartbeat service")
    try:
        serialized = json.dumps(event.data, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="Invalid plugin event data") from exc
    if len(serialized.encode("utf-8")) > 65536:
        raise HTTPException(status_code=413, detail="Plugin event data is too large")
    request.app.state.plugin_events.publish(plugin_id, event.topic, event.data)
    return {"ok": True}


def _package_context(plugin_id: str):
    """Resolve an installed or attached package without importing its code."""
    from kollabor_plugins.context import PluginContext
    from kollabor_plugins.packages import _read_registry
    from kollabor_plugins.settings import _valid_data_id

    if not _valid_data_id(plugin_id):
        return None
    home = Path.home() / ".kollab"
    try:
        registry = _read_registry(home / "plugins.json")
    except ValueError as exc:
        raise HTTPException(status_code=503, detail="Cannot read plugin registry") from exc
    if plugin_id.endswith("@dev"):
        folder = registry["dev"].get(plugin_id.removesuffix("@dev"))
        if not isinstance(folder, str):
            return None
        root = Path(folder)
    else:
        if not isinstance(registry["plugins"].get(plugin_id), dict):
            return None
        root = home / "plugins" / plugin_id
    try:
        manifest = json.loads((root / "kollab-plugin.json").read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or manifest.get("id") != plugin_id.removesuffix("@dev"):
            raise ValueError("Invalid plugin manifest")
    except (OSError, UnicodeError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="Cannot read plugin manifest") from exc
    return PluginContext(plugin_id, root, home / "plugin-data" / plugin_id, manifest)


@router.get("/{plugin_id}/settings")
def get_plugin_settings(plugin_id: str):
    """Read package settings or the public fields of a built-in plugin."""
    context = _package_context(plugin_id)
    if context is not None:
        try:
            return context.settings.get()
        except (OSError, UnicodeError, ValueError) as exc:
            raise HTTPException(status_code=409, detail="Cannot read plugin settings") from exc

    from kollabor_plugins.registry import plugin_entries

    for entry in plugin_entries():
        if entry.get("id") != plugin_id or entry.get("kind") != "built-in":
            continue
        return {
            field["path"]: field.get("value")
            for field in entry.get("settings", [])
            if isinstance(field, dict)
            and isinstance(field.get("path"), str)
            and not field["path"].endswith(".enabled")
            and not field.get("secret")
            and field.get("type") != "label"
        }
    raise HTTPException(status_code=404, detail="Plugin not found")
