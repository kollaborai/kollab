"""FastAPI server for serving the Kollab UI."""

from pathlib import Path

from fastapi import FastAPI  # type: ignore[import-not-found]
from fastapi.responses import FileResponse, HTMLResponse  # type: ignore[import-not-found]

STATIC_DIR = Path(__file__).parent / "static"


def create_app(engine_url: str = "http://127.0.0.1:7433") -> FastAPI:
    app = FastAPI(title="Kollab WebUI", docs_url=None, redir_url=None)

    # API proxy config endpoint
    @app.get("/api/config")
    async def get_config():
        return {"engine_url": engine_url}

    # Serve static files
    @app.get("/", response_class=HTMLResponse)
    async def index():
        return (STATIC_DIR / "index.html").read_text()

    @app.get("/app.js")
    async def script():
        return FileResponse(STATIC_DIR / "app.js", media_type="application/javascript")

    return app
