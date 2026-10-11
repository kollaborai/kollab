"""A local OpenAI-compatible chat server for tmux specs: no keys, no network, no cost.

Replies "pong: <the last line of the last user message>". A message containing
"runtool" gets a <terminal> tool call instead, so a spec can drive a permission
prompt; the follow-up carries the tool's result, which is answered plainly.
"fncall" gets the same kind of call written the way some models leak a native
call into the reply: <functions.terminal>{"command": ...}</functions.terminal>.
"echocall" gets a native terminal call whose reply also copies calls as
<tool_call> text, one without a name, as gpt-5.6-luna did; only the native one
may run. The answer to a native tool result is "pong: the tool answered".
"slowreply" holds the answer for SLOW_SECONDS, so a spec can act mid-turn.

usage: fake_llm.py <port file>
Binds a free port on 127.0.0.1 and writes it to <port file> once listening.
"""

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

SLOW_SECONDS = 8


def _last_user_text(body):
    for message in reversed(body.get("messages") or []):
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, list):
            content = " ".join(part.get("text", "") for part in content if isinstance(part, dict))
        return str(content or "")
    return ""


def _last_line(body):
    lines = [line.strip() for line in _last_user_text(body).splitlines() if line.strip()]
    return lines[-1] if lines else ""


def _answers_a_tool(body):
    messages = body.get("messages") or []
    return bool(messages) and messages[-1].get("role") == "tool"


def _native_calls(body):
    if _answers_a_tool(body) or "echocall" not in _last_line(body):
        return []
    arguments = json.dumps({"command": "printf 'native-%s-ran' once"})
    return [{"id": "call_echo_1", "type": "function", "function": {"name": "terminal", "arguments": arguments}}]


def _reply(body):
    if _answers_a_tool(body):
        return "pong: the tool answered"
    last = _last_line(body)
    if "echocall" in last:
        # Text copies beside the native call: echo-*-ran shows only if one runs.
        nameless = json.dumps({"command": "printf 'echo-%s-ran' nameless"})
        named = json.dumps({"name": "terminal", "arguments": {"command": "printf 'echo-%s-ran' named"}})
        return f"Running it.\n<tool_call>{nameless}</tool_call>\n<tool_call>{named}</tool_call>"
    if "runtool" in last:
        return "Running it.\n<terminal>echo fake-tool-ran</terminal>"
    if "fncall" in last:
        # The output (fn-text-ran) appears only if the command really ran.
        command = json.dumps({"command": "printf 'fn-%s-ran' text"})
        return f"Running it.\n<functions.terminal>{command}</functions.terminal>"
    return "pong: " + last[:200]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, code, payload):
        data = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.rstrip("/").endswith("/models"):
            self._json(200, {"object": "list", "data": [{"id": "fake-echo", "object": "model"}]})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if "slowreply" in _last_line(body) and not _answers_a_tool(body):
            time.sleep(SLOW_SECONDS)
        reply = _reply(body)
        calls = _native_calls(body)
        finish = "tool_calls" if calls else "stop"
        created = int(time.time())
        usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        if not body.get("stream"):
            self._json(200, {
                "id": "fake-1", "object": "chat.completion", "created": created, "model": "fake-echo",
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": reply, **({"tool_calls": calls} if calls else {})},
                    "finish_reason": finish,
                }],
                "usage": usage,
            })
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def chunk(delta, finish=None, extra=None):
            payload = {
                "id": "fake-1", "object": "chat.completion.chunk", "created": created, "model": "fake-echo",
                "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                **(extra or {}),
            }
            self.wfile.write(f"data: {json.dumps(payload)}\n\n".encode())
            self.wfile.flush()

        chunk({"role": "assistant", "content": ""})
        for i in range(0, len(reply), 12):
            chunk({"content": reply[i:i + 12]})
        if calls:
            chunk({"tool_calls": [{"index": i, **call} for i, call in enumerate(calls)]})
        chunk({}, finish=finish, extra={"usage": usage})
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Path(sys.argv[1]).write_text(str(server.server_address[1]))
    server.serve_forever()
