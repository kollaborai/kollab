"""The code editor routes (routes/code.py): files, git and a terminal, passed on to the session's daemon (#987).

The daemon is a script here: it records each ``code_call`` and answers from a table. What the daemon does with the
calls is covered in tests/unit/code_service/ at the repo root.
"""

import asyncio
from types import SimpleNamespace

import kollabor_engine.auth as engine_auth  # type: ignore[import-not-found]
import pytest
from fastapi import FastAPI  # type: ignore[import-not-found]
from fastapi.testclient import TestClient  # type: ignore[import-not-found]
from kollabor_engine.routes import code  # type: ignore[import-not-found]
from starlette.websockets import WebSocketDisconnect  # type: ignore[import-not-found]

TOKEN = "good-token"
SID = "agent@device"
OK = {"status": 200, "body": {"success": True, "data": {"hello": "world"}}}


class Daemon:
    """The state service of a session: a ``code_call`` that records what it was asked and answers from a script."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.answers: dict[str, dict] = {}
        self.fail: Exception | None = None
        self.output: list[dict] = []

    async def code_call(self, op: str, params: dict) -> dict:
        self.calls.append((op, params))
        if self.fail is not None and op != "terminal.detach":
            raise self.fail
        if op == "terminal.message":
            return self._message(params)
        if op == "terminal.poll":
            return await self._poll(params)
        if op == "terminal.detach":
            return {"status": 200, "body": {"success": True, "data": {}}}
        return self.answers.get(op, OK)

    def _message(self, params: dict) -> dict:
        import json

        message = json.loads(params["raw"])
        if message["type"] == "open":
            reply = [{"type": "ready", "sessionId": "shell-1"}]
        else:
            reply = []
            if message["type"] == "input":
                self.output.append({"type": "output", "data": message["data"].upper()})
        return {"status": 200, "body": {"success": True, "data": {"messages": reply}}}

    async def _poll(self, params: dict) -> dict:
        for _ in range(int(params["wait"] * 100)):
            if self.output:
                messages, self.output = self.output, []
                return {"status": 200, "body": {"success": True, "data": {"messages": messages, "ack": 7}}}
            await asyncio.sleep(0.01)
        return {"status": 200, "body": {"success": True, "data": {"messages": [], "ack": 7}}}


@pytest.fixture
def daemon(monkeypatch):
    daemon = Daemon()
    session = SimpleNamespace(session_id=SID, alive=True, state=daemon)
    registry = {SID: session}
    monkeypatch.setattr(code, "get_session_registry", lambda: registry)
    daemon.session = session
    daemon.registry = registry
    return daemon


@pytest.fixture
def client(monkeypatch, tmp_path, daemon):
    """Real token checks (the pytest bypass is off) against a known token; an app holding only this router."""
    monkeypatch.delenv("KOLLAB_ENGINE_BYPASS_AUTH", raising=False)
    monkeypatch.setattr(engine_auth, "_current_token", TOKEN)
    monkeypatch.setattr(engine_auth, "_token_file", lambda: tmp_path / "engine.token")
    app = FastAPI()
    app.include_router(code.router)
    with TestClient(app) as test_client:
        yield test_client


def _url(path: str, session: str = SID) -> str:
    return f"/sessions/{session}/code/{path}"


# ----------------------------------------------------------------------------------------------- files and git


def test_a_file_read_goes_to_the_sessions_daemon_and_its_answer_comes_back_unchanged(client, daemon):
    daemon.answers["fs.file"] = {"status": 403, "body": {"success": False, "error": {"code": "outside_workspace"}}}

    response = client.get(_url("fs/file"), params={"path": "a.txt"})

    assert daemon.calls == [("fs.file", {"path": "a.txt"})]
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "outside_workspace"
    assert response.headers["cache-control"] == "no-store"


def test_a_write_merges_the_query_and_the_body_with_the_body_winning(client, daemon):
    client.put(_url("fs/file"), params={"path": "a.txt", "content": "from the query"}, json={"content": "text"})

    assert daemon.calls == [("fs.write_file", {"path": "a.txt", "content": "text"})]


@pytest.mark.parametrize(
    ("method", "path", "op"),
    [
        ("POST", "fs/create", "fs.create"),
        ("POST", "fs/mkdir", "fs.mkdir"),
        ("POST", "fs/rename", "fs.rename"),
        ("POST", "fs/delete", "fs.delete"),
        ("DELETE", "fs/delete", "fs.delete"),
        ("POST", "git", "git"),
    ],
)
def test_each_body_route_names_its_daemon_operation(client, daemon, method, path, op):
    response = client.request(method, _url(path), json={"path": "x", "action": "status"})

    assert response.status_code == 200
    assert daemon.calls == [(op, {"path": "x", "action": "status"})]


@pytest.mark.parametrize(
    ("path", "op"),
    [("fs/tree", "fs.tree"), ("fs/search", "fs.search"), ("fs/git-status", "fs.git-status")],
)
def test_each_query_route_names_its_daemon_operation(client, daemon, path, op):
    client.get(_url(path), params={"q": "needle"})

    assert daemon.calls == [(op, {"q": "needle"})]


def test_get_git_answers_only_the_three_read_actions(client, daemon):
    for action in ("status", "current_branch", "list_branches"):
        assert client.get(_url("git"), params={"action": action}).status_code == 200
    assert client.get(_url("git")).status_code == 200  # no action means status
    refused = client.get(_url("git"), params={"action": "stage"})

    assert refused.status_code == 400
    assert refused.json()["error"]["code"] == "unknown_action"
    assert [call[1]["action"] for call in daemon.calls] == ["status", "current_branch", "list_branches", "status"]


def test_a_body_that_is_not_a_json_object_is_400(client, daemon):
    headers = {"Content-Type": "application/json"}

    for raw in (b"{not json", b"[1, 2]"):
        response = client.post(_url("fs/create"), content=raw, headers=headers)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "invalid_json"
    assert daemon.calls == []


def test_a_body_over_the_cap_is_413(client, daemon):
    big = b'{"content": "' + b"x" * code.MAX_BODY_BYTES + b'"}'

    response = client.post(_url("fs/create"), content=big, headers={"Content-Type": "application/json"})

    assert response.status_code == 413
    assert daemon.calls == []


def test_default_sized_escaped_file_can_reach_the_daemon(client, daemon):
    content = "\x01" * (2 * 1024 * 1024)
    response = client.put(_url("fs/file"), params={"path": "escaped.txt"}, json={"content": content})
    assert response.status_code == 200
    assert daemon.calls[-1][1]["content"] == content


@pytest.mark.asyncio
async def test_chunked_body_stops_reading_at_the_cap(monkeypatch):
    monkeypatch.setattr(code, "MAX_BODY_BYTES", 8)
    consumed = []

    async def stream():
        for chunk in (b"12345", b"6789", b"never read"):
            consumed.append(chunk)
            yield chunk

    response = await code._body(SimpleNamespace(headers={}, stream=stream))
    assert response.status_code == 413
    assert consumed == [b"12345", b"6789"]


def test_an_unknown_session_is_404_and_a_stopped_daemon_is_409(client, daemon):
    assert client.get(_url("fs/tree", "nobody")).status_code == 404
    daemon.session.alive = False

    stopped = client.get(_url("fs/tree"))

    assert stopped.status_code == 409
    assert stopped.json()["error"]["code"] == "daemon_not_running"
    assert daemon.calls == []


def test_a_daemon_that_fails_is_502_and_the_error_text_stays_out_of_the_answer(client, daemon):
    daemon.fail = RuntimeError("/home/other/secret.txt contents")

    response = client.get(_url("fs/tree"))

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "daemon_unreachable"
    assert "secret" not in response.text


def test_a_daemon_without_code_support_answers_a_plain_restart_notice(client, daemon):
    class RpcMethodNotFound(Exception):
        """Stands in for kollabor_rpc's error of the same name: the engine matches on the type's name."""

    daemon.fail = RpcMethodNotFound("method not found: state.code_call")

    response = client.get(_url("fs/tree"))

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "daemon_outdated"
    assert "Restart it" in response.json()["error"]["message"]
    assert "state.code_call" not in response.text


def test_settings_and_the_terminal_env_report_pass_through_to_the_daemon(client, daemon):
    assert client.get(_url("settings")).status_code == 200
    assert client.get(_url("terminal/env")).status_code == 200

    assert [call[0] for call in daemon.calls[-2:]] == ["settings", "terminal.env_report"]


# --------------------------------------------------------------------------------------------------- terminal


def _auth(ws, token: str = TOKEN) -> None:
    ws.send_json({"type": "auth", "token": token})


def test_a_wrong_token_closes_4401(client, daemon):
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(_url("terminal")) as ws:
            _auth(ws, "not-the-token")
            ws.receive_json()
    assert closed.value.code == 4401
    assert daemon.calls == []


def test_a_first_message_that_is_not_auth_closes_4401(client):
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(_url("terminal")) as ws:
            ws.send_text("not json")
            ws.receive_json()
    assert closed.value.code == 4401


def test_no_auth_message_in_time_closes_4401(client, monkeypatch):
    monkeypatch.setattr(code, "AUTH_TIMEOUT_SECONDS", 0.2)
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(_url("terminal")) as ws:
            ws.receive_json()  # nothing is sent; the server gives up first
    assert closed.value.code == 4401


def test_a_session_that_is_not_running_gets_an_error_and_4404(client, daemon):
    with pytest.raises(WebSocketDisconnect) as closed:
        with client.websocket_connect(_url("terminal", "nobody")) as ws:
            _auth(ws)
            assert ws.receive_json()["type"] == "error"
            ws.receive_json()
    assert closed.value.code == 4404


def test_the_socket_carries_open_input_and_output_between_the_browser_and_the_daemon(client, daemon):
    with client.websocket_connect(_url("terminal")) as ws:
        _auth(ws)
        ws.send_json({"type": "open", "cols": 100, "rows": 30})
        assert ws.receive_json() == {"type": "ready", "sessionId": "shell-1"}
        ws.send_json({"type": "input", "data": "ls\n"})
        assert ws.receive_json() == {"type": "output", "data": "LS\n"}

    ops = [op for op, _ in daemon.calls]
    assert "terminal.message" in ops and "terminal.poll" in ops  # the poll may start before the first message
    assert ops[-1] == "terminal.detach"  # closing the socket lets the daemon drop its link


def test_every_socket_gets_its_own_link_and_the_poll_acknowledges_what_it_received(client, daemon):
    for _ in range(2):
        with client.websocket_connect(_url("terminal")) as ws:
            _auth(ws)
            ws.send_json({"type": "open"})
            ws.receive_json()
            ws.send_json({"type": "input", "data": "x"})
            ws.receive_json()

    links = {params["link"] for op, params in daemon.calls if op == "terminal.message"}
    polls = [params for op, params in daemon.calls if op == "terminal.poll"]
    assert len(links) == 2 and all(8 <= len(link) <= 64 for link in links)
    assert polls[0].get("ack") is None  # the first poll has nothing to acknowledge
    assert any(poll.get("ack") == 7 for poll in polls)  # later polls acknowledge the sequence the daemon gave


def test_a_daemon_that_stops_answering_ends_the_socket_with_an_error_and_1011(client, daemon):
    with client.websocket_connect(_url("terminal")) as ws:
        _auth(ws)
        ws.send_json({"type": "open"})
        ws.receive_json()
        daemon.fail = RuntimeError("gone")
        ws.send_json({"type": "input", "data": "x"})
        assert ws.receive_json()["type"] == "error"
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_json()
    assert closed.value.code == code.CLOSE_DAEMON_LOST


def test_a_terminal_a_setting_turned_off_says_which_setting_not_that_the_daemon_stopped(client, daemon):
    refusal = {
        "status": 403,
        "body": {
            "success": False,
            "error": {
                "code": "terminal_disabled",
                "message": "Terminals are turned off for this agent. Setting: kollabor.code.terminal.enabled",
                "setting": "kollabor.code.terminal.enabled",
            },
        },
    }
    daemon.answers["terminal.message"] = refusal
    daemon.answers["terminal.poll"] = refusal

    async def refuse(op, params):
        daemon.calls.append((op, params))
        if op in ("terminal.message", "terminal.poll"):
            return refusal
        return {"status": 200, "body": {"success": True, "data": {}}}

    daemon.session.state.code_call = refuse
    with client.websocket_connect(_url("terminal")) as ws:
        _auth(ws)
        ws.send_json({"type": "open"})
        message = ws.receive_json()

    assert message["type"] == "error"
    assert "kollabor.code.terminal.enabled" in message["message"]
    assert "stopped answering" not in message["message"]
