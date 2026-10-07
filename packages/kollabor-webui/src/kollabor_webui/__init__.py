"""kollabor-webui: browser UI shell for Kollab Engine."""

from __future__ import annotations

import os
import socket
import threading
import time
import webbrowser


def _listen(hosts: list[str], port: int) -> list[socket.socket]:
    """A socket per host. 127.0.0.1 must bind; an extra host that cannot (its
    tunnel is down, say) is skipped so the local UI still starts."""
    sockets = []
    for host in hosts:
        sock = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((host, port))
        except OSError as exc:
            sock.close()
            if not sockets:
                raise
            print(f"  (not listening on {host}: {exc})")
            continue
        sock.set_inheritable(True)
        sockets.append(sock)
    return sockets


def main() -> None:
    """Launch the Kollab UI web server."""

    import uvicorn  # type: ignore[import-not-found]

    from .server import create_app

    port = int(os.getenv("KOLLAB_WEBUI_PORT", "8080"))
    engine_url = os.getenv("KOLLAB_ENGINE_URL", "http://127.0.0.1:7433")
    # Addresses to serve on besides 127.0.0.1, e.g. a WireGuard IP. Every page
    # that reaches one gets the engine token: list private interfaces only.
    extra = [host.strip() for host in os.getenv("KOLLAB_WEBUI_HOSTS", "").split(",") if host.strip()]
    sockets = _listen(["127.0.0.1", *extra], port)

    print("\n  kollabor-webui starting...")
    print(f"  engine: {engine_url}")
    for sock in sockets:
        host = sock.getsockname()[0]
        print(f"  ui:     http://{f'[{host}]' if ':' in host else host}:{port}")
    print()

    # Open browser after short delay.  Keep this daemonized so shutdown is not
    # delayed if uvicorn exits before the browser callback runs.
    def open_browser_delayed() -> None:
        time.sleep(1.5)
        webbrowser.open(f"http://127.0.0.1:{port}")

    threading.Thread(target=open_browser_delayed, daemon=True).start()

    uvicorn.Server(uvicorn.Config(create_app(engine_url), log_level="warning")).run(sockets=sockets)


if __name__ == "__main__":
    main()
