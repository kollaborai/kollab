"""Send a turn to this HOME's only live agent the way the web UI does, then leave the chat.

Starts kollabor_engine from this checkout on a free port, posts the message to
the agent's assistant-transport route, reads the start of the stream and closes
the connection: what the browser does when another chat is opened mid-turn.
The agent must finish the turn anyway. With --stop the page presses Stop as
well (POST /cancel), which must end the turn
(specs/web_chat_switch_keeps_turn.json).

usage: HOME=<spec home> web_send_then_leave.py [--stop] <text>
"""

import http.client
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _call(port, method, path, body=None, timeout=10):
    token = (Path.home() / ".kollab" / "engine.token").read_text().strip()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    conn.request(method, path, json.dumps(body) if body is not None else None, headers)
    return conn, conn.getresponse()


def _get(port, path):
    conn, resp = _call(port, "GET", path)
    try:
        return resp.status, json.loads(resp.read() or b"null")
    finally:
        conn.close()


def main(text: str, stop: bool) -> None:
    port = _free_port()
    log = open(Path.home() / "engine.log", "ab")
    engine = subprocess.Popen(
        [sys.executable, "-m", "kollabor_engine", "serve", "--port", str(port)],
        stdout=log,
        stderr=subprocess.STDOUT,
        env={k: v for k, v in os.environ.items() if k != "KOLLAB_WEBUI_HOSTS"},
    )
    try:
        deadline = time.time() + 40
        session_id = ""
        while time.time() < deadline and not session_id:
            time.sleep(0.5)
            try:
                status, listing = _get(port, "/sessions")
            except (OSError, http.client.HTTPException):
                continue
            live = [row for row in (listing or {}).get("discovered", []) if row.get("active")]
            if status == 200 and len(live) == 1:
                session_id = live[0]["session_id"]
        if not session_id:
            sys.exit("engine never listed exactly one live agent")

        message = {"role": "user", "parts": [{"type": "text", "text": text}]}
        conn, resp = _call(
            port,
            "POST",
            f"/sessions/{session_id}/assistant",
            {"commands": [{"type": "add-message", "message": message}]},
            timeout=3,
        )
        try:
            resp.read1(4096)
        except (TimeoutError, socket.timeout):
            pass
        conn.close()  # the page opened another chat: its stream is gone
        print(f"left the chat mid-turn (HTTP {resp.status})")
        if stop:
            conn, resp = _call(port, "POST", f"/sessions/{session_id}/cancel", {})
            print(f"pressed Stop (HTTP {resp.status})")
            conn.close()

        deadline = time.time() + (15 if stop else 30)
        while time.time() < deadline:
            time.sleep(1)
            _, history = _get(port, f"/sessions/{session_id}/history")
            if f"pong: {text}" in json.dumps(history):
                print("the agent finished the turn")
                return
        print("the agent never finished the turn")
    finally:
        engine.terminate()
        try:
            engine.wait(timeout=10)
        except subprocess.TimeoutExpired:
            engine.kill()


if __name__ == "__main__":
    args = sys.argv[1:]
    stop = "--stop" in args
    words = [arg for arg in args if arg != "--stop"]
    main(words[0] if words else "slowreply web switch", stop)
