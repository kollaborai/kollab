"""A scripted local IMAP server for email-wake checks: no mailbox, no network.

Speaks just enough IMAP for ``plugins/hub/email_bridge.py``: CAPABILITY
(IDLE, plus X-GM-EXT-1 like Gmail), LOGIN, STATUS UIDNEXT, read-only SELECT,
UID SEARCH (honoring X-GM-RAW "category:primary"), UID FETCH BODY.PEEK[],
IDLE with EXISTS pushes, NOOP and LOGOUT. The inbox starts with two old mails.

usage:
  fake_imap.py serve <port file>
      Binds a free port on 127.0.0.1 and writes it to <port file>.
      SIGUSR1 delivers a new "Game night invite" mail.
  fake_imap.py run <port file> <kollab args...>
      Runs main.py from this checkout with IMAP4_SSL pointed at that server
      (plain IMAP on 127.0.0.1), so a real agent watches the fake inbox.
"""

import imaplib
import runpy
import select
import signal
import socketserver
import sys
import threading
from email.message import EmailMessage
from pathlib import Path


def mail(subject: str, body: str, html: bool = False) -> bytes:
    msg = EmailMessage()
    msg["From"] = "Kelly <kelly@example.com>"
    msg["To"] = "me@example.com"
    msg["Subject"] = subject
    msg["Date"] = "Thu, 22 Oct 2026 18:00:00 -0700"
    msg.set_content(body, subtype="html" if html else "plain")
    return bytes(msg)


class FakeMailbox:
    def __init__(self, gmail: bool = True):
        self.messages = {1: mail("old", "old mail"), 2: mail("older", "older")}
        self.gmail = gmail
        self.commands: list[str] = []
        self.arrived = threading.Event()
        self.idling = threading.Event()

    def deliver(self, raw: bytes) -> None:
        self.messages[max(self.messages) + 1] = raw
        self.arrived.set()


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        box: FakeMailbox = self.server.box
        out = self.wfile

        def send(line: str) -> None:
            out.write(line.encode() + b"\r\n")
            out.flush()

        send("* OK fake IMAP ready")
        while True:
            line = self.rfile.readline()
            if not line:
                return
            tag, _, rest = line.decode().strip().partition(" ")
            box.commands.append(rest)
            words = rest.split(" ")
            cmd = words[0].upper()
            sub = words[1].upper() if len(words) > 1 else ""
            if cmd == "CAPABILITY":
                caps = "IMAP4rev1 IDLE" + (" X-GM-EXT-1" if box.gmail else "")
                send(f"* CAPABILITY {caps}")
                send(f"{tag} OK done")
            elif cmd == "LOGIN":
                send(f"{tag} OK logged in")
            elif cmd == "STATUS":
                send(f'* STATUS "INBOX" (UIDNEXT {max(box.messages) + 1})')
                send(f"{tag} OK done")
            elif cmd in ("SELECT", "EXAMINE"):
                send(f"* {len(box.messages)} EXISTS")
                send(f"{tag} OK [READ-ONLY] selected")
            elif cmd == "UID" and sub == "SEARCH":
                low = int(rest.rsplit(" ", 1)[1].split(":")[0])
                uids = [u for u in box.messages if u >= low] or [max(box.messages)]
                if "X-GM-RAW" in rest and "category:primary" not in rest:
                    uids = []
                send("* SEARCH " + " ".join(str(u) for u in uids))
                send(f"{tag} OK done")
            elif cmd == "UID" and sub == "FETCH":
                uid = int(words[2])
                raw = box.messages[uid]
                out.write(f"* 1 FETCH (UID {uid} BODY[] {{{len(raw)}}}\r\n".encode() + raw + b")\r\n")
                send(f"{tag} OK done")
            elif cmd == "IDLE":
                send("+ idling")
                box.idling.set()
                # Push EXISTS for every arrival until the client ends IDLE.
                while not select.select([self.connection], [], [], 0.05)[0]:
                    if box.arrived.is_set():
                        box.arrived.clear()
                        send(f"* {len(box.messages)} EXISTS")
                self.rfile.readline()  # DONE
                box.idling.clear()
                send(f"{tag} OK idle done")
            elif cmd == "NOOP":
                send(f"{tag} OK done")
            elif cmd == "LOGOUT":
                send("* BYE")
                send(f"{tag} OK bye")
                return
            else:
                send(f"{tag} BAD unknown")


def start(gmail: bool = True) -> socketserver.ThreadingTCPServer:
    """A server on a free 127.0.0.1 port; ``server.box`` is its mailbox."""
    server = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    server.box = FakeMailbox(gmail)
    return server


if __name__ == "__main__":
    mode, port_file = sys.argv[1], Path(sys.argv[2])
    if mode == "serve":
        server = start()
        signal.signal(
            signal.SIGUSR1,
            lambda *_: server.box.deliver(
                mail("Game night invite", "Kelly is invited to game night Thursday at 7. Want to go?")
            ),
        )
        port_file.write_text(str(server.server_address[1]))
        server.serve_forever()
    elif mode == "run":
        port = int(port_file.read_text())
        imaplib.IMAP4_SSL = lambda host, timeout=None: imaplib.IMAP4("127.0.0.1", port, timeout=timeout)
        repo = Path(__file__).resolve().parents[3]
        sys.path.insert(0, str(repo))
        sys.argv = [str(repo / "main.py"), *sys.argv[3:]]
        runpy.run_path(sys.argv[0], run_name="__main__")
    else:
        sys.exit(__doc__)
