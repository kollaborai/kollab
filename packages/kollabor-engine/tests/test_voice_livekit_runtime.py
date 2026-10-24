"""Engine LiveKit voice runtime: fake livekit-server, fake worker, fake session."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from kollabor_engine import voice_livekit
from kollabor_engine.routes import voice as voice_routes
from kollabor_engine.voice_control import VoiceControl, decode_cursor
from kollabor_engine.voice_livekit import LiveKitRuntime, mint_livekit_jwt

FAKE_SERVER = """\
#!{py}
import socket, sys, time
port = int(sys.argv[sys.argv.index("--port") + 1])
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", port)); s.listen(5)
while True: time.sleep(1)
"""

FAKE_WORKER = """\
import http.server, json, os, sys, threading
cfg = json.loads(os.environ["KOLLAB_VOICE_WORKER_CONFIG"])
log = os.environ["FAKE_WORKER_LOG"]
with open(log + ".cfg", "w") as f: json.dump(cfg, f)
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
        with open(log, "a") as f: f.write(self.path + " " + body + "\\n")
        self.send_response(200); self.end_headers(); self.wfile.write(b"{}")
        if self.path == "/stop": threading.Thread(target=srv.shutdown).start()
srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
print("WORKER_READY http://127.0.0.1:%d" % srv.server_address[1], flush=True)
srv.serve_forever()
"""


class FakeSession:
    def __init__(self, session_id="s1"):
        self.session_id = session_id
        self.alive = True
        self.agent = "lapis"
        self.daemon = None
        self.profile = SimpleNamespace(
            name="p1", provider="openai", base_url="https://api.openai.com/v1"
        )
        self.queues = []
        self.sent = []
        self.script = []

    def subscribe(self):
        q = asyncio.Queue()
        self.queues.append(q)
        return q

    def unsubscribe(self, q):
        if q in self.queues:
            self.queues.remove(q)

    async def send_message(self, message, *, request_id, thread_id, origin_context):
        self.sent.append((message, request_id, thread_id, origin_context))
        events = list(self.script)

        async def emit():
            for ev in events:
                await asyncio.sleep(0.01)
                for q in list(self.queues):
                    q.put_nowait({**ev, "request_id": request_id})

        asyncio.create_task(emit())
        return {"accepted": True, "queued": True, "request_id": request_id}

    async def cancel_owned_turn(self, request_id):
        return {"cancelled": True}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    server = tmp_path / "livekit-server"
    server.write_text(FAKE_SERVER.format(py=sys.executable))
    server.chmod(0o755)
    worker = tmp_path / "worker.py"
    worker.write_text(FAKE_WORKER)
    log = tmp_path / "worker.log"
    session = FakeSession()
    registry = {"s1": session}
    runtime = LiveKitRuntime(
        engine_url=lambda: "http://127.0.0.1:1",
        engine_token=lambda: "tok",
        server_binary=str(server),
        worker_argv=[sys.executable, str(worker)],
        worker_env={"FAKE_WORKER_LOG": str(log)},
    )
    control = VoiceControl(
        runtime=runtime,
        registry=lambda: registry,
        platform_supported=lambda: True,
        poll_interval=0.02,
    )
    app = FastAPI()
    app.state.voice = control
    app.include_router(voice_routes.router)
    return SimpleNamespace(
        control=control, runtime=runtime, session=session, app=app, log=log
    )


def client(app):
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://t"
    )


async def wait_for(predicate, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        await asyncio.sleep(0.02)
    raise AssertionError("timed out")


async def ready_session(rig, http):
    created = await http.post("/sessions/s1/voice", json={})
    assert created.status_code == 202, created.text
    view = created.json()
    for _ in range(250):
        got = (await http.get(f"/voice/sessions/{view['voice_session_id']}")).json()
        if got["state"] == "ready":
            return got
        await asyncio.sleep(0.02)
    raise AssertionError(got)


def b64d(part):
    return json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))


def transcript(view, text="hello there", turn=None):
    return {
        "transcript_id": str(uuid4()),
        "voice_session_id": view["voice_session_id"],
        "turn_id": turn or str(uuid4()),
        "generation": view["generation"],
        "speaker": "user",
        "revision": 1,
        "final": True,
        "display_text": text,
        "spoken_text": None,
        "language": "en",
        "observed_at_utc_ms": 1,
    }


def test_jwt_is_hs256_with_grants():
    token = mint_livekit_jwt(
        "k",
        "secret",
        identity="me",
        video={"room": "r", "roomJoin": True},
        expires_at_s=2000,
        now_s=1000,
    )
    head, body, sig = token.split(".")
    assert b64d(head) == {"alg": "HS256", "typ": "JWT"}
    claims = b64d(body)
    assert (claims["iss"], claims["sub"], claims["exp"], claims["video"]["room"]) == (
        "k",
        "me",
        2000,
        "r",
    )
    expected = (
        base64.urlsafe_b64encode(
            hmac.new(b"secret", f"{head}.{body}".encode(), hashlib.sha256).digest()
        )
        .rstrip(b"=")
        .decode()
    )
    assert sig == expected


async def test_full_voice_turn(rig):
    rig.session.script = [
        {"type": "token", "text": "Sure thing."},
        {
            "type": "response",
            "text": "<display_text>Sure thing, **done**.</display_text><spoken_text>Sure thing, done.</spoken_text>",
        },
        {"type": "turn_complete"},
    ]
    async with client(rig.app) as http:
        view = await ready_session(rig, http)
        assert (
            view["remote_llm"] is True and view["remote_llm_host"] == "api.openai.com"
        )
        assert view["agent_id"] == "lapis"

        ticket = (
            await http.post(
                f"/voice/sessions/{view['voice_session_id']}/join",
                json={"generation": 1},
            )
        ).json()
        claims = b64d(ticket["participant_token"].split(".")[1])
        assert claims["video"]["room"] == ticket["room_name"]
        assert claims["video"]["canPublishSources"] == ["microphone"]
        assert (
            ticket["livekit_url"] == rig.runtime.livekit_url
            and "127.0.0.1" in ticket["livekit_url"]
        )
        cfg = json.loads((rig.log.parent / "worker.log.cfg").read_text())
        assert cfg["room"] == ticket["room_name"] and cfg["engine_token"] == "tok"

        stream = await rig.control.subscribe(view["voice_session_id"], None)
        events = []

        async def collect():
            async for e in stream:
                events.append(e)

        task = asyncio.create_task(collect())
        body = transcript(view)
        resp = await http.post(
            f"/voice/sessions/{view['voice_session_id']}/transcripts", json=body
        )
        assert resp.status_code == 200 and resp.json()["accepted"] is True
        # duplicate turn = idempotent receipt, no second send
        dup = await http.post(
            f"/voice/sessions/{view['voice_session_id']}/transcripts", json=body
        )
        assert dup.json()["duplicate"] is True and len(rig.session.sent) == 1
        message, request_id, _thread, ctx = rig.session.sent[0]
        assert (
            message == "hello there"
            and request_id == body["turn_id"]
            and ctx["source"] == "livekit_voice"
        )

        await wait_for(lambda: any(e["event"] == "voice.metrics" for e in events))
        finals = [
            e
            for e in events
            if e["event"] == "voice.transcript"
            and e["payload"]["speaker"] == "assistant"
            and e["payload"]["final"]
        ]
        assert (
            len(finals) == 1
            and finals[0]["payload"]["spoken_text_chunk"] == "Sure thing, done."
        )
        assert "**" in finals[0]["payload"]["display_text_chunk"]
        seqs = [
            e["sequence"]
            for e in events
            if e["event"] != "voice.state" or e["sequence"]
        ]
        assert seqs == sorted(seqs)
        spoken = await wait_for(
            lambda: rig.log.exists()
            and [
                ln for ln in rig.log.read_text().splitlines() if ln.startswith("/speak")
            ]
        )
        assert (
            json.loads(spoken[0].split(" ", 1)[1])["spoken_text"] == "Sure thing, done."
        )
        # final transcript published before speech started
        assert events.index(finals[0]) < next(
            i for i, e in enumerate(events) if e["payload"].get("state") == "speaking"
        )

        stopped = await http.request(
            "DELETE",
            f"/voice/sessions/{view['voice_session_id']}",
            json={"generation": 1, "reason": "user"},
        )
        assert stopped.status_code == 200 and stopped.json()["generation"] == 2
        stale = await http.post(
            f"/voice/sessions/{view['voice_session_id']}/join", json={"generation": 1}
        )
        assert stale.status_code == 409 and stale.json()["code"] == "stale_generation"
        await wait_for(lambda: not rig.control.runtime._workers)
        await asyncio.wait_for(task, 3)
        assert events[-1]["payload"].get("state") == "stopped"
        assert rig.log.read_text().count("/stop") == 1

    server_pid = rig.runtime._server.pid
    await rig.control.shutdown()
    await wait_for(lambda: _gone(server_pid))


def _gone(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    return False


async def test_late_events_after_stop_never_publish(rig):
    rig.session.script = [
        {"type": "response", "text": "late reply"},
        {"type": "turn_complete"},
    ]
    async with client(rig.app) as http:
        view = await ready_session(rig, http)
        sid = view["voice_session_id"]
        await http.request(
            "DELETE", f"/voice/sessions/{sid}", json={"generation": 1, "reason": "user"}
        )
        published = []
        orig = rig.control.hub.publish
        rig.control.hub.publish = lambda v, e, p, **k: published.append(
            (v.generation, e)
        ) or orig(v, e, p, **k)
        ok = await rig.control._emit(sid, 1, "voice.transcript", {"x": 1})
        assert ok is False and published == []
    await rig.control.shutdown()


async def test_cursor_replay_and_generation_reset(rig):
    async with client(rig.app) as http:
        view = await ready_session(rig, http)
        sid = view["voice_session_id"]
        await asyncio.sleep(0.1)
        ring = list(rig.control.hub._streams[sid].ring)
        assert ring
        stream = await rig.control.subscribe(sid, voice_routes.encode_cursor(sid, 1, 0))
        first = await anext(stream)
        assert first["sequence"] == ring[0]["sequence"]
        await stream.aclose()
        other = await rig.control.subscribe(sid, voice_routes.encode_cursor(sid, 7, 3))
        reset = await anext(other)
        assert (
            reset["event"] == "voice.cursor_reset"
            and reset["payload"]["reason"] == "generation_changed"
        )
        await other.aclose()
        bad = await http.get(
            f"/voice/sessions/{sid}/events",
            params={"cursor": voice_routes.encode_cursor(str(uuid4()), 1, 1)},
        )
        assert bad.status_code == 409 and bad.json()["code"] == "cursor_mismatch"
        assert decode_cursor(voice_routes.encode_cursor(sid, 1, 2)) == (sid, 1, 2)
    await rig.control.shutdown()


async def test_missing_server_binary_is_runtime_unavailable(rig):
    rig.runtime._server_binary = "/nonexistent/livekit-server"
    async with client(rig.app) as http:
        created = (await http.post("/sessions/s1/voice", json={})).json()
        for _ in range(100):
            got = (
                await http.get(f"/voice/sessions/{created['voice_session_id']}")
            ).json()
            if got["state"] == "error":
                break
            await asyncio.sleep(0.02)
        assert got["state"] == "error" and got["retryable"] is True
    await rig.control.shutdown()


async def test_unknown_session_and_other_session_are_404(rig):
    async with client(rig.app) as http:
        assert (await http.post("/sessions/nope/voice", json={})).status_code == 404
        assert (await http.get(f"/voice/sessions/{uuid4()}")).status_code == 404
    await rig.control.shutdown()


async def test_worker_events_and_control_auth(rig):
    async with client(rig.app) as http:
        view = await ready_session(rig, http)
        sid = view["voice_session_id"]
        await http.post(
            f"/voice/sessions/{sid}/events",
            json={
                "event": "voice.state",
                "generation": 1,
                "turn_id": None,
                "payload": {
                    "state": "downloading",
                    "model": "parakeet",
                    "bytes_done": 5,
                    "bytes_total": 10,
                },
            },
        )
        stale = await http.post(
            f"/voice/sessions/{sid}/events",
            json={
                "event": "voice.state",
                "generation": 9,
                "payload": {"state": "listening"},
            },
        )
        assert stale.status_code == 409
        got = [
            e
            for e in rig.control.hub._streams[sid].ring
            if e["payload"].get("state") == "downloading_models"
        ]
        assert got[0]["payload"]["download"] == {
            "model": "parakeet",
            "bytes_done": 5,
            "bytes_total": 10,
        }
        assert await rig.runtime.speak(
            sid, 1, {"turn_id": "t", "spoken_text": "hi", "generation": 1}
        )
    await rig.control.shutdown()


def test_server_dies_with_engine_even_when_engine_is_killed(tmp_path):
    """The supervisor watchdog kills livekit-server when its parent pid vanishes."""
    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    pidfile = tmp_path / "pid"
    child = textwrap.dedent(f"""\
        import os
        open({str(pidfile)!r}, "w").write(str(os.getpid()))
        import time; time.sleep(60)""")
    script = tmp_path / "srv.py"
    script.write_text(child)
    sup = subprocess.Popen(
        [
            "/bin/sh",
            "-c",
            voice_livekit._SUPERVISE,
            "sh",
            str(parent.pid),
            sys.executable,
            str(script),
        ],
        start_new_session=True,
    )
    try:
        end = time.monotonic() + 5
        while not pidfile.exists() and time.monotonic() < end:
            time.sleep(0.05)
        srv_pid = int(pidfile.read_text())
        os.kill(srv_pid, 0)
        parent.send_signal(signal.SIGKILL)
        parent.wait()
        end = time.monotonic() + 6
        while time.monotonic() < end and not _gone(srv_pid):
            time.sleep(0.1)
        assert _gone(srv_pid)
    finally:
        try:
            os.killpg(sup.pid, signal.SIGKILL)
        except ProcessLookupError, PermissionError:
            pass
        sup.wait()
