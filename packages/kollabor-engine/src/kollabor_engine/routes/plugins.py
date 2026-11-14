"""Engine access to the shared plugin inventory."""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

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
    if method != "GET":
        return False
    return tail in (["settings"], ["agents"], ["usage"]) or (
        len(tail) == 3 and tail[0] == "services" and tail[2] == "log"
    )


@router.get("")
def list_plugins():
    from kollabor_plugins.registry import plugin_entries

    return {"schema": 1, "plugins": plugin_entries()}


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
