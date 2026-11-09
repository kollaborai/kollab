"""The terminal WebSocket: the token on the socket, a shell in the folder, reattach, the session cap, the environment.

Commands print their marker with a quoted split (``12''3`` shows as ``123``), so the echo of the typed line can never
satisfy a check; only running the command does.
"""

import contextlib
import re
import time

import kollabor_engine.auth as engine_auth  # type: ignore[import-not-found]
import pytest
from fastapi import FastAPI  # type: ignore[import-not-found]
from fastapi.testclient import TestClient  # type: ignore[import-not-found]
from kollabor_engine.routes import code_terminal  # type: ignore[import-not-found]
from starlette.websockets import WebSocketDisconnect  # type: ignore[import-not-found]

TOKEN = "good-token"
URL = "/code/terminal"


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(code_terminal.router)
    return app


@pytest.fixture
def client(monkeypatch, tmp_path):
    """Real token checks (the pytest bypass is off) against a known token; an app holding only this router."""
    monkeypatch.delenv("KOLLAB_ENGINE_BYPASS_AUTH", raising=False)
    monkeypatch.setattr(engine_auth, "_current_token", TOKEN)
    monkeypatch.setattr(engine_auth, "_token_file", lambda: tmp_path / "engine.token")
    (tmp_path / ".zshrc").write_text("")  # HOME is tmp_path (conftest); an empty rc skips zsh's first-run wizard
    with TestClient(_app()) as test_client:  # one portal per test: a shell must outlive each socket
        yield test_client
        test_client.portal.call(code_terminal.shutdown_terminals)


def _auth(ws, token: str = TOKEN) -> None:
    ws.send_json({"type": "auth", "token": token})


def _open(ws, workspace: str, **extra) -> None:
    ws.send_json({"type": "open", "workspace": workspace, "cols": 100, "rows": 30, **extra})


def _output_until(ws, marker: str) -> str:
    """Collect output until ``marker`` shows up; any message that is not output fails the test."""
    seen = ""
    for _ in range(1000):
        message = ws.receive_json()
        if message["type"] != "output":
            pytest.fail(f"expected output, got: {message}")
        seen += message["data"]
        if marker in seen:
            return seen
    pytest.fail(f"{marker!r} never showed up")


def test_a_wrong_token_closes_4401(client):
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(URL) as ws:
            _auth(ws, "not-the-token")
            ws.receive_json()
    assert closed.value.code == 4401


def test_a_first_message_that_is_not_auth_closes_4401(client):
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(URL) as ws:
            ws.send_text("not json")
            ws.receive_json()
    assert closed.value.code == 4401


def test_no_auth_message_in_time_closes_4401(client, monkeypatch):
    monkeypatch.setattr(code_terminal, "AUTH_TIMEOUT_SECONDS", 0.2)
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(URL) as ws:
            ws.receive_json()  # nothing is sent; the server gives up first
    assert closed.value.code == 4401


def test_open_answers_ready_first_then_runs_the_shell_in_the_folder(client, tmp_path):
    with client.websocket_connect(URL) as ws:
        _auth(ws)
        _open(ws, str(tmp_path))
        ready = ws.receive_json()
        assert ready["type"] == "ready" and ready["sessionId"]
        ws.send_json({"type": "input", "data": "pwd; echo kollab-marker-12''3\n"})
        output = _output_until(ws, "kollab-marker-123")
    assert str(tmp_path.resolve()) in output


def test_a_reattach_replays_the_output_and_the_shell_keeps_running(client, tmp_path):
    with client.websocket_connect(URL) as ws:
        _auth(ws)
        _open(ws, str(tmp_path))
        session_id = ws.receive_json()["sessionId"]
        ws.send_json({"type": "input", "data": "echo kollab-marker-12''6\n"})
        _output_until(ws, "kollab-marker-126")
    # the first socket is gone; the shell is not
    with client.websocket_connect(URL) as again:
        _auth(again)
        again.send_json({"type": "open", "sessionId": session_id})
        assert again.receive_json() == {"type": "ready", "sessionId": session_id}
        assert "kollab-marker-126" in _output_until(again, "kollab-marker-126")
        again.send_json({"type": "input", "data": "echo kollab-after-''7\n"})
        _output_until(again, "kollab-after-7")


def test_the_ninth_open_is_refused_with_an_error(client, tmp_path):
    with contextlib.ExitStack() as stack:
        for _ in range(code_terminal.MAX_SESSIONS):
            ws = stack.enter_context(client.websocket_connect(URL))
            _auth(ws)
            _open(ws, str(tmp_path))
            assert ws.receive_json()["type"] == "ready"
        ninth = stack.enter_context(client.websocket_connect(URL))
        _auth(ninth)
        _open(ninth, str(tmp_path))
        refused = ninth.receive_json()
    assert refused["type"] == "error"
    assert "limit" in refused["message"]


def test_a_folder_the_engine_refuses_answers_an_error(client, tmp_path):
    with client.websocket_connect(URL) as ws:
        _auth(ws)
        _open(ws, "/etc")
        outside = ws.receive_json()
        _open(ws, str(tmp_path / "missing"))
        missing = ws.receive_json()
    assert outside["type"] == "error" and "outside" in outside["message"]
    assert missing["type"] == "error" and "does not exist" in missing["message"]


def test_the_shell_gets_no_kollab_or_secret_variables(client, tmp_path, monkeypatch):
    monkeypatch.setenv("KOLLAB_X", "1")
    monkeypatch.setenv("MY_TOKEN", "2")
    with client.websocket_connect(URL) as ws:
        _auth(ws)
        _open(ws, str(tmp_path))
        ws.receive_json()
        ws.send_json({"type": "input", "data": "env; echo kollab-end-$((40+2))\n"})
        output = _output_until(ws, "kollab-end-42")
    assert "TERM=xterm-256color" in output  # the environment was printed
    assert "KOLLAB_" not in output
    assert "MY_TOKEN" not in output


def test_shell_env_drops_kollab_and_secret_names(monkeypatch):
    monkeypatch.setenv("KOLLAB_X", "1")
    monkeypatch.setenv("MY_TOKEN", "2")
    env = code_terminal._shell_env()
    assert "KOLLAB_X" not in env and "MY_TOKEN" not in env
    assert env["TERM"] == "xterm-256color"


def test_the_shell_sits_on_a_real_terminal_and_leads_its_process_group(client, tmp_path):
    """The shell's stdio is a PTY device, and the shell is its own process group leader (job control needs both)."""
    command = "echo kollab-tty-$$-$(ps -o pgid= -p $$ | tr -d ' ')-$(tty)-kollab-end-''$((6*7))\n"
    with client.websocket_connect(URL) as ws:
        _auth(ws)
        _open(ws, str(tmp_path))
        ws.receive_json()
        ws.send_json({"type": "input", "data": command})
        output = _output_until(ws, "kollab-end-42")
    found = re.search(r"kollab-tty-(\d+)-(\d+)-(/dev/ttys\d+|/dev/pts/\d+)-kollab-end-42", output)
    assert found, output
    shell_pid, process_group, _device = found.groups()
    assert shell_pid == process_group


def test_ctrl_c_interrupts_a_running_command_and_the_shell_prompts_again(client, tmp_path):
    with client.websocket_connect(URL) as ws:
        _auth(ws)
        _open(ws, str(tmp_path))
        ws.receive_json()
        started = time.monotonic()
        ws.send_json({"type": "input", "data": "echo kollab-sleeping-''$((2*3)); sleep 30\n"})
        _output_until(ws, "kollab-sleeping-6")
        time.sleep(0.5)  # let sleep take the foreground; a Ctrl-C that lands before that would reach the shell
        ws.send_json({"type": "input", "data": "\x03"})
        ws.send_json({"type": "input", "data": "echo kollab-after-''$((5*5))\n"})
        _output_until(ws, "kollab-after-25")
    assert time.monotonic() - started < 20  # a sleep that ignored Ctrl-C would hold this test for its 30 seconds
