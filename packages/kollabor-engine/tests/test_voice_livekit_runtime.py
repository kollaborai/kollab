"""Engine LiveKit voice runtime: fake livekit-server, fake worker, fake session."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import os
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from kollabor_engine import voice_livekit, voice_runtime_host
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

# Binds both of livekit-server's TCP ports, with its defaults when a flag is missing.
TWO_PORT_SERVER = """\
#!{py}
import socket, sys, time
def bind(flag, default):
    port = int(sys.argv[sys.argv.index(flag) + 1]) if flag in sys.argv else default
    s = socket.socket(); s.bind(("127.0.0.1", port)); s.listen(5)
    return s
http, ice = bind("--port", 7880), bind("--rtc.tcp_port", 7881)
while True: time.sleep(1)
"""

# RoomService.ListParticipants over the rooms in $FAKE_ROOM_PEOPLE ({room: [identity]}),
# each call logged to $FAKE_ROOM_LOG with the room asked for and the token's grants.
FAKE_ROOM_SERVER = """\
#!{py}
import base64, json, os, sys
from http.server import BaseHTTPRequestHandler, HTTPServer


class Rooms(BaseHTTPRequestHandler):
    def do_POST(self):
        room = json.loads(self.rfile.read(int(self.headers["Content-Length"])))["room"]
        claims = self.headers["Authorization"].split(".")[1]
        video = json.loads(base64.urlsafe_b64decode(claims + "=" * (-len(claims) % 4)))["video"]
        with open(os.environ["FAKE_ROOM_LOG"], "a") as log:
            log.write(json.dumps([self.path, room, video]) + "\\n")
        with open(os.environ["FAKE_ROOM_PEOPLE"]) as people:
            here = json.load(people).get(room, [])
        body = json.dumps(dict(participants=[dict(identity=who) for who in here])).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass


HTTPServer(("127.0.0.1", int(sys.argv[sys.argv.index("--port") + 1])), Rooms).serve_forever()
"""

FAKE_WORKER = """\
import json, os, signal, sys, time
cfg = json.loads(os.environ["KOLLAB_VOICE_WORKER_CONFIG"])
log = os.environ["FAKE_WORKER_LOG"]
with open(log + ".cfg", "w") as f: json.dump(cfg, f)
def stop(*_):
    with open(log, "a") as f: f.write("sigterm\\n")
    sys.exit(0)
signal.signal(signal.SIGTERM, stop)
print("WORKER_READY -", flush=True)
while True: time.sleep(0.1)
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
        self.cancels = []  # the voice turn each cancel was for
        self.revokes = []
        self.interrupted = []

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

    def cancel_turn(self, voice_turn_id=None):
        self.cancels.append(voice_turn_id)

    async def voice_admission(self, action, **params):
        self.revokes.append((action, params))
        return {"revoked": True}

    async def record_interrupted_reply(self, turn_id, text):
        # the daemon's answer: one interrupted reply per voice turn
        if any(turn == turn_id for turn, _ in self.interrupted):
            return {"recorded": False, "duplicate": True}
        self.interrupted.append((turn_id, text))
        return {"recorded": True}


@pytest.fixture
async def rig(tmp_path, monkeypatch):
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
    yield SimpleNamespace(
        control=control, runtime=runtime, session=session, app=app, log=log
    )
    # The fake server and workers run in their own process groups; a test that
    # fails before its own shutdown must not leave them running.
    await control.shutdown()


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
        {"type": "thinking"},
        {"type": "token", "text": "<display_text>Sure thing, **done**. All good.</display_text>"},
        {"type": "token", "text": "<spoken_text>Sure thing, done. "},
        {"type": "token", "text": "All good now."},
        {"type": "token", "text": "</spoken_text>"},
        {
            "type": "response",
            "text": (
                "<display_text>Sure thing, **done**. All good.</display_text>"
                "<spoken_text>Sure thing, done. All good now.</spoken_text>"
            ),
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
            f"/voice/sessions/{view['voice_session_id']}/turns", json=body
        )
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/x-ndjson")
        lines = [json.loads(ln) for ln in resp.text.splitlines()]
        assert lines == [
            {"text": "Sure thing, done."},
            {"text": "All good now."},
            {"done": True, "turn_id": body["turn_id"]},
        ]
        # duplicate turn = idempotent, no second send and no second reply
        dup = await http.post(
            f"/voice/sessions/{view['voice_session_id']}/turns", json=body
        )
        assert json.loads(dup.text)["duplicate"] is True and len(rig.session.sent) == 1
        message, request_id, _thread, ctx = rig.session.sent[0]
        assert (
            message == "hello there"
            and request_id == body["turn_id"]
            and ctx["source"] == "livekit_voice"
        )

        await wait_for(
            lambda: any(
                e["event"] == "voice.transcript"
                and e["payload"]["speaker"] == "assistant"
                and e["payload"]["final"]
                for e in events
            )
        )
        assert not any(e["event"] == "voice.metrics" for e in events)  # the worker's
        assistant = [
            e["payload"]
            for e in events
            if e["event"] == "voice.transcript"
            and e["payload"]["speaker"] == "assistant"
        ]
        finals = [p for p in assistant if p["final"]]
        partials = [p for p in assistant if not p["final"]]
        assert len(finals) == 1 and assistant[-1] is finals[0]
        assert finals[0]["spoken_text_chunk"] == "Sure thing, done. All good now."
        assert "**" in finals[0]["display_text_chunk"]
        # a non-final revision per sentence, growing; the final comes after them all
        assert [p["spoken_text_chunk"] for p in partials if p["spoken_text_chunk"]][
            :2
        ] == ["Sure thing, done.", "Sure thing, done. All good now."]
        assert [p["revision"] for p in partials] == sorted(p["revision"] for p in partials)
        seqs = [e["sequence"] for e in events if e["event"] != "voice.state" or e["sequence"]]
        assert seqs == sorted(seqs)
        # past admission the worker reports states from LiveKit's own; the model's
        # thinking event (late, maybe mid-speech) adds no second "thinking"
        states = [e["payload"].get("state") for e in events if e["event"] == "voice.state"]
        assert states.count("thinking") == 1 and "speaking" not in states

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
        # the worker stops on SIGTERM, nothing is posted to it
        await wait_for(lambda: rig.log.exists() and "sigterm" in rig.log.read_text())
        assert rig.log.read_text().count("sigterm") == 1
        # a stop revokes the generation's capabilities in the daemon too
        await wait_for(
            lambda: any(a == "revoke_generation" for a, _ in rig.session.revokes)
        )

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


async def test_a_worker_that_exits_by_itself_ends_its_session(rig):
    async with client(rig.app) as http:
        view = await ready_session(rig, http)
        sid = view["voice_session_id"]
        published = []
        orig = rig.control.hub.publish
        rig.control.hub.publish = lambda v, e, p, **k: published.append(
            (e, p)
        ) or orig(v, e, p, **k)
        (worker,) = rig.runtime._workers.values()
        os.kill(worker.process.pid, signal.SIGKILL)  # a crash: the engine never stopped it
        for _ in range(250):
            got = (await http.get(f"/voice/sessions/{sid}")).json()
            if got["state"] != "ready":
                break
            await asyncio.sleep(0.02)
        gone = {"state": "error", "detail": "The voice worker stopped.", "retryable": True}
        assert {k: got[k] for k in gone} == gone
        await wait_for(lambda: ("voice.state", gone) in published)
        assert not rig.runtime._workers
        # the next create starts a new session, and a stop of ours is not a crash
        fresh = await ready_session(rig, http)
        nid = fresh["voice_session_id"]
        assert nid != sid
        await http.request(
            "DELETE", f"/voice/sessions/{nid}", json={"generation": 1, "reason": "user"}
        )
        await wait_for(lambda: rig.control.host._sessions[nid].state == "stopped")
        await asyncio.sleep(0.1)
        assert rig.control.host._sessions[nid].state == "stopped"
    await rig.control.shutdown()


@pytest.fixture
def rooms(rig, tmp_path, monkeypatch):
    """The rig on a server that says who is in each room; here(room, *identities) sets it."""
    (tmp_path / "livekit-server").write_text(FAKE_ROOM_SERVER.format(py=sys.executable))
    people, calls = tmp_path / "people.json", tmp_path / "rooms.log"
    monkeypatch.setenv("FAKE_ROOM_PEOPLE", str(people))
    monkeypatch.setenv("FAKE_ROOM_LOG", str(calls))
    monkeypatch.setattr(voice_livekit, "PRESENCE_POLL_S", 0.05)
    monkeypatch.setattr(voice_livekit, "USER_GONE_GRACE_S", 0.4)
    monkeypatch.setattr(voice_runtime_host, "HANDOUT_GRACE_S", 0.3)

    def here(room, *identities):
        staged = tmp_path / "people.next"
        staged.write_text(json.dumps({room: list(identities)}))
        staged.replace(people)

    def asked():
        return [json.loads(line) for line in calls.read_text().splitlines()]

    here("none")
    return SimpleNamespace(here=here, asked=asked)


async def _state(http, sid):
    return (await http.get(f"/voice/sessions/{sid}")).json()


async def test_a_room_its_user_left_ends_once_they_stay_gone(rig, rooms, monkeypatch):
    """A closed or reloaded page sends no stop: the Engine ends the room after the grace."""
    for name in ("http_proxy", "HTTP_PROXY"):  # the server is asked directly, never through a proxy
        monkeypatch.setenv(name, "http://127.0.0.1:9")
    for name in ("no_proxy", "NO_PROXY"):
        monkeypatch.delenv(name, raising=False)
    async with client(rig.app) as http:
        view = await ready_session(rig, http)
        sid = view["voice_session_id"]
        room, user = f"kollab-{sid}-g1", f"user-engine-{sid[:8]}"
        rooms.here(room, f"worker-{sid}", user)
        await asyncio.sleep(0.8)  # twice the grace, with the user in the room
        assert (await _state(http, sid))["state"] == "ready"

        rooms.here(room, f"worker-{sid}")  # the page reloads
        await asyncio.sleep(0.2)
        again = (await http.post("/sessions/s1/voice", json={})).json()
        assert again["voice_session_id"] == sid  # its next start rejoins this room
        rooms.here(room, f"worker-{sid}", user)
        await asyncio.sleep(0.8)
        assert (await _state(http, sid))["state"] == "ready"

        rooms.here(room, f"worker-{sid}")  # the page closes
        await wait_for(lambda: rig.control.host._sessions[sid].state == "stopped")
        got = await _state(http, sid)
        assert (got["state"], got["generation"]) == ("stopped", 2)
        assert "sigterm" in rig.log.read_text() and not rig.runtime._workers
        asked = rooms.asked()
        assert {(path, name) for path, name, _ in asked} == {
            ("/twirp/livekit.RoomService/ListParticipants", room)
        }
        assert all(video == {"room": room, "roomAdmin": True} for *_, video in asked)
        fresh = (await http.post("/sessions/s1/voice", json={})).json()
        assert fresh["voice_session_id"] != sid
    await rig.control.shutdown()


async def test_a_room_nobody_joins_ends_and_one_the_server_cannot_list_stays(rig, rooms, tmp_path):
    async with client(rig.app) as http:
        view = await ready_session(rig, http)
        sid = view["voice_session_id"]
        await wait_for(lambda: rig.control.host._sessions[sid].state == "stopped")

        (tmp_path / "people.json").write_text("not json")  # every answer is an error
        view = await ready_session(rig, http)
        await asyncio.sleep(1.0)
        assert (await _state(http, view["voice_session_id"]))["state"] == "ready"
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


async def test_two_servers_on_one_machine_each_get_their_own_ice_port(tmp_path):
    # livekit-server's ICE/TCP default is one port for every server on the machine:
    # a second Engine, or LiveKit's own demo, made the next one exit at startup.
    server = tmp_path / "livekit-server"
    server.write_text(TWO_PORT_SERVER.format(py=sys.executable))
    server.chmod(0o755)
    runtimes = [
        LiveKitRuntime(
            engine_url=lambda: "http://127.0.0.1:1",
            engine_token=lambda: "tok",
            server_binary=str(server),
        )
        for _ in range(2)
    ]
    try:
        for runtime in runtimes:
            await runtime._ensure_server()
        assert len({runtimes[0].rtc_tcp_port, runtimes[1].rtc_tcp_port, 7881}) == 3
    finally:
        for runtime in runtimes:
            await runtime.shutdown()


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
    await rig.control.shutdown()


def test_server_dies_with_engine_even_when_engine_is_killed(tmp_path):
    """The supervisor watchdog kills livekit-server when its parent pid vanishes."""
    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    pidfile = tmp_path / "pid"
    # Like livekit-server, it outlives a SIGTERM: it drains rooms that only the
    # dead engine could end.
    child = textwrap.dedent(f"""\
        import os, signal
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
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


# Like livekit-server, it outlives a SIGTERM: it drains its rooms first.
DRAINING_SERVER = """\
#!{py}
import os, signal, socket, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
open({pidfile!r}, "w").write(str(os.getpid()))
port = int(sys.argv[sys.argv.index("--port") + 1])
s = socket.socket(); s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind(("127.0.0.1", port)); s.listen(5)
while True: time.sleep(1)
"""


async def test_a_clean_shutdown_kills_the_server_without_waiting_for_a_drain(tmp_path):
    server = tmp_path / "livekit-server"
    pidfile = tmp_path / "pid"
    server.write_text(DRAINING_SERVER.format(py=sys.executable, pidfile=str(pidfile)))
    server.chmod(0o755)
    runtime = LiveKitRuntime(
        engine_url=lambda: "http://127.0.0.1:1",
        engine_token=lambda: "tok",
        server_binary=str(server),
    )
    await runtime._ensure_server()
    srv_pid = int(pidfile.read_text())
    try:
        await runtime.shutdown()
        await wait_for(lambda: _gone(srv_pid), timeout=2.0)
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.kill(srv_pid, signal.SIGKILL)


def test_the_engine_waits_for_a_worker_longer_than_it_waits_for_its_models():
    import ast

    import kollabor_voice

    source = (Path(kollabor_voice.__file__).parent / "livekit_worker.py").read_text()
    (models_wait,) = [
        node.value.value
        for node in ast.parse(source).body
        if isinstance(node, ast.Assign)
        and getattr(node.targets[0], "id", None) == "READY_TIMEOUT_S"
    ]
    # a first-run download the worker still waits for must not be killed here
    assert voice_livekit.WORKER_READY_TIMEOUT_S > models_wait


# --- /turns: progressive speech, greeting, barge-in -------------------------------


def test_spoken_text_reads_an_open_channel_and_sentences_cut_on_whitespace():
    from kollabor_engine.voice_control import cut_sentences, spoken_so_far

    # split_response_channels(partial=True) says "" for this; the stream must not.
    assert spoken_so_far("<display_text>a</display_text><spoken_text>Hi there. Wh") == "Hi there. Wh"
    assert spoken_so_far("<spoken_text>Hi.</spo") == "Hi."  # closing tag still arriving
    assert spoken_so_far("<spoken_text>Hi.</spoken_text>tail") == "Hi."
    assert spoken_so_far("<display_text>only display so far") is None
    assert spoken_so_far("plain words <displ") == "plain words "  # no channel: all speech
    assert cut_sentences("One. Two! Thr", final=False) == (["One.", "Two!"], " Thr")
    assert cut_sentences("version 3.5 is out", final=False) == ([], "version 3.5 is out")
    assert cut_sentences("last words", final=True) == (["last words"], "")


async def test_latency_stage_names_map(rig):
    for stage in ("stt", "llm_first_token", "first_audio", "total_turn"):
        merged = rig.control._merge_latency("t1", {"stage": stage, "ms": 5})
    assert merged == {
        "stt_ms": 5,
        "llm_first_token_ms": 5,
        "first_audio_ms": 5,
        "total_turn_ms": 5,
    }
    await rig.control.shutdown()


async def test_turn_refusal_is_an_http_error_not_a_stream(rig):
    async with client(rig.app) as http:
        view = await ready_session(rig, http)
        body = transcript(view)
        body["generation"] = 9
        resp = await http.post(
            f"/voice/sessions/{view['voice_session_id']}/turns", json=body
        )
        assert resp.status_code == 409 and resp.json()["code"] == "stale_generation"
        assert rig.session.sent == []
    await rig.control.shutdown()


async def test_greeting_is_a_hidden_instruction_turn(rig):
    from kollabor_engine.voice_control import GREETING_INSTRUCTION

    rig.control.settings = None
    rig.session.script = [
        {"type": "response", "text": "Hey, what do you want to talk about?"},
        {"type": "turn_complete"},
    ]
    async with client(rig.app) as http:
        view = await ready_session(rig, http)
        body = {**transcript(view, text=""), "kind": "greeting"}
        resp = await http.post(
            f"/voice/sessions/{view['voice_session_id']}/turns", json=body
        )
        lines = [json.loads(ln) for ln in resp.text.splitlines()]
        assert lines[0] == {"text": "Hey, what do you want to talk about?"}
        assert lines[-1] == {"done": True, "turn_id": body["turn_id"]}
        message, _rid, _thread, ctx = rig.session.sent[0]
        assert message == GREETING_INSTRUCTION and ctx["voice_hidden"] is True
        ring = rig.control.hub._streams[view["voice_session_id"]].ring
        users = [
            e
            for e in ring
            if e["event"] == "voice.transcript" and e["payload"]["speaker"] == "user"
        ]
        assert users == []  # no user bubble for an instruction
    await rig.control.shutdown()


SHOWN = {"type": "message", "message_type": "assistant"}  # the daemon's display event


async def said(rig, http, view):
    resp = await http.post(
        f"/voice/sessions/{view['voice_session_id']}/turns", json=transcript(view)
    )
    lines = [json.loads(ln) for ln in resp.text.splitlines()]
    assert lines[-1]["done"] is True
    return [line["text"] for line in lines[:-1]]


async def test_a_reply_says_its_last_sentence_once_it_is_shown(rig):
    # What the daemon sends: its echo of the user, the reply's tokens, then the
    # reply's display message. A last sentence has no space after it to cut on.
    rig.session.script = [
        {"type": "message", "message_type": "user", "content": "hello there"},
        {"type": "token", "text": "Sure thing. "},
        {"type": "token", "text": "All good now."},
        {**SHOWN, "content": "Sure thing. All good now."},
        {"type": "turn_complete"},
    ]
    async with client(rig.app) as http:
        view = await ready_session(rig, http)
        assert await said(rig, http, view) == ["Sure thing.", "All good now."]
    await rig.control.shutdown()


async def test_a_turns_latency_outlives_its_reply_stream(rig):
    # The worker reports the last audio after the turn's reply stream ended: that
    # Total joins the turn's other stages, all of them the worker's own.
    rig.session.script = [
        {"type": "token", "text": "Done."},
        {**SHOWN, "content": "Done."},
        {"type": "turn_complete"},
    ]
    async with client(rig.app) as http:
        view = await ready_session(rig, http)
        body = transcript(view)
        rig.control._merge_latency(body["turn_id"], {"stage": "stt", "ms": 40})
        resp = await http.post(
            f"/voice/sessions/{view['voice_session_id']}/turns", json=body
        )
        assert json.loads(resp.text.splitlines()[-1])["done"] is True
        merged = rig.control._merge_latency(
            body["turn_id"], {"stage": "total_turn", "ms": 900}
        )
    assert merged == {
        "stt_ms": 40,
        "llm_first_token_ms": None,
        "first_audio_ms": None,
        "total_turn_ms": 900,
    }
    await rig.control.shutdown()


async def test_another_turns_events_are_never_said_as_the_voice_turns(rig):
    # The voice turn waits in the daemon's queue behind a hub wake or a typed turn,
    # whose events (no request id) reach every subscriber first.
    async with client(rig.app) as http:
        view = await ready_session(rig, http)
        snapshot = voice_snapshot(view, "what is on my calendar")
        turn = await rig.control.open_turn(view["voice_session_id"], snapshot)
        queue = rig.session.queues[0]
        for event in (
            {"type": "token", "text": "Counting: one. Two. "},
            {**SHOWN, "content": "Counting: one. Two."},
            {"type": "turn_complete"},
        ):
            queue.put_nowait(event)
        said = asyncio.ensure_future(anext(turn))
        await asyncio.sleep(0.2)
        assert not said.done()  # nothing said, and the stream is still open
        for event in ({**SHOWN, "content": "You have two meetings."}, {"type": "turn_complete"}):
            queue.put_nowait({**event, "request_id": snapshot.turn_id})
        assert await asyncio.wait_for(said, 3) == {"text": "You have two meetings."}
        assert (await anext(turn))["done"] is True
    await rig.control.shutdown()


async def test_a_reply_shown_without_its_turn_id_still_says_its_last_sentence(rig):
    # An ACP agent: its tokens and turn_complete carry the turn id, its shown reply does not.
    async with client(rig.app) as http:
        view = await ready_session(rig, http)
        snapshot = voice_snapshot(view, "is it green")
        turn = await rig.control.open_turn(view["voice_session_id"], snapshot)
        queue = rig.session.queues[0]
        stamp = {"request_id": snapshot.turn_id}
        queue.put_nowait({"type": "token", "text": "Checking now. It is green", **stamp})
        queue.put_nowait({**SHOWN, "content": "Checking now. It is green"})
        queue.put_nowait({"type": "turn_complete", **stamp})
        lines = [line async for line in turn]
    assert [line["text"] for line in lines if "text" in line] == ["Checking now.", "It is green"]
    assert lines[-1]["done"] is True
    await rig.control.shutdown()


async def test_a_reply_with_xml_tool_tags_is_said_once(rig):
    # Its "response" (the clean text) comes before its display message.
    rig.session.script = [
        {"type": "token", "text": "Checking now."},
        {"type": "response", "text": "Checking now."},
        {**SHOWN, "content": "Checking now."},
        {"type": "token", "text": "It is green."},
        {**SHOWN, "content": "It is green."},
        {"type": "turn_complete"},
    ]
    async with client(rig.app) as http:
        view = await ready_session(rig, http)
        assert await said(rig, http, view) == ["Checking now.", "It is green."]
    await rig.control.shutdown()


async def test_a_reply_after_a_round_of_hub_tags_alone_is_still_said(rig):
    # Such a round sends "response" with no text and nothing to display, then
    # runs its tags; the next reply comes whole, from a provider that does not
    # stream, as its display message alone.
    rig.session.script = [
        {"type": "response", "text": ""},
        {"type": "tool_start", "tool_id": "h1", "tool_name": "hub_msg"},
        {"type": "tool_result", "tool_id": "h1", "tool_name": "hub_msg", "success": True},
        {**SHOWN, "content": "Sent it to lapis."},
        {"type": "turn_complete"},
    ]
    async with client(rig.app) as http:
        view = await ready_session(rig, http)
        assert await said(rig, http, view) == ["Sent it to lapis."]
    await rig.control.shutdown()


async def test_words_said_over_the_agent_ride_on_the_turn_context(rig):
    rig.control.settings = None
    async with client(rig.app) as http:
        view = await ready_session(rig, http)
        rig.session.script = [{"type": "turn_complete"}]
        url = f"/voice/sessions/{view['voice_session_id']}/turns"
        await http.post(url, json={**transcript(view), "talk_over": True})
        await http.post(url, json=transcript(view, text="and the tests"))
        talked_over, clean = (sent[3] for sent in rig.session.sent)
        assert talked_over["voice_talk_over"] is True  # its yes never counts
        assert "voice_talk_over" not in clean
    await rig.control.shutdown()


async def test_voice_settings_ride_on_the_turn_context(rig):
    async with client(rig.app) as http:
        view = await ready_session(rig, http)
        rig.control.settings = SimpleNamespace(
            overlay=lambda agent: f"overlay for {agent}", reasoning=lambda: "low"
        )
        rig.session.script = [{"type": "turn_complete"}]
        await http.post(
            f"/voice/sessions/{view['voice_session_id']}/turns", json=transcript(view)
        )
        ctx = rig.session.sent[0][3]
        assert ctx["voice_prompt_overlay"] == "overlay for lapis"
        assert ctx["voice_reasoning"] == "low" and "voice_hidden" not in ctx
        # settings can add keys, never replace the identity fields
        assert ctx["source"] == "livekit_voice"
    rig.control.settings = None
    await rig.control.shutdown()


@pytest.mark.parametrize("how", ["aclose", "cancel"])
async def test_disconnect_before_done_is_a_barge_in(rig, how):
    from kollabor_agent.voice_admission import validate_voice_admission_context

    rig.session.script = [{"type": "token", "text": "Sure thing. And then I"}]  # never finishes
    async with client(rig.app) as http:
        view = await ready_session(rig, http)
        sid = view["voice_session_id"]
        snapshot = voice_snapshot(view, "tell me a long story")
        turn = await rig.control.open_turn(sid, snapshot)
        turn_queue, view_turn = rig.session.queues[0], snapshot.turn_id
        first = asyncio.Event()

        async def consume():
            async for line in turn:
                if "text" in line:
                    first.set()

        if how == "aclose":
            assert await anext(turn) == {"text": "Sure thing."}
            await turn.aclose()
        else:
            task = asyncio.create_task(consume())
            await asyncio.wait_for(first.wait(), 3)
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        ring = rig.control.hub._streams[sid].ring
        await wait_for(
            lambda: any(e["payload"].get("barge_in") for e in ring)
        )
        barge = [e for e in ring if e["payload"].get("barge_in")]
        assert len(barge) == 1 and barge[0]["payload"]["state"] == "listening"
        assert rig.session.cancels == [view_turn]  # the in-flight turn, once, by its id
        assert not rig.session.queues  # nobody listens to the daemon any more
        # the capability is gone here and in the daemon
        assert validate_voice_admission_context(rig.session.sent[0][3]) is False
        await wait_for(lambda: any(a == "revoke_turn" for a, _ in rig.session.revokes))
        # whatever the cancelled turn says later is published nowhere
        seen = len(ring)
        turn_queue.put_nowait(
            {"type": "response", "text": "late reply", "request_id": view_turn}
        )
        turn_queue.put_nowait({"type": "turn_complete", "request_id": view_turn})
        await asyncio.sleep(0.2)
        assert len(rig.control.hub._streams[sid].ring) == seen
    await rig.control.shutdown()


async def test_a_turn_the_daemon_refuses_answers_at_once(rig):
    """A refused submission sends no turn_complete: no stream is left to wait one out."""
    from kollabor_engine.voice_runtime_host import RuntimeHostError

    async def refuse(message, *, request_id, thread_id, origin_context):
        return {"accepted": False, "reason": "invalid voice admission capability"}

    rig.session.send_message = refuse
    async with client(rig.app) as http:
        view = await ready_session(rig, http)
        with pytest.raises(RuntimeHostError) as refused:
            await asyncio.wait_for(
                rig.control.open_turn(view["voice_session_id"], voice_snapshot(view, "hi")), 1
            )
        assert refused.value.code == "session_busy"
        assert not rig.session.queues  # nothing still subscribed to the daemon
    await rig.control.shutdown()


async def test_a_fault_in_the_reply_stream_stops_the_daemon_turn(rig):
    from kollabor_agent.voice_admission import validate_voice_admission_context

    rig.session.script = [{"type": "token", "text": "Sure thing. And then I"}]

    def broken(sentence):
        raise RuntimeError("speech policy fault")

    rig.control.policy.sentence = broken
    async with client(rig.app) as http:
        view = await ready_session(rig, http)
        snapshot = voice_snapshot(view, "hi")
        turn = await rig.control.open_turn(view["voice_session_id"], snapshot)
        with pytest.raises(RuntimeError, match="speech policy fault"):
            await anext(turn)
        # it does not run on with nobody listening; a later typed turn is not its to stop
        assert rig.session.cancels == [snapshot.turn_id]
        assert validate_voice_admission_context(rig.session.sent[0][3]) is False
        ring = rig.control.hub._streams[view["voice_session_id"]].ring
        assert not any(e["payload"].get("barge_in") for e in ring)  # nobody talked over it
    await rig.control.shutdown()


async def test_disconnect_after_done_is_not_a_barge_in(rig):
    rig.session.script = [{"type": "response", "text": "Done."}, {"type": "turn_complete"}]
    async with client(rig.app) as http:
        view = await ready_session(rig, http)
        turn = await rig.control.open_turn(view["voice_session_id"], voice_snapshot(view, "hi"))
        seen = [line async for line in turn]
        await turn.aclose()
        assert seen[-1]["done"] is True
        assert rig.session.cancels == []
        ring = rig.control.hub._streams[view["voice_session_id"]].ring
        assert not any(e["payload"].get("barge_in") for e in ring)
    await rig.control.shutdown()


async def test_a_talk_over_records_the_played_text_once(rig):
    async with client(rig.app) as http:
        view = await ready_session(rig, http)
        turn = str(uuid4())
        url = f"/voice/sessions/{view['voice_session_id']}/turns/{turn}/interrupted"
        first = await http.post(url, json={"text": "  So the build  "})
        assert first.status_code == 200 and first.json() == {"recorded": True}
        again = await http.post(url, json={"text": "So the build"})
        assert again.json() == {"recorded": False, "duplicate": True}
        assert rig.session.interrupted == [(turn, "So the build")]  # trimmed, once
    await rig.control.shutdown()


async def test_a_talk_over_before_any_audio_reaches_the_daemon(rig):
    # Nothing was played: the daemon takes the unheard reply away.
    async with client(rig.app) as http:
        view = await ready_session(rig, http)
        turn = str(uuid4())
        url = f"/voice/sessions/{view['voice_session_id']}/turns/{turn}/interrupted"
        blank = await http.post(url, json={"text": " \n "})
        assert blank.status_code == 200 and blank.json() == {"recorded": True}
        assert rig.session.interrupted == [(turn, "")]
    await rig.control.shutdown()


async def test_the_interrupted_route_checks_session_turn_and_size(rig):
    from kollabor_engine.voice_session_router import MAX_FINAL_TRANSCRIPT_BYTES

    async with client(rig.app) as http:
        view = await ready_session(rig, http)
        sid, text = view["voice_session_id"], {"text": "So the build"}
        assert (
            await http.post(f"/voice/sessions/{uuid4()}/turns/{uuid4()}/interrupted", json=text)
        ).status_code == 404
        assert (
            await http.post(f"/voice/sessions/{sid}/turns/not-a-turn/interrupted", json=text)
        ).status_code == 422
        assert (
            await http.post(f"/voice/sessions/{sid}/turns/{uuid4()}/interrupted", json={})
        ).status_code == 422
        big = {"text": "x" * (MAX_FINAL_TRANSCRIPT_BYTES + 1)}
        refused = await http.post(f"/voice/sessions/{sid}/turns/{uuid4()}/interrupted", json=big)
        assert refused.status_code == 413 and refused.json()["code"] == "transcript_too_large"
        assert rig.session.interrupted == []
    await rig.control.shutdown()


def voice_snapshot(view, text):
    from kollabor_engine.voice_types import VoiceTranscriptSnapshot

    return VoiceTranscriptSnapshot(**transcript(view, text=text))


def test_child_output_goes_to_the_log_dir_and_starts_over_when_big(tmp_path):
    runtime = LiveKitRuntime(engine_url=lambda: "", engine_token=lambda: "", log_dir=tmp_path / "logs")
    with runtime._log("livekit-worker.log") as log:
        log.write(b"worker died: boom\n")
    path = tmp_path / "logs" / "livekit-worker.log"
    assert path.read_bytes() == b"worker died: boom\n"
    path.write_bytes(b"x" * 5_000_001)
    with runtime._log("livekit-worker.log") as log:
        log.write(b"fresh\n")
    assert path.read_bytes() == b"fresh\n"
    # no log dir (tests, embedders): output goes nowhere, as before
    with LiveKitRuntime(engine_url=lambda: "", engine_token=lambda: "")._log("x") as log:
        assert log == asyncio.subprocess.DEVNULL
