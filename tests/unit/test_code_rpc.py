"""code_call across the RPC boundary: daemon handler <-> RemoteStateService (#987).

A session on another computer answers the web UI's files, git and terminal calls through this one method, so what
comes back must be exactly what ``kollabor.code_service.call`` answers on that computer.
"""

import asyncio
from types import SimpleNamespace

import pytest

from kollabor import code_service
from kollabor.state.handlers import register_state_handlers
from kollabor.state.local import LocalStateService
from kollabor.state.remote import RemoteStateService


class _Rpc:
    """Routes RpcClient.call straight to the daemon's registered handlers."""

    def __init__(self, handlers):
        self.handlers = handlers
        self.last_timeout = None

    async def call(self, method, params, timeout=None):
        self.last_timeout = timeout
        return await self.handlers[method](params)


@pytest.fixture
def wire(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)  # the daemon serves its working directory
    service = LocalStateService.__new__(LocalStateService)
    service._llm_service = None  # no config: secrets stay out of the shell
    handlers = {}
    register_state_handlers(SimpleNamespace(register=lambda *a, **k: handlers.__setitem__(a[0], a[1])), service)
    rpc = _Rpc(handlers)
    return RemoteStateService(rpc), rpc, handlers, service


def run(coro):
    return asyncio.run(coro)


def test_a_remote_file_call_returns_what_the_daemon_computer_has(wire, tmp_path):
    remote, _, _, _ = wire
    (tmp_path / "only-on-the-daemon.txt").write_text("hello")

    answer = run(remote.code_call("fs.tree", {}))

    assert answer["status"] == 200
    assert [entry["name"] for entry in answer["body"]["data"]["tree"]] == ["only-on-the-daemon.txt"]


def test_the_remote_answer_equals_the_local_one(wire, tmp_path):
    remote, _, _, service = wire
    (tmp_path / "a.txt").write_text("x")
    params = {"path": "a.txt"}

    assert run(remote.code_call("fs.file", params)) == run(service.code_call("fs.file", params))


def test_a_request_cannot_move_the_daemons_folder(wire, tmp_path):
    remote, _, _, _ = wire
    (tmp_path / "mine.txt").write_text("x")

    answer = run(remote.code_call("fs.tree", {"workspace": "/", "root": "/"}))

    assert [entry["name"] for entry in answer["body"]["data"]["tree"]] == ["mine.txt"]


def test_an_unknown_op_is_a_400_envelope_not_a_crash(wire):
    remote, _, _, _ = wire

    answer = run(remote.code_call("fs.shred", {}))

    assert answer["status"] == 400 and answer["body"]["success"] is False


def test_the_call_waits_longer_than_a_terminal_poll(wire):
    remote, rpc, _, _ = wire

    run(remote.code_call("fs.tree", {}))

    assert rpc.last_timeout >= code_service.terminal.MAX_POLL_SECONDS + 10


@pytest.mark.parametrize(
    "payload",
    [
        {"op": 5, "params": {}},
        {"op": "fs.tree", "params": []},
        {"op": "fs.tree", "params": {}, "extra": 1},
        {"params": {}},
    ],
)
def test_the_handler_refuses_a_malformed_request(wire, payload):
    _, _, handlers, _ = wire

    answer = run(handlers["state.code_call"](payload))

    assert answer == {"error": "invalid code request", "status": 400}


def test_a_failing_service_answers_500_without_the_exception_text(wire, monkeypatch):
    remote, _, handlers, _ = wire

    async def boom(op, params, root=None):
        raise RuntimeError("secret file content")

    monkeypatch.setattr(code_service, "call", boom)

    answer = run(handlers["state.code_call"]({"op": "fs.tree", "params": {}}))
    assert answer == {"error": "code service unavailable", "status": 500}
    with pytest.raises(ValueError):  # the remote side turns the refusal into one error
        run(remote.code_call("fs.tree", {}))


def test_local_state_resolves_code_settings_from_config_and_the_agents_json(tmp_path):
    """``kollabor.code.*`` in the config reaches the code service; the active agent's ``code`` object wins."""
    from kollabor.state.local import LocalStateService

    class Config:
        def __init__(self, values):
            self.values = values

        def get(self, path, default=None):
            return self.values.get(path, default)

    class Llm:
        def __init__(self, config):
            self.config = config

    class Agents:
        def __init__(self, directory):
            self.directory = directory

        def get_active_agent(self):
            return SimpleNamespace(directory=self.directory)

    def settings(values, agents=None):
        return LocalStateService(Llm(Config(values)), None, agent_manager=agents)._code_settings()

    assert settings({"kollabor.code.terminal.pass_secret_env": True}).pass_secret_env is True
    assert settings({"kollabor.code.terminal.pass_secret_env": "yes"}).pass_secret_env is False  # wrong type: default
    assert settings({}).pass_secret_env is False
    (tmp_path / "agent.json").write_text('{"code": {"terminal": {"pass_secret_env": true}, "read_only": true}}')
    mine = settings({"kollabor.code.read_only": False}, Agents(tmp_path))
    assert mine.pass_secret_env is True and mine.read_only is True
    (tmp_path / "agent.json").write_text("{not json")
    assert settings({}, Agents(tmp_path)).read_only is False  # a broken agent file never closes the door
    assert LocalStateService(Llm(None), None)._code_settings().enabled is True
