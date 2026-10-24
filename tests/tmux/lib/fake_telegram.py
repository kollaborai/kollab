"""A scripted local Telegram Bot API for bridge checks: no bot, no network.

Answers just what ``TelegramBridge`` in ``plugins/hub/messaging_bridge.py``
calls: getMe, getUpdates (long-poll with offset) and sendMessage. Every
sendMessage text is appended, one JSON line each, to ``sent.jsonl`` beside the
port file.

usage:
  fake_telegram.py serve <port file>
      Binds a free port on 127.0.0.1 and writes it to <port file>.
      SIGUSR1 queues "ping from telegram" from chat 42, user 7 ("Marco").
  fake_telegram.py run <port file> <kollab args...>
      Runs main.py from this checkout with api.telegram.org requests sent to
      that server, so a real agent polls the fake bot.
"""

import json
import runpy
import signal
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

CHAT_ID = 42


class FakeBot:
    def __init__(self, sent_log: Path):
        self.updates: list = []
        self.cond = threading.Condition()
        self.sent_log = sent_log

    def queue(self, text: str) -> None:
        with self.cond:
            update_id = len(self.updates) + 1
            self.updates.append({
                "update_id": update_id,
                "message": {
                    "message_id": update_id,
                    "date": 0,
                    "text": text,
                    "chat": {"id": CHAT_ID},
                    "from": {"id": 7, "first_name": "Marco"},
                },
            })
            self.cond.notify_all()

    def updates_from(self, offset: int, timeout: float) -> list:
        with self.cond:
            self.cond.wait_for(
                lambda: any(u["update_id"] >= offset for u in self.updates), timeout
            )
            return [u for u in self.updates if u["update_id"] >= offset]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _reply(self, result) -> None:
        body = json.dumps({"ok": True, "result": result}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        url = urlparse(self.path)
        bot = self.server.bot
        if url.path.endswith("/getMe"):
            self._reply({"id": 1, "first_name": "Fake", "username": "fake_bot"})
        elif url.path.endswith("/getUpdates"):
            query = parse_qs(url.query)
            offset = int(query.get("offset", ["0"])[0])
            timeout = min(float(query.get("timeout", ["0"])[0]), 5.0)
            self._reply(bot.updates_from(offset, timeout))
        else:
            self.send_error(404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        if self.path.endswith("/sendMessage"):
            with self.server.bot.sent_log.open("a") as log:
                log.write(json.dumps({"text": payload.get("text", "")}) + "\n")
            self._reply({"message_id": 1})
        else:
            self.send_error(404)


def start(sent_log: Path) -> ThreadingHTTPServer:
    """A server on a free 127.0.0.1 port; ``server.bot`` is its bot."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.bot = FakeBot(sent_log)
    return server


if __name__ == "__main__":
    mode, port_file = sys.argv[1], Path(sys.argv[2])
    if mode == "serve":
        server = start(port_file.parent / "sent.jsonl")
        signal.signal(signal.SIGUSR1, lambda *_: server.bot.queue("ping from telegram"))
        port_file.write_text(str(server.server_address[1]))
        server.serve_forever()
    elif mode == "run":
        port = int(port_file.read_text())
        repo = Path(__file__).resolve().parents[3]
        sys.path.insert(0, str(repo))
        import httpx

        send = httpx.AsyncClient.send

        async def local_send(self, request, **kwargs):
            if request.url.host == "api.telegram.org":
                request.url = request.url.copy_with(scheme="http", host="127.0.0.1", port=port)
            return await send(self, request, **kwargs)

        httpx.AsyncClient.send = local_send
        sys.argv = [str(repo / "main.py"), *sys.argv[3:]]
        runpy.run_path(sys.argv[0], run_name="__main__")
    else:
        sys.exit(__doc__)
