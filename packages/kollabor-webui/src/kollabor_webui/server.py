"""FastAPI server for serving the Kollab UI."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI  # type: ignore[import-not-found]
from fastapi.responses import FileResponse  # type: ignore[import-not-found]

PACKAGE_DIR = Path(__file__).resolve().parent
STATIC_DIR = PACKAGE_DIR / "static"
ASSISTANT_STATIC_DIR = STATIC_DIR / "assistant"
# In an editable/source checkout the generated Vite output lives beside the
# Python package.  The build hook copies this directory into the wheel at
# package time, so installed applications never need the repository checkout.
SOURCE_ASSISTANT_STATIC_DIR = PACKAGE_DIR.parents[1] / "frontend" / "dist"
LEGACY_STATIC_DIR = STATIC_DIR


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


def create_app(engine_url: str = "http://127.0.0.1:7433") -> FastAPI:
    app = FastAPI(title="Kollab WebUI", docs_url=None, redoc_url=None)

    # API proxy config endpoint. Ships the engine's bearer token to the page so
    # the user does not have to paste it by hand; both servers are bound to
    # 127.0.0.1 and this endpoint sends no CORS headers, so only a same-origin
    # page can read it.
    @app.get("/api/config")
    async def get_config():
        from kollabor_engine.auth import read_token_from_disk

        return {"engine_url": engine_url, "token": read_token_from_disk()}

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
