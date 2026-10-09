"""FastAPI application and session registry."""

import asyncio
import logging
import time
import traceback
from typing import Any, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, Request  # type: ignore[import-not-found]
from fastapi.middleware.cors import CORSMiddleware  # type: ignore[import-not-found]
from fastapi.responses import JSONResponse  # type: ignore[import-not-found]

from .auth import validate_token
from .daemon_pool import get_daemon_pool
from .hub_bridge import HubBridge
from .session import EngineSession

logger = logging.getLogger(__name__)

# Global session registry
_sessions: Dict[str, EngineSession] = {}
_start_time = time.time()


def _profile_has_configured_api_credentials(profile: object) -> bool:
    """Check credential presence without resolving or migrating secrets."""
    get_env_value = getattr(profile, "_get_env_value", None)
    if callable(get_env_value) and get_env_value("API_KEY"):
        return True

    get_global_env_value = getattr(profile, "_get_global_env_value", None)
    if callable(get_global_env_value) and get_global_env_value("API_KEY"):
        return True

    raw_api_key = str(getattr(profile, "api_key", "") or "").strip()
    return bool(raw_api_key and not raw_api_key.startswith("<"))


def get_session_registry() -> Dict[str, EngineSession]:
    return _sessions


# Opens in flight, so concurrent requests for one agent share a single attach.
_adopting: Dict[str, "asyncio.Future[Any]"] = {}


async def _adopt(found: Dict[str, Any]) -> EngineSession:
    session = await EngineSession.adopt(found)
    # Registered before the open settles: a request that arrives after it
    # finds the session instead of opening the agent a second time.
    return _sessions.setdefault(session.session_id, session)


async def _open_remote(handle: str) -> Optional[EngineSession]:
    """An agent@device on another computer of this folder's network (remote_attach)."""
    from .remote_attach import open_remote_agent

    found = await open_remote_agent(handle, _sessions)
    return await _adopt(found) if found else None


async def open_named_session(request: Request) -> None:
    """Attach the live agent a session route names, the first time one does.

    Every live agent on this computer is listed (HubBridge.discover_sessions),
    but only sessions in the registry answer the session routes. A terminal
    agent or another engine's daemon gets its registry entry here, on the
    first request that names it, and so does an agent@device on another
    computer of this folder's network; an unknown id falls through to the
    route's own 404.
    """
    session_id = request.path_params.get("session_id")
    if not session_id or session_id in _sessions:
        return
    opening = _adopting.get(session_id)
    if opening is None:
        found = next(
            (
                row
                for row in HubBridge().discover_sessions(use_cache=False)
                # This engine's own daemon, still starting, is not another agent.
                if row["session_id"] == session_id and not get_daemon_pool().owns(row)
            ),
            None,
        )
        if found is not None:
            opening = asyncio.ensure_future(_adopt(found))
        elif "@" in session_id:
            opening = asyncio.ensure_future(_open_remote(session_id))
        else:
            return
        _adopting[session_id] = opening
        opening.add_done_callback(lambda _: _adopting.pop(session_id, None))
    try:
        await asyncio.shield(opening)
    except Exception as e:
        logger.warning("could not open session %s: %s", session_id, e)
        raise HTTPException(status_code=503, detail=f"could not open this agent: {e}")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Kollab Engine",
        description="AI engine API - conversations, tools, permissions",
        version="0.1.0",
    )

    # Auth middleware
    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        import os
        import sys

        # Skip auth for tests if env var is set
        if os.environ.get("KOLLAB_ENGINE_BYPASS_AUTH") == "1" and "pytest" in sys.modules:
            return await call_next(request)

        # Skip auth for CORS preflight requests
        if request.method == "OPTIONS":
            return await call_next(request)

        # Skip auth for health/version/status/ready endpoints
        if request.url.path in ("/health", "/version", "/status", "/ready", "/"):
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return JSONResponse(
                status_code=401, content={"detail": "Missing bearer token"}
            )

        token = auth_header[7:]
        if not validate_token(token):
            return JSONResponse(status_code=401, content={"detail": "Invalid token"})

        return await call_next(request)

    def error_response(request: Request, exc: Exception) -> JSONResponse:
        tb = "".join(traceback.format_exception(exc))
        logger.error(
            f"Unhandled exception on {request.method} {request.url.path}:\n{tb}"
        )
        return JSONResponse(
            status_code=500,
            content={"detail": str(exc), "type": type(exc).__name__},
        )

    # Between auth and CORS: a crash becomes a JSON 500 that still carries CORS
    # headers. The exception handler below runs outside CORS, so on its own the
    # browser reported "Failed to fetch" instead of the error.
    @app.middleware("http")
    async def error_middleware(request: Request, call_next):
        try:
            return await call_next(request)
        except Exception as exc:
            return error_response(request, exc)

    # Registered last so it wraps auth_middleware: a short-circuited 401 still
    # gets CORS headers, otherwise the browser reports an opaque CORS failure
    # instead of the real "unauthorized".
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # local only - bound to 127.0.0.1
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Global exception handler - ensures all 500s return JSON (not plain text)
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        return error_response(request, exc)

    # Mount routes
    from .routes.mcp import router as mcp_router
    from .routes.messages import router as messages_router
    from .routes.panels import router as panels_router
    from .routes.permissions import router as permissions_router
    from .routes.profiles import router as profiles_router
    from .routes.sessions import router as sessions_router

    opens = [Depends(open_named_session)]
    app.include_router(sessions_router, dependencies=opens)
    app.include_router(messages_router, dependencies=opens)
    app.include_router(permissions_router, dependencies=opens)
    app.include_router(profiles_router)
    app.include_router(mcp_router)
    app.include_router(panels_router, dependencies=opens)

    from .routes.agents import router as agents_router
    from .routes.hub import router as hub_router

    app.include_router(agents_router)
    app.include_router(hub_router)

    from .routes.hub_ws import router as hub_ws_router

    app.include_router(hub_ws_router)

    @app.get("/health")
    async def health():
        return {"status": "healthy", "uptime": int(time.time() - _start_time)}

    @app.get("/version")
    async def version():
        import sys
        from importlib.metadata import version as pkg_version

        def safe_ver(pkg: str) -> str:
            try:
                return pkg_version(pkg)
            except Exception:
                return "unknown"

        v = safe_ver("kollabor-engine")
        return {
            "version": v,
            "engine": v,
            "kollabor_ai": safe_ver("kollabor-ai"),
            "kollabor_agent": safe_ver("kollabor-agent"),
            "kollabor_events": safe_ver("kollabor-events"),
            "python": sys.version.split()[0],
        }

    @app.get("/status")
    async def status():
        from importlib.metadata import version as pkg_version

        def safe_ver(pkg: str) -> str:
            try:
                return pkg_version(pkg)
            except Exception:
                return "unknown"

        # The providers in use. An opened agent's profile is None until its daemon
        # answers. MCP servers belong to each daemon: GET /sessions/{id}/mcp.
        providers = sorted(
            {getattr(s.profile, "provider", "") or "" for s in _sessions.values()} - {""}
        )

        return {
            "version": safe_ver("kollabor-engine"),
            "sessions": len(_sessions),
            "uptime": int(time.time() - _start_time),
            "providers": providers,
            "session_ids": list(_sessions.keys()),
        }

    @app.get("/ready")
    async def ready():
        """Readiness check - verifies engine can handle requests."""
        checks: Dict[str, str] = {}

        # Check if any profiles are configured
        from kollabor_ai import ProfileManager

        profile_mgr = ProfileManager()
        profiles = profile_mgr.list_profiles()
        checks["profiles"] = "ok" if profiles else "none"

        # Check if we have sessions (optional)
        checks["sessions"] = "ok" if _sessions else "idle"

        # Verify at least one profile has configured credentials. Do not call
        # get_api_key(); that may read or migrate secrets in the OS keyring.
        api_check = "failed"
        for profile in profiles:
            if _profile_has_configured_api_credentials(profile):
                api_check = "ok"
                break
        checks["api_credentials"] = api_check

        # Ready if profiles exist AND at least one has valid credentials
        ready = checks["profiles"] == "ok" and checks["api_credentials"] == "ok"
        return {"ready": ready, "checks": checks}

    @app.on_event("startup")
    async def on_startup():
        logger.info("Engine started")

    @app.on_event("shutdown")
    async def on_shutdown():
        for session in list(_sessions.values()):
            await session.shutdown()
        _sessions.clear()
        logger.info("Engine shutdown - all sessions cleaned up")

    return app
