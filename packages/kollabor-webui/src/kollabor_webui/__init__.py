"""kollabor-webui: Hacker-style terminal web UI for Kollab Engine."""

import asyncio
import os
import webbrowser
from pathlib import Path

STATIC_DIR = Path(__file__).parent / "static"


def main():
    """Launch the Kollab UI web server."""
    import threading
    import time

    import uvicorn  # type: ignore[import-not-found]

    from .server import create_app

    port = int(os.getenv("KOLLAB_WEBUI_PORT", "8080"))
    engine_url = os.getenv("KOLLAB_ENGINE_URL", "http://127.0.0.1:7433")

    print("\n  kollabor-webui starting...")
    print(f"  engine: {engine_url}")
    print(f"  ui:     http://127.0.0.1:{port}\n")

    # Open browser after short delay
    def open_browser_delayed():
        time.sleep(1.5)
        webbrowser.open(f"http://127.0.0.1:{port}")

    browser_thread = threading.Thread(target=open_browser_delayed, daemon=True)
    browser_thread.start()

    uvicorn.run(
        create_app(engine_url),
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
