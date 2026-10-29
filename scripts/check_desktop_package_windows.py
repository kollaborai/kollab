#!/usr/bin/env python3
"""Smoke the Windows installer: install silently, launch, render, close to the tray, relaunch.

The app runs against the same isolated, externally owned Web UI as the Linux
smoke (scripts/check_desktop_package.py), so this checks the native shell, the
installed layout and the WebView2 window, not the Python runtime. Clean Quit
needs GUI automation this script does not have: the app is ended with taskkill.
"""

from __future__ import annotations

import argparse
import http.server
import os
import subprocess
import tempfile
import threading
from pathlib import Path

from check_desktop_package import SmokeUI, wait_log


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("installer", type=Path)
    installer = parser.parse_args().installer.resolve()
    with tempfile.TemporaryDirectory(prefix="kollab-desktop-smoke-", ignore_cleanup_errors=True) as scratch:
        root = Path(scratch)
        target = root / "Kollab"
        # NSIS: /S is silent, and /D (the install folder) must come last, unquoted.
        subprocess.run([str(installer), "/S", f"/D={target}"], check=True, timeout=600)
        app, runtime = target / "kollab-desktop.exe", target / "runtime" / "kollab.exe"
        for path in (app, runtime):
            assert path.is_file(), f"the installer left no {path.relative_to(target)}"
        shown = subprocess.run([str(runtime), "--version"], capture_output=True, text=True, timeout=600)
        assert shown.returncode == 0, f"installed runtime --version failed:\n{shown.stdout}{shown.stderr}"
        env = {**os.environ, "KOLLAB_DESKTOP_SELFTEST_CLOSE": "1", "USERPROFILE": str(root), "HOME": str(root)}
        with http.server.ThreadingHTTPServer(("127.0.0.1", 0), SmokeUI) as server:
            threading.Thread(target=server.serve_forever, daemon=True).start()
            env["KOLLAB_WEBUI_PORT"] = str(server.server_address[1])
            log = root / "desktop.log"
            with log.open("wb") as stream:
                process = subprocess.Popen([str(app)], env=env, stdout=stream, stderr=stream)
                try:
                    wait_log(process, log, '("rendered", "true")')
                    wait_log(process, log, "window closed; hidden")
                    reopened = subprocess.run([str(app)], env=env, capture_output=True, timeout=30)
                    if reopened.returncode:
                        raise RuntimeError(f"second launch failed: {reopened.stderr.decode(errors='replace')}")
                    wait_log(process, log, "second launch, focusing the existing window")
                finally:
                    subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
                    process.wait(timeout=30)
            print(log.read_text(errors="replace"))
            server.shutdown()
    print("passed installed app: rendered, close to tray, second launch; Quit not exercised (taskkill)")


if __name__ == "__main__":
    main()
