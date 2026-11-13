"""A terminal attach window outlives a restart of its daemon and attaches again by itself (#968).

A restart execs the daemon in place: same pid, same socket path, a new image whose
event numbers start over. The window used to print "daemon exited" and quit for any
closed connection, while the daemon was alive.
"""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from kollabor.application import TerminalLLMChat
from kollabor_config import local_ipc


@pytest.fixture
def sock(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("KOLLAB_DAEMON_PID", raising=False)
    # macOS caps unix socket paths at 104 bytes: a short relative name stays inside tmp_path.
    monkeypatch.chdir(tmp_path)
    return Path("sunstone.sock")


def _line(obj):
    return (json.dumps(obj) + "\n").encode()


def _message(seq, text):
    return {"type": "message", "message_type": "assistant", "content": text, "kwargs": {}, "seq": seq}


class Image:
    """One image of a daemon, listening on the socket path an exec keeps."""

    def __init__(self, path, agent_id, version, snapshot=()):
        self.path = path
        self.agent_id = agent_id
        self.version = version
        self.snapshot = list(snapshot)
        self.attaches, self.inputs, self.writers = [], [], []
        self.endpoint = None

    async def start(self):
        self.endpoint = await local_ipc.serve(self._handle, self.path)

    async def _handle(self, reader, writer):
        self.attaches.append(json.loads(await reader.readline()))
        ack = {"type": "attach_ack", "agent_id": self.agent_id, "version": self.version, "uptime": 3, "hub": {}}
        writer.write(_line(ack))
        for event in self.snapshot:
            writer.write(_line(event))
        await writer.drain()
        self.writers.append(writer)
        while True:
            raw = await reader.readline()
            if not raw:
                return
            msg = json.loads(raw)
            if msg.get("type") == "input":
                self.inputs.append(msg["text"])
            elif msg.get("action") == "rpc_request":
                reply = {"action": "rpc_reply", "request_id": msg["request_id"], "result": {"image": self.agent_id}}
                writer.write(_line(reply))
                await writer.drain()

    def send(self, event):
        for writer in self.writers:
            writer.write(_line(event))

    def drop_windows(self):
        """Close every window's connection; the daemon keeps listening."""
        for writer in self.writers:
            writer.close()
        self.writers = []

    def exec_away(self):
        """What a restart in place does to a window: the connection closes and the socket goes away."""
        self.endpoint.close()
        self.drop_windows()


def _window(path, alive):
    """A TerminalLLMChat in attach mode with just enough around it; ``alive`` answers the pid check."""
    shown, scheduled, tasks, hooks = [], [], [], {}
    app = TerminalLLMChat.__new__(TerminalLLMChat)
    app._attach_to = "sunstone"
    app._attach_socket = str(path)
    app._startup_ready = asyncio.Event()
    app.running = True
    app.renderer = SimpleNamespace(
        message_coordinator=SimpleNamespace(display_message_sequence=shown.extend),
        update_thinking=lambda *a, **k: None,
    )

    async def register_hook(hook):
        hooks[hook.name] = hook

    app.event_bus = SimpleNamespace(
        register_service=lambda *a, **k: None,
        register_hook=register_hook,
        get_service=lambda name: None,
    )

    def create_background_task(coro, name):
        if name == "attach_event_reader":
            tasks.append(asyncio.ensure_future(coro))
        else:  # the shutdown and the 5 s os._exit watchdog must never run inside pytest
            scheduled.append(name)
            coro.close()

    app.create_background_task = create_background_task
    app._attach_daemon_alive = lambda: alive
    return app, shown, scheduled, hooks, tasks


def _texts(shown):
    return [item[1] for item in shown]


async def _until(condition, seconds=10.0):
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while not condition():
        if loop.time() > deadline:
            raise AssertionError("timed out waiting")
        await asyncio.sleep(0.05)


async def _stop(app, tasks, *images):
    app.running = False
    for task in tasks:
        task.cancel()
    for image in images:
        if image.endpoint is not None:
            image.endpoint.close()
        image.drop_windows()


@pytest.mark.asyncio
async def test_window_attaches_again_when_the_daemon_announces_a_restart(sock):
    first = Image(sock, "img1", "0.21.0+aaa", [_message(41, "old line")])
    second = Image(sock, "img2", "0.21.0+bbb", [_message(1, "new image line")])
    await first.start()
    app, shown, scheduled, hooks, tasks = _window(sock, alive=True)
    try:
        await app._initialize_attach_proxy()
        await _until(lambda: "old line" in _texts(shown))

        first.send({"type": "daemon_restarting", "identity": "sunstone", "seq": 42})
        await asyncio.sleep(0.2)
        first.exec_away()
        await asyncio.sleep(0.8)  # the gap between the old image and the new one
        await second.start()
        await _until(lambda: "new image line" in _texts(shown))  # seq 1 after seq 42: not fenced out

        texts = _texts(shown)
        assert app.running is True and scheduled == []
        assert texts.count("Restarting sunstone...") == 1
        assert texts.index("Restarting sunstone...") < texts.index("Restarted on 0.21.0+bbb")
        assert not [t for t in texts if "daemon exited" in t]
        assert texts.count("old line") == 1  # the scrollback is left as it is

        await hooks["attach_proxy_input"].callback({"message": "hello"})
        await _until(lambda: second.inputs == ["hello"])  # typing reaches the new image
        assert await app._rpc_client.call("state.ping", {}, timeout=5) == {"image": "img2"}
    finally:
        await _stop(app, tasks, first, second)


@pytest.mark.asyncio
async def test_window_shows_when_a_restart_fails_after_the_command_returns(sock):
    daemon = Image(sock, "img1", "0.21.0+aaa", [_message(41, "old line")])
    await daemon.start()
    app, shown, scheduled, hooks, tasks = _window(sock, alive=True)
    try:
        await app._initialize_attach_proxy()
        await _until(lambda: "old line" in _texts(shown))

        daemon.send({
            "type": "agent_restart_failed",
            "identity": "sunstone",
            "seq": 42,
        })
        await _until(lambda: "Restart of sunstone failed; the agent is still running." in _texts(shown))

        assert app.running is True and scheduled == []
        assert "[ok] Restarting this agent. The conversation stays." not in _texts(shown)
    finally:
        await _stop(app, tasks, daemon)


@pytest.mark.asyncio
async def test_window_attaches_again_while_the_pid_is_alive_and_nobody_announced_anything(sock):
    daemon = Image(sock, "img1", "0.21.0+aaa", [_message(41, "old line")])
    await daemon.start()
    app, shown, scheduled, hooks, tasks = _window(sock, alive=True)
    try:
        await app._initialize_attach_proxy()
        await _until(lambda: "old line" in _texts(shown))

        daemon.drop_windows()  # the daemon lives and still listens: only the connection went
        await _until(lambda: len(daemon.attaches) == 2)
        daemon.send(_message(43, "after"))
        await _until(lambda: "after" in _texts(shown))

        texts = _texts(shown)
        assert app.running is True and scheduled == []
        assert "lost connection to sunstone, reconnecting..." in texts
        assert "reconnected to sunstone" in texts
        assert not [t for t in texts if "daemon exited" in t or "Restarted" in t]
        assert texts.count("old line") == 1  # the replay of the same image is not printed twice
    finally:
        await _stop(app, tasks, daemon)


@pytest.mark.asyncio
async def test_window_still_says_daemon_exited_once_the_pid_is_gone(sock):
    daemon = Image(sock, "img1", "0.21.0+aaa", [_message(41, "old line")])
    await daemon.start()
    app, shown, scheduled, hooks, tasks = _window(sock, alive=False)
    try:
        await app._initialize_attach_proxy()
        await _until(lambda: "old line" in _texts(shown))
        daemon.exec_away()
        await _until(lambda: app.running is False)
        assert "sunstone disconnected — daemon exited" in _texts(shown)
        assert "attach_client_shutdown" in scheduled
    finally:
        await _stop(app, tasks, daemon)


@pytest.mark.asyncio
async def test_window_that_cannot_tell_the_pid_keeps_the_old_quick_exit(sock):
    daemon = Image(sock, "img1", "0.21.0+aaa", [_message(41, "old line")])
    await daemon.start()
    app, shown, scheduled, hooks, tasks = _window(sock, alive=None)
    try:
        await app._initialize_attach_proxy()
        await _until(lambda: "old line" in _texts(shown))
        daemon.exec_away()
        await _until(lambda: app.running is False, seconds=5.0)  # no 30 s wait for an unknown pid
        assert "sunstone disconnected — daemon exited" in _texts(shown)
    finally:
        await _stop(app, tasks, daemon)


@pytest.mark.asyncio
async def test_window_gives_up_after_a_bounded_wait_with_one_true_line(sock, monkeypatch):
    import kollabor.daemon as daemon_module

    monkeypatch.setattr(daemon_module, "REATTACH_WAIT", 1.5, raising=False)
    first = Image(sock, "img1", "0.21.0+aaa", [_message(41, "old line")])
    await first.start()
    app, shown, scheduled, hooks, tasks = _window(sock, alive=True)
    try:
        await app._initialize_attach_proxy()
        await _until(lambda: "old line" in _texts(shown))
        first.send({"type": "daemon_restarting", "identity": "sunstone", "seq": 42})
        await asyncio.sleep(0.2)
        first.exec_away()  # nothing ever listens again
        await _until(lambda: app.running is False, seconds=8.0)

        texts = _texts(shown)
        assert texts[-1] == "Restarted. Run kollab again to attach."
        assert not [t for t in texts if "daemon exited" in t]
        assert "attach_client_shutdown" in scheduled
    finally:
        await _stop(app, tasks, first)


@pytest.mark.asyncio
async def test_detaching_with_ctrl_z_is_not_mistaken_for_a_restart(sock):
    daemon = Image(sock, "img1", "0.21.0+aaa", [_message(41, "old line")])
    await daemon.start()
    app, shown, scheduled, hooks, tasks = _window(sock, alive=True)
    try:
        await app._initialize_attach_proxy()
        await _until(lambda: "old line" in _texts(shown))
        app._attach_detaching = True
        daemon.drop_windows()
        await _until(lambda: all(task.done() for task in tasks), seconds=5.0)
        assert len(daemon.attaches) == 1  # it did not attach again
        assert not [t for t in _texts(shown) if "reconnect" in t or "Restart" in t]
    finally:
        await _stop(app, tasks, daemon)
