"""`--web-ui-hosts` must reach the spawned web UI exactly as passed (#674).

An explicit flag value always overrides the shell's KOLLAB_WEBUI_HOSTS --
including an explicitly EMPTY one, which means "localhost only, even though
my shell exports hosts". The launcher used to treat `if hosts:` the same as
absent, so `--web-ui-hosts ""` silently inherited the environment value.
"""

import asyncio
import http.server
import json
import socket
import subprocess
import threading

import kollabor.cli as cli


class _EngineStub(http.server.BaseHTTPRequestHandler):
    """Answers /health and /version so the launcher considers the engine up."""

    def do_GET(self):
        body = json.dumps({"version": "test"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class _FakeProc:
    def poll(self):
        return 0  # already exited: _terminate_child returns without signaling

    def wait(self, *args, **kwargs):
        return 0


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _run_launcher(monkeypatch, hosts_value, env_hosts) -> dict:
    """Runs _handle_cli_web_ui against stub children; returns the web UI env."""
    server = http.server.HTTPServer(("127.0.0.1", 0), _EngineStub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    engine_port = server.server_address[1]
    webui_port = _free_port()
    try:
        monkeypatch.setenv("KOLLAB_WEBUI_PORT", str(webui_port))
        if env_hosts is None:
            monkeypatch.delenv("KOLLAB_WEBUI_HOSTS", raising=False)
        else:
            monkeypatch.setenv("KOLLAB_WEBUI_HOSTS", env_hosts)

        spawned = []
        monkeypatch.setattr(
            subprocess, "Popen", lambda *a, **k: (spawned.append(k), _FakeProc())[1]
        )
        monkeypatch.setattr(
            cli, "_pick_engine_port", lambda *a, **k: (engine_port, False)
        )
        asyncio.run(cli._handle_cli_web_ui(restart=False, hosts=hosts_value))

        webui_calls = [kwargs for kwargs in spawned if "env" in kwargs]
        assert len(webui_calls) == 1, f"expected exactly one env-bearing spawn: {spawned}"
        return webui_calls[0]["env"]
    finally:
        server.shutdown()
        server.server_close()


def test_explicit_empty_hosts_overrides_shell_env(monkeypatch):
    env = _run_launcher(monkeypatch, hosts_value="", env_hosts="10.0.0.7")
    assert env["KOLLAB_WEBUI_HOSTS"] == ""


def test_flag_value_reaches_webui_env(monkeypatch):
    env = _run_launcher(monkeypatch, hosts_value="192.168.1.5", env_hosts="10.0.0.7")
    assert env["KOLLAB_WEBUI_HOSTS"] == "192.168.1.5"


def test_absent_flag_leaves_shell_env_inherited(monkeypatch):
    env = _run_launcher(monkeypatch, hosts_value=None, env_hosts="10.0.0.7")
    assert env["KOLLAB_WEBUI_HOSTS"] == "10.0.0.7"
