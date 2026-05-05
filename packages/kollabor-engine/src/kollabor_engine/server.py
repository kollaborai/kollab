"""FastAPI application and session registry."""

import logging
import time
import traceback
from typing import Dict

from fastapi import FastAPI, Request  # type: ignore[import-not-found]
from fastapi.middleware.cors import CORSMiddleware  # type: ignore[import-not-found]
from fastapi.responses import JSONResponse  # type: ignore[import-not-found]

from .auth import validate_token
from .session import EngineSession

logger = logging.getLogger(__name__)

# Global session registry
_sessions: Dict[str, EngineSession] = {}
_start_time = time.time()


def get_session_registry() -> Dict[str, EngineSession]:
    return _sessions


def create_app() -> FastAPI:
    app = FastAPI(
        title="Kollab Engine",
        description="AI engine API - conversations, tools, permissions",
        version="0.1.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # local only - bound to 127.0.0.1
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Auth middleware
    @app.middleware("http")
    async def auth_middleware(request: Request, call_next):
        import os

        # Skip auth for tests if env var is set
        if os.environ.get("KOLLAB_ENGINE_BYPASS_AUTH") == "1":
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

    # Global exception handler - ensures all 500s return JSON (not plain text)
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        tb = traceback.format_exc()
        logger.error(
            f"Unhandled exception on {request.method} {request.url.path}:\n{tb}"
        )
        return JSONResponse(
            status_code=500,
            content={"detail": str(exc), "type": type(exc).__name__},
        )

    # Mount routes
    from .routes.mcp import router as mcp_router
    from .routes.messages import router as messages_router
    from .routes.permissions import router as permissions_router
    from .routes.profiles import router as profiles_router
    from .routes.sessions import router as sessions_router

    app.include_router(sessions_router)
    app.include_router(messages_router)
    app.include_router(permissions_router)
    app.include_router(profiles_router)
    app.include_router(mcp_router)

    from .routes.hub import router as hub_router

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

        # Aggregate providers from all sessions
        providers = sorted(set(s.profile.provider for s in _sessions.values()))

        # Aggregate MCP server status from all sessions (actual connection state)
        mcp_status: Dict[str, str] = {}
        for session in _sessions.values():
            connections = session.mcp_integration.server_connections
            for server_name, conn in connections.items():
                if conn.initialized:
                    mcp_status[server_name] = "connected"
                elif server_name not in mcp_status:
                    mcp_status[server_name] = "disconnected"

        return {
            "version": safe_ver("kollabor-engine"),
            "sessions": len(_sessions),
            "uptime": int(time.time() - _start_time),
            "providers": providers,
            "mcp_servers": mcp_status,
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

        # Verify at least one profile has valid API credentials
        api_check = "failed"
        for profile in profiles:
            if profile.get_api_key():
                # Verify API key format (non-empty after env var resolution)
                api_key = profile.get_api_key()
                if api_key and not api_key.startswith("<"):
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
