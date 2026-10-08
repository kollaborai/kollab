"""FastAPI server for serving the Kollab UI."""

from __future__ import annotations

import ipaddress
from contextlib import asynccontextmanager
from pathlib import Path

import anyio
import httpx
from fastapi import FastAPI, Request  # type: ignore[import-not-found]
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse  # type: ignore[import-not-found]

PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR / "static"
ASSISTANT_STATIC_DIR = STATIC_DIR / "assistant"
# In an editable/source checkout the generated Vite output lives beside the
# Python package.  The build hook copies this directory into the wheel at
# package time, so installed applications never need the repository checkout.
SOURCE_ASSISTANT_STATIC_DIR = PACKAGE_DIR.parents[1] / "frontend" / "dist"
LEGACY_STATIC_DIR = STATIC_DIR
# Headers that belong to one connection (RFC 9110 7.6.1), or that the proxy's
# own client and server set again.
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
    "trailer", "transfer-encoding", "upgrade", "host", "content-length", "date", "server",
}


def _is_local(request: Request) -> bool:
    """Whether the page was opened on this machine (a peer IP that is loopback)."""
    host = request.client.host if request.client else ""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return True  # no network peer at all: a test client or a unix socket


def _assistant_static_dir() -> Path | None:
    """Return the first available assistant-ui build directory."""

    for directory in (ASSISTANT_STATIC_DIR, SOURCE_ASSISTANT_STATIC_DIR):
        if (directory / "index.html").is_file():
            return directory
    return None


def _safe_asset(root: Path, asset_path: str) -> Path | None:
    """Resolve an asset below ``root`` without allowing path traversal."""

    if not asset_path:
        return None
    candidate = (root / asset_path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def create_app(
    engine_url: str = "http://127.0.0.1:7433",
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    engine = httpx.AsyncClient(
        base_url=engine_url, transport=transport, timeout=httpx.Timeout(10.0, read=None)
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await engine.aclose()

    app = FastAPI(title="Kollab WebUI", docs_url=None, redoc_url=None, lifespan=lifespan)

    # API proxy config endpoint. Ships the engine's bearer token to the page so
    # the user does not have to paste it by hand. This server listens on
    # 127.0.0.1 plus any KOLLAB_WEBUI_HOSTS address, and sends no CORS headers,
    # so only a same-origin page on those interfaces can read it. A page opened
    # from another machine cannot reach the engine's 127.0.0.1, so it gets this
    # server's /engine proxy instead.
    @app.get("/api/config")
    async def get_config(request: Request):
        from kollabor_engine.auth import read_token_from_disk

        url = engine_url if _is_local(request) else f"{request.base_url}engine"
        return {"engine_url": url, "token": read_token_from_disk()}

    @app.api_route("/engine/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def engine_proxy(path: str, request: Request):
        query = f"?{request.url.query}" if request.url.query else ""
        upstream = engine.build_request(
            request.method,
            f"/{path}{query}",
            headers=[(k, v) for k, v in request.headers.items() if k.lower() not in _HOP_BY_HOP],
            content=await request.body(),
        )
        try:
            response = await engine.send(upstream, stream=True)
        except httpx.HTTPError as exc:
            return JSONResponse({"detail": f"Engine unreachable: {exc}"}, status_code=502)

        async def body():
            # Streams (SSE, the assistant transport) pass through as they come;
            # a page that leaves closes the engine side too.
            try:
                async for chunk in response.aiter_raw():
                    yield chunk
            finally:
                with anyio.CancelScope(shield=True):
                    await response.aclose()

        return StreamingResponse(
            body(),
            status_code=response.status_code,
            headers={k: v for k, v in response.headers.items() if k.lower() not in _HOP_BY_HOP},
        )

    # Keep the legacy entrypoint available while the assistant-ui app rolls out.
    # Existing bookmarks and scripts may still request /app.js directly.
    @app.get("/app.js")
    async def script():
        return FileResponse(
            LEGACY_STATIC_DIR / "app.js", media_type="application/javascript"
        )

    @app.get("/")
    async def index():
        assistant_dir = _assistant_static_dir()
        if assistant_dir is not None:
            return FileResponse(assistant_dir / "index.html", media_type="text/html")
        return FileResponse(LEGACY_STATIC_DIR / "index.html", media_type="text/html")

    @app.get("/{asset_path:path}")
    async def static_or_spa(asset_path: str):
        """Serve hashed Vite assets and fall back to the SPA entrypoint.

        Vite emits relative asset URLs, so this route intentionally handles
        arbitrary nested paths (for example ``assets/index-<hash>.js``).  A
        missing path is an SPA navigation when assistant-ui is installed; the
        legacy index remains the fallback for Python-only/old installations.
        """

        assistant_dir = _assistant_static_dir()
        if assistant_dir is not None:
            asset = _safe_asset(assistant_dir, asset_path)
            if asset is not None:
                return FileResponse(asset)
            return FileResponse(assistant_dir / "index.html", media_type="text/html")

        asset = _safe_asset(LEGACY_STATIC_DIR, asset_path)
        if asset is not None:
            return FileResponse(asset)
        return FileResponse(LEGACY_STATIC_DIR / "index.html", media_type="text/html")

    return app
