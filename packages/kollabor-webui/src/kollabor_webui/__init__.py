"""kollabor-webui: browser UI shell for Kollab Engine."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import threading
import time
import webbrowser


def rebuild_stale_bundle() -> None:
    """Rebuild a source checkout's UI when its source is newer than its build.

    A checkout serves ``frontend/dist`` and nothing else rebuilt it, so a change
    to ``frontend/src`` kept showing the previous build, which looked exactly
    like the change had been reverted. The server reads the files on every
    request, so a running UI shows the rebuild on its next load. An installed
    package has no ``frontend/src`` and serves the bundle it shipped with.
    """
    from .server import SOURCE_ASSISTANT_STATIC_DIR as dist

    frontend = dist.parent
    if not (frontend / "src").is_dir():
        return
    index = dist / "index.html"
    built = index.stat().st_mtime if index.is_file() else 0.0
    inputs = [*frontend.glob("*.html"), *frontend.glob("*.json"), *frontend.glob("vite.config.*")]
    if not any(path.is_file() and path.stat().st_mtime > built for path in [*inputs, *(frontend / "src").rglob("*")]):
        return
    npm = shutil.which("npm")
    if npm is None or not (frontend / "node_modules").is_dir():
        print(f"  (the web ui source is newer than its build: run `npm run build` in {frontend})")
        return
    print("  (the web ui source is newer than its build; rebuilding it...)")
    try:
        result = subprocess.run(
            [npm, "run", "build"], cwd=frontend, capture_output=True, text=True, timeout=300
        )
    except subprocess.TimeoutExpired:
        print("  (the rebuild took over 5 minutes; serving the previous build)")
        return
    if result.returncode != 0:
        print("  (the rebuild failed; serving the previous build)")
        for line in (result.stderr or result.stdout).strip().splitlines()[-5:]:
            print(f"    {line}")


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
    rebuild_stale_bundle()

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
