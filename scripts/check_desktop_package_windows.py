#!/usr/bin/env python3
"""Smoke the Windows installer: install silently, launch, render, close to the tray, relaunch, quit.

First the app runs against the same isolated, externally owned Web UI as the
Linux smoke (scripts/check_desktop_package.py), which checks the native shell,
the installed layout and the WebView2 window; a second launch with ``--quit``
ends it. Then the app starts its bundled runtime itself and ``--quit`` ends it
again: the app closes the runtime's stdin, the runtime stops its engine and UI
server, the engine logs its shutdown, and no runtime process may be left.
"""

from __future__ import annotations

import argparse
import csv
import http.server
import io
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from check_desktop_package import SmokeUI, wait_log

# Command lines of the runtime the app starts: the CLI, its engine and its UI server.
RUNTIME = ("--web-ui", "kollabor_engine", "kollabor_webui")


def process_table() -> dict[int, tuple[int, str]]:
    """pid -> (parent pid, command line) of every process."""
    listed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,CommandLine"
            " | ConvertTo-Csv -NoTypeInformation",
        ],
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=True,
    )
    return {
        int(row["ProcessId"]): (int(row["ParentProcessId"]), row["CommandLine"] or "")
        for row in csv.DictReader(io.StringIO(listed.stdout))
    }


def descendants(table: dict[int, tuple[int, str]], root: int) -> dict[int, str]:
    """pid -> command line of every process under ``root``."""
    found: dict[int, str] = {}
    grew = True
    while grew:
        grew = False
        for pid, (parent, command) in table.items():
            if pid not in found and pid != root and (parent == root or parent in found):
                found[pid] = command
                grew = True
    return found


def runtime_version(runtime: Path, env: dict[str, str]) -> None:
    """Read the runtime's version the way the app does: no console window, no stdin, the app's environment."""
    shown = subprocess.run(
        [str(runtime), "--version"],
        env=env,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        timeout=600,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    out = shown.stdout.decode(errors="replace")
    err = shown.stderr.decode(errors="replace")
    print(f"runtime --version as the app runs it: exit {shown.returncode}\n{out}{err[-4000:]}")
    last = (out.split() or [""])[-1]
    assert shown.returncode == 0 and all(part[:1].isdigit() for part in last.split(".")), (
        "the app cannot read the runtime's version, so it would show the install page"
    )


def quit_app(app: Path, env: dict[str, str], process: subprocess.Popen, log: Path) -> None:
    """A second launch with --quit ends the running app."""
    subprocess.run([str(app), "--quit"], env=env, capture_output=True, timeout=30)
    process.wait(timeout=60)
    wait_log(process, log, "second launch asked to quit")


def report_webui(env: dict[str, str], app: int, table: dict[int, tuple[int, str]], waited: float) -> None:
    """Why the app reported no web UI: every process under it, whether the UI answers in the next 60 s, and where
    the same web UI command stops when run again (its stack after 30 s, and its imports)."""
    port = env["KOLLAB_WEBUI_PORT"]
    print(f"no web UI reported {waited:.0f} s after the app started; processes under it:")
    under, webui = {app}, ""
    while grown := {pid for pid, (ppid, _) in table.items() if ppid in under and pid not in under}:
        under |= grown
    for pid in sorted(under - {app}):
        ppid, command = table[pid]
        print(f"  {pid} (parent {ppid}) {command[:220]}")
        if "kollabor_webui import main" in command:
            webui = command
    start = time.monotonic()
    while True:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/openapi.json", timeout=10) as reply:
                body = reply.read(160)
                print(f"the web UI answered {time.monotonic() - start:.0f} s later: HTTP {reply.status} {body!r}")
                return
        except OSError as exc:
            if time.monotonic() > start + 60:
                print(f"the web UI on port {port} never answered: {exc}")
                return
        time.sleep(3)
    if not webui:
        print("no web UI process to run again")
        return
    python = webui.split('"')[1] if webui.startswith('"') else webui.split()[0]
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        rerun = {**env, "KOLLAB_WEBUI_PORT": str(probe.getsockname()[1]), "PYTHONUNBUFFERED": "1"}
    code = (
        "import faulthandler; faulthandler.dump_traceback_later(30, exit=True); "
        "from kollabor_webui import main; main()"
    )
    print(f"--- {python} -X importtime -c <web UI main, stack dump after 30 s>")
    try:
        done = subprocess.run(
            [python, "-X", "importtime", "-c", code], env=rerun, capture_output=True, encoding="utf-8",
            errors="replace", timeout=90, cwd=env.get("USERPROFILE"),
        )
        print(f"exit {done.returncode}; output:\n{done.stdout[-3000:]}")
        print("\n".join(done.stderr.splitlines()[-80:]))
    except subprocess.TimeoutExpired as exc:
        print(f"still running after 90 s; last stderr:\n{str(exc.stderr or '')[-4000:]}")


def quit_owned_runtime(app: Path, runtime: Path, env: dict[str, str], root: Path) -> None:
    """The app starts its bundled runtime; --quit stops it through stdin and leaves no runtime process."""
    env = {k: v for k, v in env.items() if k != "KOLLAB_DESKTOP_SELFTEST_CLOSE"}
    # The build's own smoke ran this binary under a home it then deleted, and the scie cache still points at
    # the venv it made there: the Windows pin (pex 2.98.2) cannot recover from that (pex#3226). A fresh scie
    # base where a new user's is (AppData\Local\scie in their home) gives this run what their first launch gets.
    env["SCIE_BASE"] = str(root / "AppData" / "Local" / "scie")
    with socket.socket() as probe:  # a free port with no web UI on it, so the app starts its own
        probe.bind(("127.0.0.1", 0))
        env["KOLLAB_WEBUI_PORT"] = str(probe.getsockname()[1])
    runtime_version(runtime, env)
    log = root / "desktop-runtime.log"
    owned: dict[int, str] = {}
    with log.open("wb") as stream:
        process = subprocess.Popen([str(app)], env=env, stdout=stream, stderr=stream)
        started = time.monotonic()
        try:
            wait_log(process, log, "web UI is up at")
            owned = {
                pid: command
                for pid, command in descendants(process_table(), process.pid).items()
                if any(mark in command for mark in RUNTIME)
            }
            assert owned, "no runtime process under the app"
            quit_app(app, env, process, log)
            wait_log(process, log, "runtime stopped after stdin closed")
            table = process_table()
            left = {pid: command for pid, command in owned.items() if table.get(pid, (0, ""))[1] == command}
            assert not left, f"Quit left the runtime running: {left}"
            # The engine's lifespan ends with this line: a stopped engine never writes it.
            shutdown = "Engine shutdown - persistent WebUI agents detached"
            logs = [p for p in root.rglob("*.log") if shutdown in p.read_text(errors="replace")]
            assert logs, f"the engine never logged {shutdown!r}: it was ended, not shut down"
            print(f"engine shutdown logged in {logs[0].relative_to(root)}")
        except Exception:
            if not owned:  # the app never reported its web UI; the report must not hide that failure
                try:
                    report_webui(env, process.pid, process_table(), time.monotonic() - started)
                except Exception as exc:
                    print(f"(could not report on the web UI: {exc})")
            runtime_log = Path(os.environ.get("LOCALAPPDATA", "")) / "ai.kollabor.kollab" / "logs" / "webui.log"
            try:  # the app's runtime log, then the engine's and the agents' logs in this home; never hide the failure
                for path in [runtime_log, *sorted(root.rglob("*.log"))]:
                    if path != log and path.is_file() and "EBWebView" not in path.parts:  # WebView2 logs are binary
                        print(f"--- last 60 lines of {path}")
                        print("\n".join(path.read_text(errors="replace").splitlines()[-60:]))
            except OSError as exc:
                print(f"(could not read the logs: {exc})")
            raise
        finally:
            # Cleanup only, so a failed Quit leaves the runner clean; the same command line guards a reused pid.
            if process.poll() is None:
                subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
                process.wait(timeout=30)
            if owned:
                table = process_table()
                for pid, command in owned.items():
                    if table.get(pid, (0, ""))[1] == command:
                        subprocess.run(["taskkill.exe", "/PID", str(pid), "/T", "/F"], capture_output=True)
    print(log.read_text(errors="replace"))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("installer", type=Path)
    installer = parser.parse_args().installer.resolve()
    # The logs this prints can hold text the runner's cp1252 console cannot encode, as kollabor/cli.py knows.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    # This folder is the app's home. Under RUNNER_TEMP (D:\a\_temp) it is as deep as C:\Users\<20-letter name>:
    # the packaged interpreter's DLLs sit ~190 characters below the home, and Windows refused to load
    # _multiprocessing at 253 characters when the home was under the default temp folder.
    with tempfile.TemporaryDirectory(
        prefix="kollab-smoke-", dir=os.environ.get("RUNNER_TEMP"), ignore_cleanup_errors=True
    ) as scratch:
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
                    quit_app(app, env, process, log)
                finally:
                    if process.poll() is None:  # cleanup only: a Quit that failed has already raised
                        subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
                        process.wait(timeout=30)
            print(log.read_text(errors="replace"))
            server.shutdown()
        quit_owned_runtime(app, runtime, env, root)
    print("passed installed app: rendered, close to tray, second launch, clean quit")


if __name__ == "__main__":
    main()
