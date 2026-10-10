"""`kollab --web-ui` with the UI already up reuses it instead of crashing on its port.

The launcher reused the running engine but always started a second UI, whose
bind to 8080 died with "Address already in use" and a traceback.
"""

import asyncio
import http.server
import json
import socket
import subprocess
import sys
import threading
import time

import psutil
import pytest

import kollabor.cli as cli


@pytest.fixture
def listener():
    """`serve(title)` starts a dummy server answering /openapi.json with that app title (None: 404)."""
    servers = []

    def serve(title):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                if title is None:
                    self.send_error(404)
                    return
                body = json.dumps({"info": {"title": title}}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return server.server_address[1]

    yield serve
    for server in servers:
        server.shutdown()
        server.server_close()


@pytest.fixture
def launch(monkeypatch):
    """Runs the launcher on a port; starting a process or picking an engine fails the test."""
    monkeypatch.setattr(
        subprocess, "Popen", lambda *a, **k: pytest.fail("started a process")
    )
    monkeypatch.setattr(
        cli, "_pick_engine_port", lambda *a, **k: pytest.fail("picked an engine")
    )

    def run(port):
        monkeypatch.setenv("KOLLAB_WEBUI_PORT", str(port))
        asyncio.run(cli._handle_cli_web_ui())

    return run


def test_a_running_web_ui_is_reused(listener, launch, capsys):
    port = listener("Kollab WebUI")

    launch(port)

    out = capsys.readouterr().out
    assert "already running" in out
    assert f"http://127.0.0.1:{port}" in out


def test_another_program_on_the_port_is_a_clean_error(listener, launch, capsys):
    port = listener(None)

    with pytest.raises(SystemExit) as exc:
        launch(port)

    assert exc.value.code == 1
    assert str(port) in capsys.readouterr().err


# `kollab --web-ui --restart` stops the running UI through real processes.
# A stand-in UI: its command line names kollabor_webui, as the real one does.
_UI = (
    "import socket, time  # kollabor_webui\n"
    "s = socket.socket(); s.bind(('127.0.0.1', {port})); s.listen(); time.sleep(60)\n"
)
# A stand-in `kollab --web-ui`: on SIGTERM it stops its UI, as the real one
# does, and leaves a mark (psutil reaps a test's children, so no exit code).
_LAUNCHER = (
    "import pathlib, signal, subprocess, sys\n"
    "ui = subprocess.Popen([sys.executable, '-c', {ui!r}])\n"
    "def stop(*_):\n"
    "    raise KeyboardInterrupt\n"
    "signal.signal(signal.SIGTERM, stop)\n"
    "try:\n"
    "    ui.wait()\n"
    "except KeyboardInterrupt:\n"
    "    ui.terminate(); ui.wait(); pathlib.Path({mark!r}).write_text('term')\n"
)


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_listening(port):
    for _ in range(100):
        with socket.socket() as probe:
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.05)
    pytest.fail("stand-in UI never listened")


def test_restart_stops_the_launcher_that_owns_the_ui(tmp_path):
    port, mark = _free_port(), tmp_path / "launcher-stopped"
    code = _LAUNCHER.format(ui=_UI.format(port=port), mark=str(mark))
    launcher = subprocess.Popen([sys.executable, "-c", code, "--web-ui"])
    try:
        _wait_listening(port)
        assert cli._stop_webui(port, timeout=10)
        # The launcher got the SIGTERM, so its own cleanup stopped the UI.
        assert mark.read_text() == "term"
        assert not psutil.pid_exists(launcher.pid)
    finally:
        launcher.kill()


def test_restart_stops_a_ui_started_by_hand():
    port = _free_port()
    ui = subprocess.Popen([sys.executable, "-c", _UI.format(port=port)])
    try:
        _wait_listening(port)
        assert cli._stop_webui(port, timeout=10)
        assert not psutil.pid_exists(ui.pid)
    finally:
        ui.kill()
