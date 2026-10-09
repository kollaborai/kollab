"""A same-host proxy reaches the relay over a unix socket only it can open (issue #123, items 8 and 9).

Item 9: over a unix socket the immediate peer is whoever may open the socket file, so
X-Real-IP is trusted there and a local process that cannot open it has no say.
Item 8: the shipped caps (4096 per worker, 64 per address) must agree everywhere they are stated.
"""

from __future__ import annotations

import asyncio
import errno
import grp
import http.client
import ipaddress
import json
import os
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path

import aiohttp
import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from plugins.hub import relay_runtime, relay_selfhost
from plugins.hub import relay_service as service
from plugins.hub.relay_backend import RelayLimits
from plugins.hub.relay_runtime import RelayRuntime, RuntimeConfig, RuntimeConfigError, Worker
from plugins.hub.relay_service import RelayConfig, RelayConfigError, bind_unix_socket, raise_fd_limit

ROOT = Path(__file__).resolve().parents[2]
ORIGIN = "https://relay.example"
NODE_ID = "a" * 32


@pytest.fixture
def sockdir():
    # sun_path holds ~104 bytes on macOS; pytest's tmp_path alone can exceed that.
    path = tempfile.mkdtemp(prefix="kr-", dir="/tmp")
    os.chmod(path, 0o755)
    yield Path(path)
    shutil.rmtree(path, ignore_errors=True)


def mode_of(path: Path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


# --- item 8: one set of caps -------------------------------------------------------------------


def test_every_default_states_the_same_caps():
    caps = (4096, 16, 64)  # per worker, per room, per client address

    def of(source):
        return tuple(getattr(source, f"max_connections_per_{scope}") for scope in ("node", "room", "source"))

    assert of(RelayLimits()) == caps
    assert (service.MAX_CONNECTIONS_PER_NODE, service.MAX_CONNECTIONS_PER_ROOM) == caps[:2]
    assert service.MAX_CONNECTIONS_PER_SOURCE == caps[2]
    assert tuple(relay_runtime.LIMIT_DEFAULTS.values()) == caps
    assert of(service.build_parser().parse_args(["--origin", ORIGIN])) == caps
    directory = relay_selfhost.build_parser().parse_args(["--domain", "agents.example.com"])
    assert (directory.max_connections_per_room, directory.max_connections_per_source) == caps[1:]


def test_the_descriptor_limit_is_raised_for_the_cap_or_the_cap_is_lowered_to_fit(monkeypatch):
    calls = []
    fake = types.SimpleNamespace(
        RLIMIT_NOFILE=7,
        RLIM_INFINITY=-1,
        getrlimit=lambda _: (1024, -1),
        setrlimit=lambda which, pair: calls.append(pair),
    )
    monkeypatch.setattr(service, "resource", fake)
    assert raise_fd_limit(4096) == 4096
    assert calls == [(4096 + service.FD_HEADROOM, -1)]
    calls.clear()
    assert raise_fd_limit(100) == 100  # already enough: left alone
    assert calls == []
    fake.getrlimit = lambda _: (256, 1024)  # a small host still starts, on the cap that fits
    assert raise_fd_limit(4096) == 1024 - service.FD_HEADROOM
    assert calls == [(1024, 1024)]
    fake.getrlimit = lambda _: (100, 200)
    with pytest.raises(RelayConfigError, match=r"hard limit \(200\).*LimitNOFILE"):
        raise_fd_limit(4096)


# --- item 9: the socket -----------------------------------------------------------------------


def test_a_socket_without_a_group_is_for_its_owner_only(sockdir):
    path = sockdir / "relay.sock"
    with bind_unix_socket(str(path)):
        assert stat.S_ISSOCK(os.stat(path).st_mode) and mode_of(path) == 0o600


def test_a_named_group_gets_read_write_and_nobody_else_does(sockdir):
    gid = next((g for g in os.getgroups() if g != os.getgid()), os.getgid())
    path = sockdir / "relay.sock"
    with bind_unix_socket(str(path), grp.getgrgid(gid).gr_name):
        assert mode_of(path) == 0o660 and os.stat(path).st_gid == gid


def test_a_group_this_user_is_not_in_is_refused_without_leaving_a_socket(sockdir, monkeypatch):
    def denied(*_):
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(os, "chown", denied)
    path = sockdir / "relay.sock"
    with pytest.raises(RelayConfigError, match="this user must belong to it"):
        bind_unix_socket(str(path), grp.getgrgid(os.getgid()).gr_name)
    assert not path.exists()


def test_an_unknown_group_is_refused(sockdir):
    with pytest.raises(RelayConfigError, match="unknown unix socket group"):
        bind_unix_socket(str(sockdir / "relay.sock"), "no-such-group-here")


def test_a_directory_others_can_write_to_is_refused(sockdir):
    os.chmod(sockdir, 0o777)
    with pytest.raises(RelayConfigError, match="not be writable by group or others"):
        bind_unix_socket(str(sockdir / "relay.sock"))
    os.chmod(sockdir, 0o770)  # a group that can write can replace the socket too
    with pytest.raises(RelayConfigError, match="not be writable by group or others"):
        bind_unix_socket(str(sockdir / "relay.sock"))


@pytest.mark.parametrize("path", ["relay.sock", "/tmp/" + "x" * 120, "/tmp/a\0b"])
def test_a_relative_long_or_nul_path_is_refused(path):
    with pytest.raises(RelayConfigError, match="absolute path"):
        bind_unix_socket(path)


def test_a_leftover_socket_is_replaced_but_a_live_one_or_a_file_is_not(sockdir):
    path = sockdir / "relay.sock"
    stale = socket.socket(socket.AF_UNIX)
    stale.bind(str(path))
    stale.close()  # a crash leaves the file with nobody listening
    with bind_unix_socket(str(path)):
        pass
    with bind_unix_socket(str(path)) as live:
        live.listen()
        with pytest.raises(RelayConfigError, match="in use by a running process"):
            bind_unix_socket(str(path))
    path.unlink()
    path.write_text("not a socket")
    with pytest.raises(RelayConfigError, match="not a socket of this user"):
        bind_unix_socket(str(path))
    assert path.read_text() == "not a socket"


def test_the_process_umask_is_restored(sockdir):
    before = os.umask(0o022)
    try:
        with bind_unix_socket(str(sockdir / "relay.sock")):
            pass
        assert os.umask(0o022) == 0o022
    finally:
        os.umask(before)


def test_the_worker_command_takes_a_socket_instead_of_an_address():
    with pytest.raises(SystemExit, match="replaces --bind, --port and --trusted-proxy"):
        service.main(["--origin", ORIGIN, "--dev-in-memory", "--unix-socket", "/tmp/x.sock", "--port", "9"])
    with pytest.raises(SystemExit, match="replaces --bind, --port and --trusted-proxy"):
        service.main(
            ["--origin", ORIGIN, "--dev-in-memory", "--unix-socket", "/tmp/x.sock", "--trusted-proxy", "10.0.0.1"]
        )
    with pytest.raises(SystemExit, match="--unix-socket-group needs --unix-socket"):
        service.main(["--origin", ORIGIN, "--dev-in-memory", "--unix-socket-group", "www-data"])


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path: str):
        super().__init__("localhost", timeout=5)
        self._path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.settimeout(5)
        self.sock.connect(self._path)


def test_the_worker_command_serves_on_its_socket_and_removes_it_on_stop(sockdir):
    path = sockdir / "relay.sock"
    env = {**os.environ, "HOME": str(sockdir), "KOLLAB_NO_KEYRING": "1"}
    command = [sys.executable, "-m", "plugins.hub.relay_service", "--dev-in-memory", "--origin", ORIGIN]
    with tempfile.TemporaryFile() as log:
        process = subprocess.Popen(
            [*command, "--unix-socket", str(path)], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT
        )
        reply, deadline = None, time.monotonic() + 30
        try:
            while reply is None and time.monotonic() < deadline:
                try:
                    connection = UnixHTTPConnection(str(path))
                    connection.request("GET", "/relay/v1/health")
                    reply = connection.getresponse()
                except OSError:
                    time.sleep(0.1)
            assert reply is not None and reply.status == 200 and json.loads(reply.read())["origin"] == ORIGIN
            assert mode_of(path) == 0o600
        finally:
            process.send_signal(signal.SIGTERM)
            code = process.wait(timeout=30)
            log.seek(0)
            assert code == 0, log.read().decode(errors="replace")
    assert not path.exists()


# --- X-Real-IP: trusted on the socket, and only there ---------------------------------------------


@pytest_asyncio.fixture
async def over_unix(sockdir):
    limits = RelayLimits(max_connections_per_source=1)
    app = service.create_app(RelayConfig(origin=ORIGIN, node_id=NODE_ID, dev_in_memory=True, limits=limits))
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    path = sockdir / "relay.sock"
    await web.SockSite(runner, bind_unix_socket(str(path))).start()
    session = aiohttp.ClientSession(connector=aiohttp.UnixConnector(path=str(path)))
    try:
        yield app["relay_state"], session
    finally:
        await session.close()
        await runner.cleanup()


def sources(state) -> list[str]:
    return sorted(client.source_ip for client in state.connections)


@pytest.mark.asyncio
async def test_the_socket_takes_its_client_from_x_real_ip(over_unix):
    state, session = over_unix
    first = await session.ws_connect("http://relay" + service.WEBSOCKET_PATH, headers={"X-Real-IP": "203.0.113.7"})
    await first.receive_json()
    assert sources(state) == ["203.0.113.7"]
    # The per-address cap is that client's, not the proxy's: a second address is not blocked by the first.
    second = await session.ws_connect("http://relay" + service.WEBSOCKET_PATH, headers={"X-Real-IP": "2001:db8::9"})
    await second.receive_json()
    assert sources(state) == ["2001:db8::9", "203.0.113.7"]
    with pytest.raises(aiohttp.WSServerHandshakeError) as refused:  # the cap (1 here) still holds per address
        await session.ws_connect("http://relay" + service.WEBSOCKET_PATH, headers={"X-Real-IP": "203.0.113.7"})
    assert refused.value.status == 503
    await first.close()
    await second.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{}, {"X-Real-IP": "not-an-ip"}, {"X-Real-IP": "203.0.113.7, 198.51.100.1"}])
async def test_a_socket_request_without_one_literal_client_address_is_refused(over_unix, headers):
    state, session = over_unix
    with pytest.raises(aiohttp.WSServerHandshakeError) as refused:
        await session.ws_connect("http://relay" + service.WEBSOCKET_PATH, headers=headers)
    assert refused.value.status == 403
    assert sources(state) == []


@pytest.mark.asyncio
async def test_health_and_metrics_answer_on_the_socket_without_a_client_address(over_unix):
    _, session = over_unix
    health = await session.get("http://relay/relay/v1/health")
    assert health.status == 200 and (await health.json())["origin"] == ORIGIN
    assert (await session.get("http://relay/relay/v1/metrics")).status == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("trusted", [False, True])
async def test_over_tcp_only_a_listed_proxy_may_name_the_client(trusted):
    """The hole the socket closes: any local process can reach 127.0.0.1 and send X-Real-IP."""
    proxies = frozenset({ipaddress.ip_address("127.0.0.1")}) if trusted else frozenset()
    app = service.create_app(RelayConfig(origin=ORIGIN, node_id=NODE_ID, dev_in_memory=True, trusted_proxies=proxies))
    async with TestClient(TestServer(app)) as client:
        ws = await client.ws_connect(service.WEBSOCKET_PATH, headers={"X-Real-IP": "203.0.113.7"})
        await ws.receive_json()
        assert sources(app["relay_state"]) == (["203.0.113.7"] if trusted else ["127.0.0.1"])
        await ws.close()


# --- the runtime's workers ---------------------------------------------------------------------


def runtime_config(**overrides) -> RuntimeConfig:
    values = dict(
        origin=ORIGIN,
        bind_host="127.0.0.1",
        base_port=19078,
        workers=2,
        health_port=19080,
        node_prefix="relay-test",
        state_dir=Path(tempfile.gettempdir()),
        trusted_proxies=(),
        backend={"mode": "external", "cluster": False},
        limits=dict(relay_runtime.LIMIT_DEFAULTS),
    )
    return RuntimeConfig(**{**values, **overrides})


def write_config(tmp_path: Path, **extra) -> Path:
    directory = tmp_path / "cfg"
    directory.mkdir(mode=0o700)
    directory.chmod(0o700)
    path = directory / "relay.json"
    path.write_text(
        json.dumps(
            {
                "origin": ORIGIN,
                "node_prefix": "t",
                "state_dir": str(tmp_path / "state"),
                "backend": {"mode": "external", "url_env": "RELAY_URL", "cluster": False},
                **extra,
            }
        )
    )
    path.chmod(0o600)
    return path


def test_the_config_defaults_to_the_shipped_caps_and_reads_a_socket_directory(tmp_path):
    config = RuntimeConfig.load(write_config(tmp_path))
    assert config.limits == relay_runtime.LIMIT_DEFAULTS and config.unix_socket_dir is None
    other = tmp_path / "other"
    other.mkdir()
    config = RuntimeConfig.load(write_config(other, unix_socket_dir="/run/kollab-relay", unix_socket_group="www-data"))
    assert config.socket_path(2) == Path("/run/kollab-relay/relay-2.sock") and config.unix_socket_group == "www-data"


@pytest.mark.parametrize(
    "extra",
    [
        {"unix_socket_dir": "run/kollab"},
        {"unix_socket_dir": "/run/" + "x" * 90},
        {"unix_socket_dir": "/run/kollab", "trusted_proxies": ["10.0.0.1"]},
        {"unix_socket_group": "www-data"},
        {"unix_socket_dir": "/run/kollab", "unix_socket_group": "Bad Group"},
    ],
)
def test_a_socket_config_that_contradicts_itself_is_refused(tmp_path, extra):
    with pytest.raises(RuntimeConfigError, match="unix_socket"):
        RuntimeConfig.load(write_config(tmp_path, **extra))


@pytest.mark.asyncio
async def test_workers_listen_on_their_own_socket_when_a_directory_is_configured(monkeypatch):
    captured = []

    class Process:
        pid = 4242
        stdout = asyncio.StreamReader()

    async def fake_exec(*args, **kwargs):
        captured.append(list(args))
        Process.stdout.feed_eof()
        return Process()

    monkeypatch.setattr(relay_runtime.asyncio, "create_subprocess_exec", fake_exec)
    for config, expect in (
        (
            runtime_config(unix_socket_dir=Path("/run/kollab-relay"), unix_socket_group="www-data"),
            ["--unix-socket", "/run/kollab-relay/relay-2.sock", "--unix-socket-group", "www-data"],
        ),
        (runtime_config(), ["--bind", "127.0.0.1", "--port", "19079"]),
    ):
        runtime = RelayRuntime(config)
        runtime.backend_url = "redis://127.0.0.1:1/0"
        worker = Worker(2)
        await runtime._spawn(worker)
        await worker.log_task
        args = captured[-1]
        at = args.index(expect[0])
        assert args[at : at + len(expect)] == expect
        assert ("--port" in args) == (expect[0] == "--bind")


@pytest.mark.asyncio
async def test_the_supervisor_probes_a_socket_worker_over_its_socket(sockdir):
    config = runtime_config(unix_socket_dir=sockdir, workers=1)
    app = service.create_app(RelayConfig(origin=ORIGIN, node_id=config.node_id(1), dev_in_memory=True))
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.SockSite(runner, bind_unix_socket(str(config.socket_path(1)))).start()
    worker = Worker(1)
    worker.process = types.SimpleNamespace(returncode=None)
    stopped = False
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=3)) as session:
            await RelayRuntime(config)._worker_health(session, worker)
            assert worker.ready is True
            await runner.cleanup()
            stopped = True
            await RelayRuntime(config)._worker_health(session, worker)
            assert worker.ready is False
    finally:
        if not stopped:
            await runner.cleanup()
