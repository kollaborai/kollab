#!/usr/bin/env python3
"""engine.py <command> <port> [args]: the engine HTTP calls of tests/live/attach/proof.sh.

Stdlib only, so it runs under any python3. The token is read from
~/.kollab/engine.token on every call (the engine writes it at start).

  health <port> <seconds>                 wait until /health answers
  create <port> <profile> <workspace>     POST /sessions; prints the session id
  remote <port> <name|-> <seconds>        the agent@device handle of <name> (- for any) on another computer
  device <port>                           this computer's network name
  history <port> <id>                     prints "<status> <message count>" or "<status> <detail>"
  dump <port> <id>                        the session history as JSON
  send <port> <id> <text>                 POST a message
  reply <port> <id> <regex> <seconds>     wait for an assistant message matching regex
  gone <port> <id> <seconds>              wait until the session is gone (404)
  delete <port> <id>                      DELETE /sessions/<id>
"""

import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def call(port, method, path, body=None, timeout=60):
    token = (Path.home() / ".kollab" / "engine.token").read_text().strip()
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as error:
        raw = error.read()
        try:
            return error.code, json.loads(raw)
        except ValueError:
            return error.code, {"detail": raw.decode(errors="replace")}


def quote(session_id):
    return urllib.parse.quote(session_id, safe="")


def messages(payload):
    if isinstance(payload, dict):
        for key in ("messages", "history"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return payload if isinstance(payload, list) else []


def text_of(message):
    content = message.get("content", "")
    if isinstance(content, list):
        return " ".join(str(part.get("text", "")) for part in content if isinstance(part, dict))
    return str(content)


def main(argv):
    command, port, *args = argv
    if command == "health":
        deadline = time.time() + float(args[0])
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3):
                    return 0
            except OSError:
                time.sleep(1)
        return 1
    if command == "create":
        status, body = call(port, "POST", "/sessions", {"profile": args[0], "workspace": args[1]}, 180)
        if status != 200:
            print(f"{status} {body}", file=sys.stderr)
            return 1
        print(body["session_id"])
        return 0
    if command in ("remote", "device"):
        deadline = time.time() + float(args[1] if command == "remote" else 30)
        while time.time() < deadline:
            status, body = call(port, "GET", "/sessions")
            network = (body or {}).get("network") or {}
            if command == "device" and network.get("device"):
                print(network["device"])
                return 0
            for row in network.get("remote") or []:
                if command == "remote" and args[0] in ("-", row.get("name")) and row.get("handle"):
                    print(row["handle"])
                    return 0
            time.sleep(3)
        return 1
    if command in ("history", "dump"):
        status, body = call(port, "GET", f"/sessions/{quote(args[0])}/history", timeout=120)
        if command == "dump":
            print(json.dumps(body, indent=1, default=str))
        elif status == 200:
            print(f"{status} {len(messages(body))}")
        else:
            print(f"{status} {(body or {}).get('detail', body)}")
        return 0
    if command == "send":
        status, body = call(port, "POST", f"/sessions/{quote(args[0])}/message", {"content": args[1]}, 300)
        print(f"{status}")
        return 0 if status in (200, 202) else 1
    if command == "reply":
        pattern = re.compile(args[1], re.IGNORECASE)
        deadline = time.time() + float(args[2])
        while time.time() < deadline:
            status, body = call(port, "GET", f"/sessions/{quote(args[0])}/history", timeout=60)
            for message in reversed(messages(body)):
                if message.get("role") == "assistant" and pattern.search(text_of(message)):
                    print(text_of(message)[:400])
                    return 0
            time.sleep(4)
        return 1
    if command == "gone":
        # GET /sessions/<id> reaps a dead session (404); asked again, the engine tries
        # to open it anew and the other computer refuses (503).
        deadline = time.time() + float(args[1])
        while time.time() < deadline:
            status, _ = call(port, "GET", f"/sessions/{quote(args[0])}", timeout=30)
            if status in (404, 503):
                print(status)
                return 0
            time.sleep(2)
        return 1
    if command == "delete":
        status, _ = call(port, "DELETE", f"/sessions/{quote(args[0])}", timeout=60)
        print(status)
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
