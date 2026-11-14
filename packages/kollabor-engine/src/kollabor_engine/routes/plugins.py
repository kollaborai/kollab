"""Engine access to the shared plugin inventory."""

import asyncio
import hashlib
import importlib.abc
import importlib.machinery
import inspect
import json
import logging
import os
import secrets
import stat
import sys
import threading
import time
import types
import uuid
from contextvars import ContextVar
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request, WebSocketException
from fastapi.responses import JSONResponse
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
    if "\\" in path or "\x00" in path or "%" in path or any(part in {"", ".", ".."} for part in parts):
        return False
    method = method.upper()
    if len(parts) == 1:
        return method == "GET"
    if len(parts) == 2:
        return method == "GET" and parts[1] == "events"
    if parts[1] == "jobs" and (len(parts) == 3 or (len(parts) == 4 and parts[3] == "confirm")):
        return False
    tail = parts[2:]
    if tail[0] == "api" and len(tail) >= 2:
        return True
    if tail == ["events"]:
        return method in {"GET", "POST"}
    if tail == ["agents"]:
        return method == "POST"
    if len(tail) == 2 and tail[0] == "agents":
        return method in {"GET", "DELETE"}
    if method != "GET":
        return False
    return tail in (["settings"], ["usage"]) or (
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
        error = request.app.state.plugin_dispatcher.errors.get(plugin_id)
        if error and entry.get("state") == "ok":
            entry["state"], entry["error"] = "error", error
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
    try:
        _validate_plugin_event(context.manifest, event.topic, event.data)
    except OverflowError as exc:
        raise HTTPException(status_code=413, detail="Plugin event data is too large") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
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


def _validate_plugin_event(manifest: dict, topic: str, data: Any) -> None:
    if not isinstance(topic, str) or not 1 <= len(topic) <= 200:
        raise ValueError("Invalid plugin event topic")
    if topic == "heartbeat":
        services = manifest.get("services", {})
        service = data.get("service") if isinstance(data, dict) else None
        if not isinstance(services, dict) or not isinstance(service, str) or service not in services:
            raise ValueError("Unknown heartbeat service")
    try:
        serialized = json.dumps(data, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError("Invalid plugin event data") from exc
    if len(serialized.encode("utf-8")) > 65536:
        raise OverflowError("Plugin event data is too large")


_remote_plugin_request: ContextVar[str | None] = ContextVar("remote_plugin_request", default=None)


def _local_guard(endpoint):
    def check():
        remote = _remote_plugin_request.get()
        if remote == "websocket":
            raise WebSocketException(code=1008, reason="Plugin route is local only")
        if remote:
            raise HTTPException(status_code=403, detail="Plugin route is local only")

    if inspect.iscoroutinefunction(endpoint):
        @wraps(endpoint)
        async def async_guarded(*args, **kwargs):
            check()
            return await endpoint(*args, **kwargs)

        return async_guarded

    @wraps(endpoint)
    def guarded(*args, **kwargs):
        check()
        return endpoint(*args, **kwargs)

    return guarded



def _engine_context(plugin_id: str, hub: PluginEventHub):
    """Create capabilities only for an enabled, still-approved package."""
    from kollabor_plugins.context import PluginContext
    from kollabor_plugins.packages import _read_registry
    from kollabor_plugins.services import _approved_service_root

    context = _package_context(plugin_id)
    if context is None:
        raise HTTPException(status_code=404, detail="Plugin not found")
    home = Path.home() / ".kollab"
    try:
        registry = _read_registry(home / "plugins.json")
        root, record = _approved_service_root(home, registry, plugin_id)
        if not plugin_id.endswith("@dev") and record.get("enabled") is not True:
            raise HTTPException(status_code=403, detail="Plugin is disabled")
        granted = record.get("granted", [])
        if not isinstance(granted, list) or any(not isinstance(grant, str) for grant in granted):
            raise ValueError("Invalid plugin grants")
        if root.resolve() != context.root.resolve():
            raise ValueError("Plugin root changed")
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=409, detail="Plugin approval or files changed") from exc

    def publish(own_id, topic, data):
        _validate_plugin_event(context.manifest, topic, data)
        return hub.publish(own_id, topic, data)

    def notify(own_id, text, button):
        if not isinstance(text, str) or not text.strip() or "\n" in text or "\r" in text:
            raise ValueError("Invalid plugin notice text")
        payload = {"plugin_id": own_id, "text": text, "button": button}
        _validate_plugin_event(context.manifest, "notice", payload)
        return hub.publish(None, "notice", payload)

    return PluginContext(
        plugin_id, root, context.data, context.manifest, grants=granted,
        publish=publish, notify=notify, heartbeat=hub.heartbeat, local_guard=_local_guard,
    )



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


def _usage_date(stamp: Any) -> str:
    if isinstance(stamp, bool):
        raise ValueError("Invalid usage timestamp")
    if isinstance(stamp, (int, float)):
        return datetime.fromtimestamp(stamp).astimezone().date().isoformat()
    if isinstance(stamp, str):
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).astimezone().date().isoformat()
    raise ValueError("Invalid usage timestamp")


def _usage_day_and_tokens(line: bytes) -> tuple[str, int]:
    try:
        item = json.loads(line)
        if not isinstance(item, dict):
            raise ValueError("Usage line must be an object")
        date = _usage_date(item["ts"])
        if not isinstance(item["model"], str) or not isinstance(item["labels"], dict):
            raise ValueError("Invalid usage metadata")
        input_tokens, output_tokens = item["input"], item["output"]
        if any(type(value) is not int or value < 0 for value in (input_tokens, output_tokens)):
            raise ValueError("Invalid usage token count")
        return date, input_tokens + output_tokens
    except (KeyError, TypeError, ValueError, OverflowError, OSError, UnicodeError) as exc:
        raise ValueError("Invalid plugin usage line") from exc


def _daily_usage(run: Path, date: str) -> dict:
    state = _read_usage_json(run / f"usage-{date}.json", {"date": date, "total": 0, "run_ids": []})
    if (
        state.get("date") != date
        or type(state.get("total")) is not int
        or state["total"] < 0
        or not isinstance(state.get("run_ids"), list)
        or any(not isinstance(run_id, str) for run_id in state["run_ids"])
        or len(set(state["run_ids"])) != len(state["run_ids"])
        or not isinstance(state.get("usage_sources", {}), dict)
    ):
        raise ValueError("Invalid daily plugin usage")
    return state


def _reconcile_finished_runs(run: Path) -> None:
    with locked_file(run / "runs.json.lock", private=True):
        state = _read_usage_json(run / "runs.json", {"runs": []})
    rows = state.get("runs")
    if not isinstance(rows, list):
        raise ValueError("Invalid plugin runs")
    seen: set[str] = set()
    by_date: dict[str, list[tuple[str, int]]] = {}
    try:
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError("Invalid plugin run")
            run_id = row["run_id"]
            status = row["status"]
            if not isinstance(run_id, str) or not run_id or run_id in seen:
                raise ValueError("Invalid or duplicate plugin run id")
            seen.add(run_id)
            if status == "running":
                continue
            if status not in {"done", "timeout", "capped", "failed", "cancelled"}:
                raise ValueError("Invalid plugin run status")
            date = _usage_date(row["finished_at"])
            usage = row["result"]["usage"]
            input_tokens, output_tokens = usage["input"], usage["output"]
            if any(type(value) is not int or value < 0 for value in (input_tokens, output_tokens)):
                raise ValueError("Invalid plugin run usage")
            by_date.setdefault(date, []).append((run_id, input_tokens + output_tokens))
    except (KeyError, TypeError, ValueError, OverflowError, OSError) as exc:
        raise ValueError("Invalid plugin runs") from exc
    for date, records in by_date.items():
        path = run / f"usage-{date}.json"
        with locked_file(run / f"usage-{date}.json.lock", private=True):
            counter = _daily_usage(run, date)
            counted = set(counter["run_ids"])
            new = [(run_id, amount) for run_id, amount in records if run_id not in counted]
            if new:
                counter["total"] += sum(amount for _, amount in new)
                counter["run_ids"].extend(run_id for run_id, _ in new)
                _write_usage_json(path, counter)



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
                while True:
                    pending: dict[str, list[tuple[int, int]]] = {}
                    reached_end = False
                    for _ in range(512):
                        remaining = stat.st_size - stream.tell()
                        if remaining <= 0:
                            reached_end = True
                            break
                        line = stream.readline(min(1_048_577, remaining))
                        if not line:
                            reached_end = True
                            break
                        if len(line) > 1_048_576:
                            raise ValueError("Plugin usage line is too large")
                        if not line.endswith(b"\n"):
                            reached_end = True
                            break
                        date, tokens = _usage_day_and_tokens(line)
                        position = stream.tell()
                        pending.setdefault(date, []).append((position, tokens))
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
                    anchor = _usage_anchor(stream, position)
                    next_offset = {"source": identity, "generation": generation, "offset": position, "anchor": anchor}
                    if next_offset != offset_state:
                        _write_usage_json(offset_path, next_offset)
                        offset_state = next_offset
                    if reached_end:
                        break
        _reconcile_finished_runs(run)
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


def _plugin_token_target(method: str, path: str) -> str | None:
    """Return the own-plugin id only for routes that accept a plugin token."""
    from kollabor_plugins.settings import _valid_data_id

    if not path.startswith("/") or "\\" in path or "\x00" in path or "%" in path:
        return None
    parts = path.split("/")[1:]
    if any(part in {"", ".", ".."} for part in parts):
        return None
    if len(parts) < 3 or parts[0] != "plugins" or not _valid_data_id(parts[1]):
        return None
    tail = parts[2:]
    method = method.upper()
    allowed = (
        (len(tail) >= 2 and tail[0] == "api")
        or (tail == ["events"] and method in {"GET", "POST"})
        or (tail == ["usage"] and method == "GET")
        or (tail == ["settings"] and method in {"GET", "PUT"})
        or (tail == ["agents"] and method == "POST")
        or (len(tail) == 2 and tail[0] == "agents" and method in {"GET", "DELETE"})
        or (len(tail) == 3 and tail[0] == "services" and tail[2] in {"start", "stop", "restart"} and method == "POST")
        or (len(tail) == 3 and tail[0] == "services" and tail[2] == "log" and method == "GET")
    )
    return parts[1] if allowed else None


class PluginTokenStore:
    """Stable, private plugin tokens; reading never creates credentials."""

    @staticmethod
    def _run(context, create: bool) -> Path:
        data_root = Path.home() / ".kollab" / "plugin-data"
        data = data_root / context.id
        if context.data != data:
            raise ValueError("Invalid plugin data directory")
        run = data / "run"
        for path in (data_root, data, run):
            if path.is_symlink():
                raise ValueError("Plugin token directory cannot be a symlink")
        if create:
            run.mkdir(parents=True, exist_ok=True, mode=0o700)
            for path in (data_root, data, run):
                if path.is_symlink():
                    raise ValueError("Plugin token directory cannot be a symlink")
        return run

    @staticmethod
    def _read(path: Path) -> str:
        if path.is_symlink():
            raise ValueError("Plugin token cannot be a symlink")
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.getuid():
                raise ValueError("Plugin token file must be private")
            raw = os.read(descriptor, 128)
        finally:
            os.close(descriptor)
        try:
            token = raw.decode("ascii")
        except UnicodeError as exc:
            raise ValueError("Invalid plugin token") from exc
        if len(token) != 43 or not all(ch.isascii() and (ch.isalnum() or ch in "-_") for ch in token):
            raise ValueError("Invalid plugin token")
        return token

    def mint(self, context) -> str:
        run = self._run(context, create=True)
        path = run / "token"
        with locked_file(run / "token.lock", private=True):
            if path.is_symlink():
                raise ValueError("Plugin token cannot be a symlink")
            if not path.exists():
                write_text_atomic(path, secrets.token_urlsafe(32))
            return self._read(path)

    def authorize(self, token: str, method: str, path: str, hub: PluginEventHub) -> bool:
        from kollabor_plugins.packages import _read_registry

        plugin_id = _plugin_token_target(method, path)
        if plugin_id is None:
            return False
        try:
            context = _engine_context(plugin_id, hub)
            registry = _read_registry(Path.home() / ".kollab" / "plugins.json")
            if not plugin_id.endswith("@dev") and plugin_id in registry["dev"]:
                return False
            expected = self._read(self._run(context, create=False) / "token")
            return secrets.compare_digest(token, expected)
        except (HTTPException, OSError, TypeError, ValueError):
            return False



class _NoBytecodeLoader(importlib.machinery.SourceFileLoader):
    """Do not mutate approved plugin files when importing vendored Python."""

    def get_code(self, fullname):
        return self.source_to_code(self.get_data(self.path), self.path)


class _VendorFinder(importlib.abc.MetaPathFinder):
    def __init__(self, vendor: Path, names: set[str]):
        self.vendor = vendor
        self.names = names

    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".", 1)[0] not in self.names:
            return None
        search = path if path is not None else [str(self.vendor)]
        spec = importlib.machinery.PathFinder.find_spec(fullname, search, target)
        if spec is None:
            return None
        if spec.origin:
            try:
                inside = Path(spec.origin).resolve().is_relative_to(self.vendor)
            except OSError:
                inside = False
            if not inside or not isinstance(spec.loader, importlib.machinery.SourceFileLoader):
                raise ImportError("Vendored modules must be pure Python inside the plugin")
            spec.loader = _NoBytecodeLoader(fullname, spec.origin)
        elif spec.submodule_search_locations and any(
            not Path(location).resolve().is_relative_to(self.vendor)
            for location in spec.submodule_search_locations
        ):
            raise ImportError("Vendored package escapes the plugin")
        return spec


_vendor_guard = threading.RLock()
_vendor_refs: dict[Path, int] = {}


def _acquire_vendor(root: Path):
    from kollabor_plugins.registry import _prepare_vendor, _vendor_owners

    with _vendor_guard:
        vendor, names = _prepare_vendor(root)
        if vendor is None:
            return None
        finder = _VendorFinder(vendor, names)
        for name in names:
            _vendor_owners[name] = vendor
        _vendor_refs[vendor] = _vendor_refs.get(vendor, 0) + 1
        sys.meta_path.insert(0, finder)
        return vendor, names, finder


def _release_vendor(reservation) -> None:
    from kollabor_plugins.registry import _vendor_owners

    if reservation is None:
        return
    vendor, names, finder = reservation
    with _vendor_guard:
        if finder in sys.meta_path:
            sys.meta_path.remove(finder)
        count = _vendor_refs.get(vendor, 0) - 1
        if count > 0:
            _vendor_refs[vendor] = count
            return
        _vendor_refs.pop(vendor, None)
        for name in names:
            if _vendor_owners.get(name) == vendor:
                _vendor_owners.pop(name, None)
        for name in list(sys.modules):
            if name.split(".", 1)[0] not in names:
                continue
            module = sys.modules.get(name)
            location = getattr(module, "__file__", None)
            paths = getattr(module, "__path__", ())
            try:
                inside = bool(location and Path(location).resolve().is_relative_to(vendor)) or (
                    not location and bool(paths) and all(Path(path).resolve().is_relative_to(vendor) for path in paths)
                )
            except (OSError, TypeError):
                inside = False
            if inside:
                sys.modules.pop(name, None)


class _LoadedPlugin:
    def __init__(self, app, lifecycle, revision, module_name, vendor):
        self.app = app
        self.lifecycle = lifecycle
        self.revision = revision
        self.module_name = module_name
        self.vendor = vendor
        self.active = 0


class PluginDispatcher:
    """A single ASGI mount that resolves approved routers on each request."""

    def __init__(self, hub: PluginEventHub, tokens: PluginTokenStore):
        self.hub = hub
        self.tokens = tokens
        self.errors: dict[str, str] = {}
        self._condition = asyncio.Condition()
        self._loaded: dict[str, _LoadedPlugin] = {}
        self._retired: dict[str, list[_LoadedPlugin]] = {}
        self._closing = False

    async def _close(self, loaded: _LoadedPlugin) -> None:
        try:
            await asyncio.wait_for(loaded.lifecycle.__aexit__(None, None, None), timeout=5)
        except Exception as exc:
            logger.warning("Plugin engine shutdown failed: %s", type(exc).__name__)
        finally:
            sys.modules.pop(loaded.module_name, None)
            _release_vendor(loaded.vendor)

    async def _retire(self, plugin_id: str) -> None:
        loaded = self._loaded.pop(plugin_id, None)
        if loaded is None:
            return
        if loaded.active:
            self._retired.setdefault(plugin_id, []).append(loaded)
        else:
            await self._close(loaded)

    async def _load(self, context, revision: str, entrypoint: str) -> _LoadedPlugin:
        from kollabor_plugins.packages import _SYMBOL, _within

        if not isinstance(entrypoint, str) or entrypoint.count(":") != 1:
            raise ValueError("Invalid engine entry")
        filename, symbol = entrypoint.split(":")
        if not _SYMBOL.fullmatch(symbol):
            raise ValueError("Invalid engine symbol")
        path = _within(context.root.resolve(), filename, "engine")
        if not path.is_file():
            raise ValueError("Engine entry is not a file")
        try:
            vendor = _acquire_vendor(context.root)
        except ValueError as exc:
            raise ValueError("Vendored module name clash or invalid vendor") from exc
        module_name = f"_kollab_plugin_engine_{uuid.uuid4().hex}"
        try:
            module = types.ModuleType(module_name)
            module.__file__ = str(path)
            module.__package__ = ""
            sys.modules[module_name] = module
            exec(compile(path.read_bytes(), str(path), "exec"), module.__dict__)
            factory = getattr(module, symbol)
            router = factory(context)
            if not isinstance(router, APIRouter):
                raise ValueError("Engine factory must return an APIRouter")
            subapp = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
            subapp.include_router(router)
            lifecycle = subapp.router.lifespan_context(subapp)
            await lifecycle.__aenter__()
            return _LoadedPlugin(subapp, lifecycle, revision, module_name, vendor)
        except BaseException as exc:
            sys.modules.pop(module_name, None)
            _release_vendor(vendor)
            if isinstance(exc, Exception):
                raise RuntimeError(f"Plugin engine initialization failed ({type(exc).__name__})") from exc
            raise

    async def _acquire(self, plugin_id: str) -> _LoadedPlugin:
        from kollabor_plugins.packages import _read_registry, files_sha256
        from kollabor_plugins.services import redact_log

        async with self._condition:
            if self._closing:
                raise HTTPException(status_code=503, detail="Plugin dispatcher is shutting down")
            if "--no-plugins" in sys.argv[1:]:
                raise HTTPException(status_code=404, detail="Plugins are disabled")
            try:
                context = _engine_context(plugin_id, self.hub)
                context.token = self.tokens.mint(context)
                registry = _read_registry(Path.home() / ".kollab" / "plugins.json")
                if not plugin_id.endswith("@dev") and plugin_id in registry["dev"]:
                    raise HTTPException(status_code=404, detail="Plugin is shadowed")
                entrypoint = context.manifest.get("engine")
                if entrypoint is None:
                    raise HTTPException(status_code=404, detail="Plugin has no engine entry")
                if plugin_id.endswith("@dev"):
                    revision = files_sha256(context.root)
                else:
                    revision = registry["plugins"][plugin_id]["files_sha256"]
            except HTTPException as exc:
                await self._retire(plugin_id)
                if exc.status_code in {403, 404}:
                    self.errors.pop(plugin_id, None)
                    raise HTTPException(status_code=404, detail="Plugin is unavailable") from exc
                raise
            except (OSError, ValueError, KeyError) as exc:
                await self._retire(plugin_id)
                raise HTTPException(status_code=409, detail="Plugin approval or files changed") from exc
            loaded = self._loaded.get(plugin_id)
            if loaded is not None and loaded.revision != revision:
                await self._retire(plugin_id)
                loaded = None
            while self._retired.get(plugin_id):
                await self._condition.wait()
                if self._closing:
                    raise HTTPException(status_code=503, detail="Plugin dispatcher is shutting down")
            if loaded is None:
                try:
                    loaded = await self._load(context, revision, entrypoint)
                except Exception as exc:
                    message = redact_log(str(exc).strip())[:500] or type(exc).__name__
                    self.errors[plugin_id] = message
                    logger.warning("Plugin %s engine failed to load: %s", plugin_id, type(exc).__name__)
                    raise HTTPException(status_code=503, detail="Plugin engine failed to load") from exc
                self._loaded[plugin_id] = loaded
            self.errors.pop(plugin_id, None)
            loaded.active += 1
            return loaded

    async def _release(self, plugin_id: str, loaded: _LoadedPlugin) -> None:
        async with self._condition:
            loaded.active -= 1
            retired = self._retired.get(plugin_id, [])
            if loaded.active == 0 and loaded in retired:
                retired.remove(loaded)
                await self._close(loaded)
                if not retired:
                    self._retired.pop(plugin_id, None)
                self._condition.notify_all()

    async def warm(self) -> None:
        """Load enabled routers so startup failures appear in plugin inventory."""
        from kollabor_plugins.packages import _read_registry

        if "--no-plugins" in sys.argv[1:]:
            return
        try:
            registry = _read_registry(Path.home() / ".kollab" / "plugins.json")
        except ValueError:
            logger.warning("Cannot read plugin registry during engine startup")
            return
        ids = [
            plugin_id for plugin_id, record in registry["plugins"].items()
            if isinstance(record, dict) and record.get("enabled") is True and plugin_id not in registry["dev"]
        ]
        ids.extend(f"{plugin_id}@dev" for plugin_id in registry["dev"] if isinstance(plugin_id, str))
        for plugin_id in ids:
            try:
                loaded = await self._acquire(plugin_id)
                await self._release(plugin_id, loaded)
            except HTTPException as exc:
                if exc.status_code not in {404, 503}:
                    self.errors[plugin_id] = str(exc.detail)
                    logger.warning("Cannot load plugin %s engine: %s", plugin_id, exc.detail)

    async def shutdown(self) -> None:
        async with self._condition:
            self._closing = True
            for plugin_id in list(self._loaded):
                await self._retire(plugin_id)
            self._condition.notify_all()

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return
        if scope["type"] == "websocket":
            from ..server import validate_token

            bypass = os.environ.get("KOLLAB_ENGINE_BYPASS_AUTH") == "1" and "pytest" in sys.modules
            header = next((value.decode("latin-1") for key, value in scope.get("headers", [])
                           if key.lower() == b"authorization"), "")
            token = header[7:] if header.startswith("Bearer ") else ""
            try:
                authenticated = bool(token) and (
                    validate_token(token) or self.tokens.authorize(token, "GET", scope["path"], self.hub)
                )
            except (OSError, TypeError, ValueError):
                authenticated = False
            if not bypass and not authenticated:
                await send({"type": "websocket.close", "code": 1008})
                return
        plugin_id = scope.get("path_params", {}).get("plugin_id", "")
        try:
            loaded = await self._acquire(plugin_id)
        except HTTPException as exc:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await JSONResponse({"detail": exc.detail}, status_code=exc.status_code)(scope, receive, send)
            return
        remote = any(k.lower() == b"x-kollab-plugin-remote" and v == b"1" for k, v in scope.get("headers", []))
        token = _remote_plugin_request.set(scope["type"] if remote else None)
        try:
            await loaded.app(scope, receive, send)
        finally:
            _remote_plugin_request.reset(token)
            await self._release(plugin_id, loaded)
