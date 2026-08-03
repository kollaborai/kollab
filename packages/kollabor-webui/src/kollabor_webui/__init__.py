"""kollabor-webui: browser UI shell for Kollab Engine."""

from __future__ import annotations

import os
import threading
import time
import webbrowser


def main() -> None:
    """Launch the Kollab UI web server."""

    import uvicorn  # type: ignore[import-not-found]

    from .server import create_app

    port = int(os.getenv("KOLLAB_WEBUI_PORT", "8080"))
    engine_url = os.getenv("KOLLAB_ENGINE_URL", "http://127.0.0.1:7433")

    print("\n  kollabor-webui starting...")
    print(f"  engine: {engine_url}")
    print(f"  ui:     http://127.0.0.1:{port}\n")

    # Open browser after short delay.  Keep this daemonized so shutdown is not
    # delayed if uvicorn exits before the browser callback runs.
    def open_browser_delayed() -> None:
        time.sleep(1.5)
        webbrowser.open(f"http://127.0.0.1:{port}")

    threading.Thread(target=open_browser_delayed, daemon=True).start()

    uvicorn.run(
        create_app(engine_url),
        host="127.0.0.1",
        port=port,
        log_level="warning",
    )


if __name__ == "__main__":
    main()
