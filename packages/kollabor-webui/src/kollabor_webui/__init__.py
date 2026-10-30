"""kollabor-webui: browser UI shell for Kollab Engine."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import threading
import time
import webbrowser

from .network_security import configured_bind_hosts, validate_bind_hosts


def rebuild_stale_bundle() -> None:
    """Rebuild a source checkout's UI when its source is newer than its build.

    A checkout serves ``frontend/dist`` and nothing else rebuilt it, so a change
    to ``frontend/src`` kept showing the previous build, which looked exactly
    like the change had been reverted. The server reads the files on every
    request, so a running UI shows the rebuild on its next load. An installed
    package has no ``frontend/src`` and serves the bundle it shipped with.
    """
    from .server import SOURCE_ASSISTANT_STATIC_DIR as dist
    from .server import _explicit_frontend_dir, _validate_frontend_dir

    # An explicit, validated artifact is already compiled. This includes
    # editable installs, whose source dist may be older than the chosen app.
    if _explicit_frontend_dir() is not None:
        return

    frontend = dist.parent
    if not (frontend / "src").is_dir():
        return
    index = dist / "index.html"
    built = index.stat().st_mtime if index.is_file() else 0.0
    inputs = [
        *frontend.glob("*.html"), *frontend.glob("*.json"), *frontend.glob("vite.config.*"),
        frontend / "CHANGELOG.md", frontend.parents[2] / "kollabor/updates/CHANGELOG.md",
        *(frontend / "src").rglob("*"), *(frontend / "public").rglob("*"),
    ]
    if not any(path.is_file() and path.stat().st_mtime > built for path in inputs):
        return
    npm = shutil.which("npm")
    if npm is None or not (frontend / "node_modules").is_dir():
        raise RuntimeError(
            f"The WebUI source is newer than its build. Install its npm dependencies and run `npm run build` in "
            f"{frontend}, or select a compiled app with KOLLAB_FRONTEND_DIST."
        )
    print("  (the web ui source is newer than its build; rebuilding it...)")
    try:
        result = subprocess.run(
            [npm, "run", "build"], cwd=frontend, capture_output=True, text=True, timeout=300
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("The WebUI rebuild could not finish; no older build will be served.") from exc
    if result.returncode != 0:
        detail = "\n".join((result.stderr or result.stdout).strip().splitlines()[-5:])
        raise RuntimeError(f"The WebUI rebuild failed; no older build will be served.\n{detail}")
    _validate_frontend_dir(dist)


def _listen(hosts: list[str], port: int) -> list[socket.socket]:
    """A socket per host. 127.0.0.1 must bind; an extra host that cannot (its
    tunnel is down, say) is skipped so the local UI still starts."""
    hosts = validate_bind_hosts(hosts)

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


def build_uvicorn_config(app):
    """Build a direct-bind server; forwarded client headers are never trusted."""
    import uvicorn  # type: ignore[import-not-found]

    # An open page holds a stream; a stop waits at most 3 s for it instead of until the page goes away.
    return uvicorn.Config(app, log_level="warning", proxy_headers=False, timeout_graceful_shutdown=3)



def main() -> None:
    """Launch the Kollab UI web server."""


    import uvicorn  # type: ignore[import-not-found]

    from .server import create_app

    port = int(os.getenv("KOLLAB_WEBUI_PORT", "8080"))
    engine_url = os.getenv("KOLLAB_ENGINE_URL", "http://127.0.0.1:7433")
    # Bind only explicit local addresses. This host allowlist is also used for
    # safe Host validation and exact client-subnet checks in the WebUI app.
    try:
        bind_hosts = configured_bind_hosts()
    except ValueError as exc:
        raise SystemExit(str(exc)) from None
    rebuild_stale_bundle()
    sockets = _listen(bind_hosts, port)
    active_hosts = [str(sock.getsockname()[0]) for sock in sockets]

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

    # The desktop app is the browser. A no-op BROWSER does not work on
    # Windows (no `true`), so it sets this instead (#268).
    if not os.getenv("KOLLAB_WEBUI_NO_BROWSER"):
        threading.Thread(target=open_browser_delayed, daemon=True).start()

    app = create_app(engine_url, bind_hosts=active_hosts)
    uvicorn.Server(build_uvicorn_config(app)).run(sockets=sockets)


if __name__ == "__main__":
    main()
