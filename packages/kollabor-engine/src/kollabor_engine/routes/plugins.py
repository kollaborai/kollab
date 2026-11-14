"""Engine access to the shared plugin inventory."""

from fastapi import APIRouter

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
