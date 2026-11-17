"""`kollab --web-ui` starts its engine again when the engine stops (#1123).

The launcher waited only on the web UI process. When the engine died (another
program's dev script sent SIGTERM to everything on its port), the web UI kept
serving a page that could not load anything until the user restarted Kollab.
"""

import asyncio
import http.server
import json
import signal
import socket
import subprocess
import sys
import threading
import time

import pytest

import kollabor.cli as cli


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class FakeEngine:
    """A stand-in engine process: answers /health and /version on its port until it 'dies'."""

    def __init__(self, port):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = json.dumps({"version": "test"}).encode() if self.path == "/version" else b"ok"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        http.server.HTTPServer.allow_reuse_address = True
        self.server = http.server.HTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.code = None
        self.pid = 0

    def die(self):
        if self.code is None:
            self.server.shutdown()
            self.server.server_close()
            self.code = -15

    def poll(self):
        return self.code

    def terminate(self):
        self.die()

    kill = terminate

    def wait(self, timeout=None):
        return self.code


class FakeWebUI:
    """A stand-in web UI process that runs until the test lets it exit."""

    def __init__(self):
        self.done = threading.Event()
        self.pid = 0

    def poll(self):
        return 0 if self.done.is_set() else None

    def wait(self, timeout=None):
        self.done.wait(timeout)
        return self.poll()

    def terminate(self):
        self.done.set()

    kill = terminate


@pytest.fixture
def stack(monkeypatch):
    port = free_port()
    engines, webuis = [], []

    def popen(cmd, *args, **kwargs):
        if any("kollabor_engine" in str(part) for part in cmd):
            engines.append(FakeEngine(port))
            return engines[-1]
        webuis.append(FakeWebUI())
        return webuis[-1]

    monkeypatch.setattr(subprocess, "Popen", popen)
    # The launcher runs on a test thread here; only the main thread may set signal handlers.
    monkeypatch.setattr(signal, "signal", lambda *a, **k: None)
    monkeypatch.setattr(cli, "_pick_engine_port", lambda *a, **k: (port, False))
    monkeypatch.setattr(cli, "_adopt_login_path", lambda: None)
    monkeypatch.setenv("KOLLAB_WEBUI_PORT", str(free_port()))
    monkeypatch.setattr(cli, "ENGINE_DOWN_GRACE", 0.3, raising=False)
    monkeypatch.setattr(cli, "WEBUI_WATCH_INTERVAL", 0.05, raising=False)
    yield engines, webuis
    for engine in engines:
        engine.die()


def run_launcher():
    t = threading.Thread(target=lambda: asyncio.run(cli._handle_cli_web_ui()), daemon=True)
    t.start()
    return t


def wait_for(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_a_dead_engine_is_started_again_on_the_same_port(stack, capsys):
    engines, webuis = stack
    launcher = run_launcher()
    assert wait_for(lambda: webuis), "the web UI never started"

    engines[0].die()  # what the other program's SIGTERM did on 2026-11-14
    restarted = wait_for(lambda: len(engines) == 2)

    webuis[0].done.set()
    launcher.join(timeout=10)
    assert restarted, "the launcher left the web UI without an engine"
    assert engines[1].poll() is not None  # the launcher stops the engine it restarted when it exits
    assert "engine" in capsys.readouterr().out.lower()


def test_no_plugins_is_forwarded_to_the_engine(stack, monkeypatch):
    engines, webuis = stack
    commands = []
    original_popen = subprocess.Popen

    def capture_engine_command(cmd, *args, **kwargs):
        if any("kollabor_engine" in str(part) for part in cmd):
            commands.append(cmd)
        return original_popen(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", capture_engine_command)
    monkeypatch.setattr(sys, "argv", ["kollab", "--web-ui", "--no-plugins"])
    launcher = run_launcher()
    assert wait_for(lambda: webuis), "the web UI never started"
    webuis[0].done.set()
    launcher.join(timeout=10)
    assert len(engines) == 1
    assert commands and "--no-plugins" in commands[0]


def test_an_engine_that_is_alive_but_slow_is_not_touched(stack, monkeypatch):
    engines, webuis = stack
    launcher = run_launcher()
    assert wait_for(lambda: webuis)

    # The process is still running; only its port stops answering (a busy event loop).
    engines[0].server.shutdown()
    engines[0].server.server_close()
    time.sleep(1.0)

    webuis[0].done.set()
    launcher.join(timeout=10)
    assert len(engines) == 1


def test_restarts_stop_after_the_cap():
    starts, now = [], [0.0]

    class Dead:
        def poll(self):
            return 1

    keeper = cli._EngineKeeper(lambda: starts.append(1) or Dead(), lambda: False, Dead(),
                               grace=1.0, limit=3, window=60.0, clock=lambda: now[0])
    notes = []
    for _ in range(40):
        now[0] += 0.5
        notes.append(keeper.check())
    assert len(starts) == 3
    assert any(n and "not restarting" in n for n in notes)

    now[0] += 61  # the window has passed: it may try again, once per grace period while the engine stays down
    for _ in range(2):
        now[0] += 0.5
        keeper.check()
    assert len(starts) == 4


def test_an_engine_another_program_put_on_the_port_is_left_alone():
    starts = []

    class Dead:
        def poll(self):
            return 1

    keeper = cli._EngineKeeper(lambda: starts.append(1), lambda: True, Dead(), grace=0.0, clock=time.monotonic)
    for _ in range(5):
        keeper.check()
    assert starts == []
