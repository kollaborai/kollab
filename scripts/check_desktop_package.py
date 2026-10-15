#!/usr/bin/env python3
"""Smoke a packaged Linux window against an isolated, externally owned Web UI."""

from __future__ import annotations

import argparse
import http.server
import os
import signal
import subprocess
import tempfile
import threading
import time
import urllib.request
from pathlib import Path


class SmokeUI(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/openapi.json"):
            body, mime = b'{"info":{"title":"Kollab WebUI"}}', "application/json"
        else:
            body, mime = (
                b'<!doctype html><title>Kollab package smoke</title><div id="root"><p>Ready</p></div>',
                "text/html",
            )
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


def wait_log(process, log: Path, expected: str) -> None:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        text = log.read_text(errors="replace")
        if expected in text:
            return
        if process.poll() is not None:
            raise RuntimeError(f"packaged app exited before {expected!r}:\n{text[-8000:]}")
        time.sleep(0.1)
    raise RuntimeError(f"packaged app did not report {expected!r}:\n{log.read_text(errors='replace')[-8000:]}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("appimage", type=Path)
    asset = parser.parse_args().appimage.resolve()
    with tempfile.TemporaryDirectory(prefix="kollab-desktop-smoke-") as scratch:
        root = Path(scratch)
        env = {**os.environ, "KOLLAB_DESKTOP_SELFTEST_CLOSE": "1", "APPIMAGE_EXTRACT_AND_RUN": "1"}
        for name in ("HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME"):
            folder = root / name.lower()
            folder.mkdir()
            env[name] = str(folder)
        with http.server.ThreadingHTTPServer(("127.0.0.1", 0), SmokeUI) as server:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            port = server.server_address[1]
            env["KOLLAB_WEBUI_PORT"] = str(port)
            log = root / "desktop.log"
            with log.open("wb") as stream:
                process = subprocess.Popen([str(asset)], env=env, stdout=stream, stderr=stream, start_new_session=True)
                try:
                    wait_log(process, log, '("rendered", "true")')
                    wait_log(process, log, "window closed; hidden")
                    reopened = subprocess.run([str(asset)], env=env, capture_output=True, timeout=30)
                    if reopened.returncode:
                        raise RuntimeError(f"second launch failed: {reopened.stderr.decode(errors='replace')}")
                    wait_log(process, log, "second launch, focusing the existing window")
                finally:
                    if process.poll() is None:
                        # Only this private test session, whose leader remains
                        # an unreaped direct child. No PID/name lookup or force.
                        os.killpg(process.pid, signal.SIGINT)
                    process.wait(timeout=15)
                if process.returncode != 0:
                    raise RuntimeError(f"package quit failed ({process.returncode}):\n{log.read_text()[-8000:]}")
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/openapi.json", timeout=2) as response:
                    assert response.status == 200, "attached runtime was stopped by the desktop app"
                print(log.read_text(errors="replace"))
            server.shutdown()
    print("passed packaged AppImage: rendered, close/reopen, clean quit; attached runtime preserved")


if __name__ == "__main__":
    main()
