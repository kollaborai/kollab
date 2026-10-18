"""Open this HOME's only live agent in the engine and read its history mid-turn.

What a second web window does when it opens an agent whose turn another window
(or the agent's own terminal) started a moment ago: the history it first gets
must hold the message being answered (specs/web_open_midturn_shows_message.json).

usage (HOME=<spec home>, from the agent's folder):
  web_open_midturn.py start [--cold]   start kollabor_engine from this checkout and open the agent once
                                       (--cold: only list it, so the history request below opens it)
  web_open_midturn.py history <text>   GET the agent's history now; exit 1 unless <text> is in it as a user message
  web_open_midturn.py stop             stop the engine

The engine listens on KOLLAB_SPEC_ENGINE_PORT when set, else on a free port.
"""

import http.client
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _get(port, path):
    token = (HOME / ".kollab" / "engine.token").read_text().strip()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request("GET", path, headers={"Authorization": f"Bearer {token}"})
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read() or b"null")
    finally:
        conn.close()


def _copies(history, role, text):
    return sum(
        1
        for m in history
        if m.get("role") == role and text in json.dumps(m.get("content"))
    )


def start(cold: bool) -> None:
    port = int(os.environ.get("KOLLAB_SPEC_ENGINE_PORT") or _free_port())
    log = open(HOME / "engine.log", "ab")
    engine = subprocess.Popen(
        [sys.executable, "-m", "kollabor_engine", "serve", "--port", str(port)],
        stdout=log,
        stderr=subprocess.STDOUT,
        env={k: v for k, v in os.environ.items() if k != "KOLLAB_WEBUI_HOSTS"},
        start_new_session=True,
    )
    (HOME / "engine.pid").write_text(str(engine.pid))
    deadline = time.time() + 40
    session_id = ""
    while time.time() < deadline and not session_id:
        time.sleep(0.5)
        try:
            status, listing = _get(port, "/sessions")
        except (OSError, http.client.HTTPException):
            continue
        live = [
            row for row in (listing or {}).get("discovered", []) if row.get("active")
        ]
        if status == 200 and len(live) == 1:
            session_id = live[0]["session_id"]
    if not session_id:
        sys.exit("engine never listed exactly one live agent")
    if not cold:
        # Open it once and wait until the engine holds the daemon's own history:
        # the warm mirror a second window finds.
        while time.time() < deadline:
            _, answer = _get(port, f"/sessions/{session_id}/history")
            if answer.get("source") == "mirror":
                break
            time.sleep(0.3)
        else:
            sys.exit("the engine never reconciled the agent's history")
    (HOME / "engine.port").write_text(str(port))
    (HOME / "engine.session").write_text(session_id)
    print(f"engine on {port} has {'listed' if cold else 'opened'} {session_id}")


def history(text: str) -> None:
    port = int((HOME / "engine.port").read_text())
    session_id = (HOME / "engine.session").read_text()
    _, answer = _get(port, f"/sessions/{session_id}/history")
    shown = answer["history"]
    users, replies = _copies(shown, "user", text), _copies(shown, "assistant", text)
    print(
        f"history from the {answer['source']}: user copies={users} "
        f"assistant copies={replies} of {len(shown)} messages"
    )
    if not users:
        sys.exit("the history does not hold the message being answered")


def stop() -> None:
    pid_file = HOME / "engine.pid"
    if pid_file.exists():
        try:
            os.kill(int(pid_file.read_text()), signal.SIGTERM)
        except (ProcessLookupError, ValueError):
            pass


if __name__ == "__main__":
    command, *rest = sys.argv[1:] or ["start"]
    if command == "start":
        start("--cold" in rest)
    elif command == "history":
        history(rest[0])
    else:
        stop()
