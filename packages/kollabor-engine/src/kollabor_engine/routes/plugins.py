"""Engine access to the shared plugin inventory."""

import asyncio
import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from kollabor_config.atomic_write import write_text_atomic
from kollabor_config.file_lock import locked_file

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


def _read_usage_json(path: Path, default: dict) -> dict:
    if path.is_symlink():
        raise ValueError("Usage state cannot be a symlink")
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return default
    try:
        result = json.loads(text)
    except (UnicodeError, ValueError) as exc:
        raise ValueError("Invalid usage state") from exc
    if not isinstance(result, dict):
        raise ValueError("Invalid usage state")
    return result


def _write_usage_json(path: Path, value: dict) -> None:
    if path.is_symlink():
        raise ValueError("Usage state cannot be a symlink")
    write_text_atomic(path, json.dumps(value, ensure_ascii=False) + "\n")


def _usage_day_and_tokens(line: bytes) -> tuple[str, int]:
    try:
        item = json.loads(line)
        if not isinstance(item, dict):
            raise ValueError("Usage line must be an object")
        stamp = item["ts"]
        if isinstance(stamp, bool):
            raise ValueError("Invalid usage timestamp")
        if isinstance(stamp, (int, float)):
            date = datetime.fromtimestamp(stamp).astimezone().date()
        elif isinstance(stamp, str):
            date = datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone().date()
        else:
            raise ValueError("Invalid usage timestamp")
        if not isinstance(item["model"], str) or not isinstance(item["labels"], dict):
            raise ValueError("Invalid usage metadata")
        input_tokens, output_tokens = item["input"], item["output"]
        if any(type(value) is not int or value < 0 for value in (input_tokens, output_tokens)):
            raise ValueError("Invalid usage token count")
        return date.isoformat(), input_tokens + output_tokens
    except (KeyError, TypeError, ValueError, OverflowError, OSError, UnicodeError) as exc:
        raise ValueError("Invalid plugin usage line") from exc


def _daily_usage(run: Path, date: str) -> dict:
    state = _read_usage_json(run / f"usage-{date}.json", {"date": date, "total": 0, "run_ids": []})
    if (
        state.get("date") != date
        or type(state.get("total")) is not int
        or state["total"] < 0
        or not isinstance(state.get("run_ids"), list)
        or not isinstance(state.get("usage_sources", {}), dict)
    ):
        raise ValueError("Invalid daily plugin usage")
    return state


def _usage_anchor(stream, offset: int) -> str:
    stream.seek(max(0, offset - 64))
    return hashlib.sha256(stream.read(min(offset, 64))).hexdigest()


def _ingest_plugin_usage(run: Path, today: str) -> int:
    """Commit daily counters before the offset, retaining replay high-water marks."""
    if run.is_symlink():
        raise ValueError("Usage directory cannot be a symlink")
    run.mkdir(parents=True, exist_ok=True, mode=0o700)
    if run.is_symlink():
        raise ValueError("Usage directory cannot be a symlink")
    with locked_file(run / "usage.offset.lock", private=True):
        offset_path = run / "usage.offset"
        offset_state = _read_usage_json(offset_path, {})
        usage_path = run / "usage.jsonl"
        if usage_path.is_symlink():
            raise ValueError("Usage log cannot be a symlink")
        try:
            descriptor = os.open(usage_path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        except FileNotFoundError:
            descriptor = None
        if descriptor is not None:
            with os.fdopen(descriptor, "rb") as stream:
                stat = os.fstat(stream.fileno())
                identity = f"{stat.st_dev}:{stat.st_ino}"
                position = offset_state.get("offset", 0)
                generation = offset_state.get("generation", 0)
                if type(position) is not int or position < 0 or type(generation) is not int or generation < 0:
                    raise ValueError("Invalid plugin usage offset")
                if offset_state and not isinstance(offset_state.get("source"), str):
                    raise ValueError("Invalid plugin usage offset")
                if offset_state.get("source") != identity:
                    position, generation = 0, 0
                elif position > stat.st_size or (
                    position and offset_state.get("anchor") != _usage_anchor(stream, position)
                ):
                    position, generation = 0, generation + 1
                source = f"{identity}:{generation}"
                stream.seek(position)
                pending: dict[str, list[tuple[int, int]]] = {}
                while True:
                    line = stream.readline(1_048_577)
                    if not line:
                        break
                    if len(line) > 1_048_576:
                        raise ValueError("Plugin usage line is too large")
                    if not line.endswith(b"\n"):
                        break
                    date, tokens = _usage_day_and_tokens(line)
                    position = stream.tell()
                    pending.setdefault(date, []).append((position, tokens))
                anchor = _usage_anchor(stream, position)
            for date, records in pending.items():
                counter_path = run / f"usage-{date}.json"
                with locked_file(run / f"usage-{date}.json.lock", private=True):
                    counter = _daily_usage(run, date)
                    sources = counter.setdefault("usage_sources", {})
                    last = sources.get(source, 0)
                    if type(last) is not int or last < 0:
                        raise ValueError("Invalid daily usage offset")
                    new = [(end, amount) for end, amount in records if end > last]
                    if new:
                        counter["total"] += sum(amount for _, amount in new)
                        sources[source] = new[-1][0]
                        _write_usage_json(counter_path, counter)
            next_offset = {"source": identity, "generation": generation, "offset": position, "anchor": anchor}
            if next_offset != offset_state:
                _write_usage_json(offset_path, next_offset)
        with locked_file(run / f"usage-{today}.json.lock", private=True):
            return _daily_usage(run, today)["total"]


@router.get("/{plugin_id}/usage")
def get_plugin_usage(plugin_id: str):
    context = _package_context(plugin_id)
    if context is None:
        raise HTTPException(status_code=404, detail="Plugin not found")
    try:
        cap = context.settings.get().get("max_tokens_per_day", 2_000_000)
        if type(cap) is not int or cap < 0:
            raise ValueError("Invalid plugin daily token cap")
        today = datetime.now().astimezone().date().isoformat()
        total = _ingest_plugin_usage(context.data / "run", today)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="Invalid plugin usage data") from exc
    except OSError as exc:
        logger.warning("Cannot update plugin usage for %s: %s", plugin_id, exc)
        raise HTTPException(status_code=503, detail="Cannot update plugin usage") from exc
    return {"date": today, "total": total, "cap": cap, "remaining": max(0, cap - total)}
