"""Engine smoke for Windows CI (kollab #268).

Starts `python -m kollabor_engine serve` with a scratch HOME, opens a session on a
scratch project, checks the session state and its daemon, closes it, and checks
that no process is left behind. On any mismatch it prints what failed and exits 1.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import IO, Any

import psutil

DEFAULT_PORT = 7491
RESERVED_PORTS = {7433, 8080}  # the engine's and the web UI's defaults
READY_TIMEOUT = 90.0
POST_TIMEOUT = 150.0
GET_TIMEOUT = 60.0
CLEANUP_TIMEOUT = 30.0
REPORT_LINES = 40
BODY_CHARS = 500
LOG_PATTERN = re.compile(r"WARNING|ERROR|CRITICAL|Traceback")
SUCCESS_LINE = (
    "engine started a session and its daemon, got the session state, closed it, "
    "and left no process behind"
)


class SmokeFailure(Exception):
    """A smoke step that did not behave as the brief requires."""

    def __init__(self, step: str, status: int | None = None, body: str = "") -> None:
        super().__init__(step)
        self.step = step
        self.status = status
        self.body = body


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Engine smoke for Windows CI.")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = parser.parse_args()
    if args.port in RESERVED_PORTS:
        parser.error(f"port {args.port} is a kollab default; pick a free one")
    return args


def _write_config(home: Path) -> None:
    kollab_dir = home / ".kollab"
    kollab_dir.mkdir(parents=True)
    config = {"application": {"setup_completed": True}}
    (kollab_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")


def _pump_stdout(stream: IO[str], lines: queue.Queue[str | None]) -> None:
    for line in iter(stream.readline, ""):
        lines.put(line.rstrip("\r\n"))
    lines.put(None)


def _wait_ready(lines: queue.Queue[str | None], port: int) -> None:
    deadline = time.monotonic() + READY_TIMEOUT
    expected = f"READY:{port}"
    while True:
        remaining = max(deadline - time.monotonic(), 0)
        try:
            line = lines.get(timeout=remaining)
        except queue.Empty:
            raise SmokeFailure("engine did not report READY") from None
        if line is None:
            raise SmokeFailure("engine did not report READY (engine exited first)")
        if line.startswith(expected):
            return


def _read_token(home: Path) -> str:
    token_file = home / ".kollab" / "engine.token"
    try:
        return token_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise SmokeFailure(f"engine token not readable: {exc}") from exc


def _request(
    method: str,
    url: str,
    token: str,
    payload: dict[str, Any] | None,
    timeout: float,
) -> tuple[int, str]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Authorization": f"Bearer {token}"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def _call(
    step: str,
    method: str,
    url: str,
    token: str,
    payload: dict[str, Any] | None,
    timeout: float,
) -> tuple[int, str]:
    try:
        return _request(method, url, token, payload, timeout)
    except OSError as exc:
        raise SmokeFailure(step, None, str(exc)) from exc


def _run_session(port: int, token: str, project: Path, home: Path) -> list[int]:
    """Open a session, check its state and daemon, close it; return the daemon pids."""
    base = f"http://127.0.0.1:{port}"

    step = "POST /sessions"
    status, body = _call(
        step,
        "POST",
        f"{base}/sessions",
        token,
        {"workspace": str(project)},
        POST_TIMEOUT,
    )
    if status != 200:
        raise SmokeFailure(step, status, body)
    try:
        session_id = str(json.loads(body)["session_id"])
    except (ValueError, KeyError, TypeError) as exc:
        raise SmokeFailure(
            f"{step} returned 200 without a session_id", status, body
        ) from exc

    step = "GET /sessions/<id>/state"
    status, body = _call(
        step, "GET", f"{base}/sessions/{session_id}/state", token, None, GET_TIMEOUT
    )
    if status != 200:
        raise SmokeFailure(step, status, body)
    try:
        state = json.loads(body)
    except ValueError:
        state = None
    if not isinstance(state, dict) or not state:
        raise SmokeFailure(f"{step} returned 200 without a state object", status, body)
    daemons = _daemon_pids(home)
    if not daemons:
        raise SmokeFailure("no live session daemon in hub presence")

    step = "DELETE /sessions/<id>"
    status, body = _call(
        step, "DELETE", f"{base}/sessions/{session_id}", token, None, GET_TIMEOUT
    )
    if status != 200:
        raise SmokeFailure(step, status, body)
    return daemons


def _homed_pids(home: str, skip: set[int]) -> list[int]:
    pids: list[int] = []
    for proc in psutil.process_iter():
        if proc.pid in skip:
            continue
        try:
            env = proc.environ()
        except psutil.Error:
            continue
        if env.get("HOME") == home or env.get("USERPROFILE") == home:
            pids.append(proc.pid)
    return pids


def _alive(pid: int) -> bool:
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False
    except psutil.Error:  # cannot read it, so it may still run
        return True


def _describe(pids: list[int]) -> str:
    parts = []
    for pid in pids:
        try:
            proc = psutil.Process(pid)
            args = " ".join(proc.cmdline()[1:4])  # enough to name it
            parts.append(f"{pid} {proc.name()} {args} (parent {proc.ppid()})")
        except psutil.Error:
            parts.append(f"{pid} (unreadable)")
    return ", ".join(parts)


def _daemon_pids(home: Path) -> list[int]:
    """Live pids the session daemons wrote to their hub presence files."""
    pids: list[int] = []
    for record in (home / ".kollab").rglob("*.json"):
        if "presence" not in record.parts:
            continue
        try:
            pid = int(json.loads(record.read_text(encoding="utf-8")).get("pid") or 0)
        except OSError, ValueError, AttributeError, TypeError:
            continue
        if pid > 0 and _alive(pid):
            pids.append(pid)
    return pids


def _engine_pids(engine: subprocess.Popen[str]) -> set[int]:
    """The engine, and the interpreter a Windows venv launcher starts for it."""
    pids = {engine.pid}
    try:
        children = psutil.Process(engine.pid).children()
    except psutil.Error:
        return pids
    for child in children:
        try:
            if child.cmdline()[1:] == list(engine.args)[1:]:
                pids.add(child.pid)
                print(f"engine runs as pid {child.pid} under launcher pid {engine.pid}")
        except psutil.Error:
            continue
    return pids


def _log_has(home: Path, text: str) -> bool:
    for log in (home / ".kollab").rglob("*.log"):
        try:
            if text in log.read_text(encoding="utf-8", errors="replace"):
                return True
        except OSError:
            continue
    return False


def _check_windows_stop(home: Path) -> None:
    """Windows has no SIGTERM: the engine asks the daemon to stop over local IPC."""
    if not _log_has(home, "Shutdown requested: session closed"):
        raise SmokeFailure("the daemon never got the engine's shutdown request")
    if _log_has(home, "did not stop on request"):
        raise SmokeFailure("the daemon did not stop on request and was ended")
    print("the daemon stopped on the engine's shutdown request")


def _wait_for_daemons_gone(
    home: str, engine_pids: set[int], daemons: list[int]
) -> None:
    deadline = time.monotonic() + CLEANUP_TIMEOUT
    while True:
        homed = _homed_pids(home, engine_pids)
        pids = sorted(set(homed) | {pid for pid in daemons if _alive(pid)})
        if not pids:
            return
        if time.monotonic() >= deadline:
            raise SmokeFailure(f"still running after DELETE: {_describe(pids)}")
        time.sleep(1)


def _kill_homed(home: str) -> None:
    for pid in _homed_pids(home, set()):
        try:
            psutil.Process(pid).kill()
        except psutil.Error:
            pass


def _stop_engine(engine: subprocess.Popen[str]) -> None:
    if engine.poll() is not None:
        return
    engine.terminate()
    try:
        engine.wait(15)
    except subprocess.TimeoutExpired:
        engine.kill()
        engine.wait()


def _tail(path: Path, pattern: re.Pattern[str] | None = None) -> list[str]:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    if pattern is not None:
        lines = [line for line in lines if pattern.search(line)]
    return lines[-REPORT_LINES:]


def _report(failure: SmokeFailure, home: Path, stderr_path: Path) -> None:
    print(f"FAILED: {failure.step}")
    if failure.status is not None:
        print(f"HTTP status: {failure.status}")
    if failure.body:
        print(f"body: {failure.body[:BODY_CHARS]}")
    print(f"--- last {REPORT_LINES} lines of engine-stderr.txt ---")
    print("\n".join(_tail(stderr_path)))
    for log in sorted((home / ".kollab").rglob("*.log")):
        print(f"--- {log.name}: last {REPORT_LINES} ERROR/CRITICAL/Traceback lines ---")
        print("\n".join(_tail(log, LOG_PATTERN)))


def main() -> int:
    args = _parse_args()
    root = Path(tempfile.mkdtemp(prefix="kollab-engine-smoke-"))
    home = root / "home"
    project = root / "project"
    stderr_path = root / "engine-stderr.txt"
    home_str = str(home)
    engine: subprocess.Popen[str] | None = None
    stderr_file: IO[str] | None = None
    failure: SmokeFailure | None = None
    daemons: list[int] = []
    try:
        project.mkdir(parents=True)
        _write_config(home)
        env = {
            **os.environ,
            "HOME": home_str,
            "USERPROFILE": home_str,
            "KOLLAB_NO_KEYRING": "1",
            "KOLLAB_HUB_SOLO": "1",
            "KOLLAB_WEBUI_NO_BROWSER": "1",
        }
        stderr_file = stderr_path.open("w", encoding="utf-8")
        engine = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "kollabor_engine",
                "serve",
                "--port",
                str(args.port),
            ],
            cwd=project,
            env=env,
            stdout=subprocess.PIPE,
            stderr=stderr_file,
            text=True,
        )
        lines: queue.Queue[str | None] = queue.Queue()
        threading.Thread(
            target=_pump_stdout, args=(engine.stdout, lines), daemon=True
        ).start()
        _wait_ready(lines, args.port)
        engine_pids = _engine_pids(engine)
        token = _read_token(home)
        daemons = _run_session(args.port, token, project, home)
        _wait_for_daemons_gone(home_str, engine_pids, daemons)
        if os.name == "nt":
            _check_windows_stop(home)
    except SmokeFailure as exc:
        failure = exc
    finally:
        if engine is not None:
            _stop_engine(engine)
        _kill_homed(home_str)
        for pid in daemons:
            if _alive(pid):
                try:
                    psutil.Process(pid).kill()
                except psutil.Error:
                    pass
        if stderr_file is not None:
            stderr_file.close()
        if failure is not None:
            _report(failure, home, stderr_path)
        shutil.rmtree(root, ignore_errors=True)
    if failure is not None:
        return 1
    print(SUCCESS_LINE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
