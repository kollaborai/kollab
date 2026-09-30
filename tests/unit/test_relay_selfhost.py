"""`kollab relay serve --domain`: the relay, the publisher and the key file in one process."""

from __future__ import annotations

import asyncio
import json
import os
import re
import select
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestClient, TestServer

from plugins.hub import relay_selfhost as selfhost
from plugins.hub.dns.discovery import normalize_target, parse_txt, verify_manifest
from plugins.hub.dns.discovery_publish import publish
from plugins.hub.relay_runtime import RuntimeConfigError
from plugins.hub.relay_selfhost import Settings, build_app, build_parser, prepare, publish_once, settings_from

ROOT = Path(__file__).resolve().parents[2]
DOMAIN = "agents.example.com"


def make_settings(tmp_path: Path, domain: str = DOMAIN, **overrides) -> Settings:
    return Settings(
        target=normalize_target(domain, document=False),
        state_dir=tmp_path / "state",
        trusted_proxies=("127.0.0.1", "::1"),
        **overrides,
    )


def parse(*argv: str) -> Settings:
    return settings_from(build_parser().parse_args(list(argv)))


def manifest_of(settings: Settings) -> dict:
    return json.loads(settings.key_file.read_text())


# --- settings, and what the operator is told to do ---------------------------------------------


def test_domain_becomes_origin_txt_and_state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    settings = parse("--domain", "Agents.Example.COM")
    assert settings.origin == "https://agents.example.com"
    assert settings.state_dir == tmp_path / ".kollab" / "relay" / "agents.example.com"
    assert (settings.bind, settings.port) == ("127.0.0.1", 9078)
    assert settings.trusted_proxies == ("127.0.0.1", "::1")
    assert parse("--domain", "localhost:9443").state_dir.name == "localhost-9443"


def test_the_printed_txt_record_is_one_the_client_accepts(tmp_path):
    settings = make_settings(tmp_path)
    hint = parse_txt([(selfhost.txt_value(settings).encode(),)])
    assert hint == {"v": "aid1", "u": normalize_target(DOMAIN).url}
    assert hint["u"] == "https://agents.example.com/.well-known/agent-keys.json"


@pytest.mark.parametrize(
    "argv",
    [
        ["--domain", "127.0.0.1"],
        ["--domain", "https://agents.example.com/relay"],
        ["--domain", "agents.example.com", "--bind", "localhost"],
        ["--domain", "agents.example.com", "--port", "0"],
        ["--domain", "agents.example.com", "--max-connections-per-room", "1000"],
        ["--domain", "agents.example.com", "--trusted-proxy", "proxy"],
    ],
)
def test_bad_settings_are_refused_before_anything_is_created(argv, capsys):
    with pytest.raises(SystemExit) as exit_info:
        selfhost.main(argv)
    assert exit_info.value.code == 2
    assert "kollab relay serve: error" in capsys.readouterr().err


def test_flags_recreate_the_same_settings(tmp_path):
    settings = parse(
        "--domain", "agents.example.com:8443", "--state-dir", str(tmp_path / "s"), "--bind", "10.0.0.5",
        "--port", "9100", "--trusted-proxy", "10.0.0.1", "--max-connections-per-source", "200",
    )  # fmt: skip
    assert parse(*selfhost.flags(settings)) == settings
    # Defaults stay out of the command the operator is shown; a non-default state dir does not.
    assert selfhost.flags(parse("--domain", DOMAIN)) == ["--domain", DOMAIN]
    assert selfhost.flags(make_settings(tmp_path)) == ["--domain", DOMAIN, "--state-dir", str(tmp_path / "state")]


def test_a_non_loopback_bind_trusts_no_proxy_until_told():
    assert parse("--domain", DOMAIN, "--bind", "10.0.0.5").trusted_proxies == ()
    told = parse("--domain", DOMAIN, "--bind", "10.0.0.5", "--trusted-proxy", "10.0.0.1")
    assert told.trusted_proxies == ("10.0.0.1",)


def test_setup_text_says_exactly_what_is_left(tmp_path):
    settings = make_settings(tmp_path, port=9100)
    text = selfhost.setup_text(settings, created=True)
    assert f'_agent.{DOMAIN}  TXT  "v=aid1;u=https://{DOMAIN}/.well-known/agent-keys.json"' in text
    for method, path, _ in selfhost.ROUTES:
        assert f"{method} " in text and path in text
    assert "forward only these routes to 127.0.0.1:9100" in text
    assert f"--domain {DOMAIN}" in text and "--print nginx" in text and "--print systemd" in text
    assert "new signing key created" in text and str(settings.state_dir) in text
    assert "signing key loaded" in selfhost.setup_text(settings, created=False)
    assert f"/connect {DOMAIN}" in text


def test_proxy_upstream_is_a_connectable_address(tmp_path):
    assert make_settings(tmp_path).upstream == "127.0.0.1:9078"
    assert make_settings(tmp_path, bind="0.0.0.0").upstream == "127.0.0.1:9078"
    assert make_settings(tmp_path, bind="::1").upstream == "[::1]:9078"
    assert make_settings(tmp_path, bind="::").upstream == "[::1]:9078"


def locations(config: str) -> list[str]:
    return [line.split()[-2] for line in config.splitlines() if line.startswith("location ")]


def test_nginx_forwards_the_five_routes_and_nothing_else(tmp_path):
    config = selfhost.nginx_config(make_settings(tmp_path))
    assert locations(config) == [
        "/.well-known/agent-keys.json",
        "/relay/v1/health",
        "/relay/v1/ws",
        "/relay/v1/enrollment/",
        "/relay/v1/contact/",
    ]
    assert config.count("proxy_pass http://127.0.0.1:9078;") == 5
    assert config.count("limit_except POST") == 2
    assert config.count("proxy_set_header X-Real-IP $remote_addr;") == 3
    assert 'proxy_set_header Connection "upgrade";' in config
    assert "\nlocation / " not in config


@pytest.mark.skipif(shutil.which("nginx") is None, reason="nginx is not installed")
def test_the_nginx_snippet_passes_nginx_t(tmp_path):
    snippet = tmp_path / "snippet.conf"
    snippet.write_text(selfhost.nginx_config(make_settings(tmp_path)))
    temp = "access_log off;\n" + "".join(
        f"{name}_temp_path {tmp_path}/{name};\n" for name in ("client_body", "proxy", "fastcgi", "uwsgi", "scgi")
    )
    conf = tmp_path / "nginx.conf"
    conf.write_text(
        f"error_log stderr;\npid {tmp_path}/nginx.pid;\nevents {{}}\n"
        f"http {{\n{temp}server {{ listen 127.0.0.1:18443; server_name {DOMAIN}; include {snippet}; }}\n}}\n"
    )
    result = subprocess.run(
        ["nginx", "-t", "-c", str(conf), "-p", f"{tmp_path}/"], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr


def test_caddy_forwards_the_same_five_routes(tmp_path):
    config = selfhost.caddy_config(make_settings(tmp_path, bind="::1"))
    matcher = next(line for line in config.splitlines() if line.strip().startswith("@kollab path"))
    assert matcher.split()[2:] == [path for _, path, _ in selfhost.ROUTES]
    assert "reverse_proxy [::1]:9078" in config and "header_up X-Real-IP {remote_host}" in config
    assert config.splitlines()[2] == f"{DOMAIN} {{" and "respond 404" in config


def test_systemd_unit_runs_this_state_dir_so_the_identity_survives(tmp_path, monkeypatch):
    monkeypatch.setattr(selfhost.getpass, "getuser", lambda: "ops")
    settings = make_settings(tmp_path, port=9100, max_per_source=200)
    unit = selfhost.systemd_unit(settings)
    exec_line = next(line for line in unit.splitlines() if line.startswith("ExecStart="))
    assert exec_line == (
        f"ExecStart={sys.executable} -m kollabor_cli_main relay serve --domain {DOMAIN} "
        f"--state-dir {tmp_path / 'state'} --port 9100 --max-connections-per-source 200"
    )
    assert "User=ops" in unit and f"ReadWritePaths={tmp_path / 'state'}" in unit
    assert "--print" not in exec_line and "Restart=on-failure" in unit
    assert f"kollab-relay-{DOMAIN}.service" in unit
    # A default state dir is spelled out, never left to the service user's own home.
    target = normalize_target(DOMAIN, document=False)
    default = Settings(target=target, state_dir=selfhost.default_state_dir(target))
    assert f"--state-dir {default.state_dir}" in selfhost.systemd_unit(default)


@pytest.mark.parametrize("kind", ["nginx", "caddy", "systemd"])
def test_print_writes_the_config_and_creates_no_state(kind, tmp_path, capsys):
    state = tmp_path / "state"
    assert selfhost.main(["--domain", DOMAIN, "--state-dir", str(state), "--print", kind]) == 0
    assert DOMAIN in capsys.readouterr().out
    assert not state.exists()


# --- keys and state ------------------------------------------------------------------------------


def test_first_run_creates_private_keys_and_a_restart_reuses_them(tmp_path):
    settings = make_settings(tmp_path)
    lock, created = prepare(settings)
    os.close(lock)
    first = manifest_of(settings)
    assert created is True
    assert (settings.state_dir.stat().st_mode & 0o777) == 0o700
    assert ((settings.state_dir / "service.key").stat().st_mode & 0o777) == 0o600
    assert first["discovery"]["roles"] == []  # identity only until the relay listens

    lock, created = prepare(settings)  # the restart
    os.close(lock)
    second = manifest_of(settings)
    assert created is False
    assert second["coordinator"]["public_key"] == first["coordinator"]["public_key"]
    assert second["revision"] > first["revision"]
    verify_manifest(second, settings.target)


def test_a_second_process_on_one_state_directory_is_refused(tmp_path):
    settings = make_settings(tmp_path)
    lock, _ = prepare(settings)
    try:
        with pytest.raises(RuntimeConfigError, match="already served"):
            prepare(settings)
    finally:
        os.close(lock)
    lock, _ = prepare(settings)  # released with the first process
    os.close(lock)


def test_a_state_directory_others_can_read_is_refused_with_its_path(tmp_path):
    settings = make_settings(tmp_path)
    settings.state_dir.mkdir()
    settings.state_dir.chmod(0o755)
    with pytest.raises(RuntimeConfigError, match=re.escape(str(settings.state_dir))):
        prepare(settings)
    assert not (settings.state_dir / "service.key").exists()


def test_the_standalone_publishers_state_directory_is_adopted_as_is(tmp_path):
    """A directory that runs the standalone publisher today keeps its key and revision history."""
    settings = make_settings(tmp_path)
    old = publish(settings.origin, settings.state_dir, tmp_path / "old-public" / "agent-keys.json")
    lock, created = prepare(settings)
    os.close(lock)
    adopted = manifest_of(settings)
    assert created is False
    assert adopted["coordinator"]["public_key"] == old["coordinator"]["public_key"]
    assert adopted["revision"] == old["revision"] + 1


def test_a_state_directory_of_another_domain_is_refused_and_leaves_no_lock_behind(tmp_path):
    settings = make_settings(tmp_path)
    lock, _ = prepare(settings)
    os.close(lock)
    other = make_settings(tmp_path, domain="other.example.com")
    with pytest.raises(ValueError, match="another origin"):
        prepare(other)
    lock, created = prepare(settings)  # the failed start released its lock
    os.close(lock)
    assert created is False


def test_a_lost_key_is_never_replaced(tmp_path):
    settings = make_settings(tmp_path)
    lock, _ = prepare(settings)
    os.close(lock)
    (settings.state_dir / "service.key").unlink()
    with pytest.raises(ValueError, match="key is missing"):
        prepare(settings)


# --- one process: relay + publisher + key file ----------------------------------------------------


@pytest_asyncio.fixture
async def running(tmp_path):
    settings = make_settings(tmp_path)
    lock, _ = prepare(settings)
    app = build_app(settings)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        yield settings, app, client
    finally:
        await client.close()
        os.close(lock)


@pytest.mark.asyncio
async def test_the_key_file_and_the_relay_answer_on_one_port(running):
    settings, app, client = running
    payload = await publish_once(app)

    response = await client.get("/.well-known/agent-keys.json")
    body = await response.read()
    assert response.status == 200
    assert response.content_type == "application/json"
    assert response.headers["Cache-Control"] == "no-store"
    assert body == settings.key_file.read_bytes()
    assert json.loads(body) == payload
    result = verify_manifest(json.loads(body), settings.target)
    assert result.manifest["endpoints"]["control"] == f"https://{DOMAIN}/relay/v1"
    assert result.manifest["discovery"]["roles"] == ["rendezvous", "relay"]
    assert (await client.get("/.well-known/agent-keys")).status == 200  # the documented alias

    health = await client.get("/relay/v1/health")
    assert health.status == 200
    assert (await health.json())["origin"] == f"https://{DOMAIN}"
    for route in ("enrollment", "contact"):
        assert (await client.post(f"/relay/v1/{route}/lookup", json={})).status == 400  # served, and strict


@pytest.mark.asyncio
async def test_the_key_file_is_unavailable_until_it_is_published(tmp_path):
    settings = make_settings(tmp_path)
    settings.state_dir.mkdir(mode=0o700)
    client = TestClient(TestServer(build_app(settings)))
    await client.start_server()
    try:
        assert (await client.get("/.well-known/agent-keys.json")).status == 503
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_the_document_names_the_relay_only_while_it_is_ready(running):
    settings, app, _ = running
    state = app["relay_state"]
    assert (await publish_once(app))["discovery"]["roles"] == ["rendezvous", "relay"]
    state.ready = False
    down = await publish_once(app)
    assert down["discovery"]["roles"] == [] and "control" not in down["endpoints"]
    state.ready, state.shutting_down = True, True
    assert (await publish_once(app))["discovery"]["roles"] == []
    state.shutting_down = False
    assert (await publish_once(app))["discovery"]["roles"] == ["rendezvous", "relay"]
    verify_manifest(manifest_of(settings), settings.target)


@pytest.mark.asyncio
async def test_a_restarted_process_serves_the_same_identity(tmp_path):
    settings = make_settings(tmp_path)
    keys, revisions = [], []
    for _ in range(2):
        lock, _ = prepare(settings)
        app = build_app(settings)
        client = TestClient(TestServer(app))
        await client.start_server()
        try:
            await publish_once(app)
            served = await (await client.get("/.well-known/agent-keys.json")).json()
            verify_manifest(served, settings.target)
            keys.append(served["coordinator"]["public_key"])
            revisions.append(served["revision"])
        finally:
            await client.close()
            os.close(lock)
    assert keys[0] == keys[1] and revisions[1] > revisions[0]


@pytest.mark.asyncio
async def test_renewals_follow_the_relay_and_survive_a_failed_write(running, monkeypatch, capsys):
    _, app, _ = running
    monkeypatch.setattr(selfhost, "PUBLISH_SECONDS", 0.01)
    state, real_publish, calls = app["relay_state"], selfhost.publish, []

    def flaky(*args, **kwargs):
        calls.append(kwargs.get("relay_control"))
        if len(calls) == 2:
            raise OSError("disk full")
        return real_publish(*args, **kwargs)

    monkeypatch.setattr(selfhost, "publish", flaky)
    task = asyncio.create_task(selfhost.keep_published(app, True))
    try:
        await asyncio.sleep(0.3)
        state.ready = False
        await asyncio.sleep(0.3)
        state.ready = True
        await asyncio.sleep(0.3)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    out = capsys.readouterr().out
    assert "publishing failed" in out and "disk full" in out
    assert "relay not ready: key file is identity only" in out
    assert "relay ready again" in out
    assert None in calls and f"https://{DOMAIN}/relay/v1" in calls


# --- the real entry point ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _read_until(process: subprocess.Popen, marker: str, timeout: float = 30) -> str:
    """Read the child's output without buffering ahead of select, until the marker shows."""
    deadline, seen = time.monotonic() + timeout, b""
    while time.monotonic() < deadline and marker.encode() not in seen:
        if select.select([process.stdout], [], [], 0.5)[0]:
            chunk = os.read(process.stdout.fileno(), 4096)
            if not chunk:
                break
            seen += chunk
    assert marker.encode() in seen, f"never saw {marker!r}; output so far:\n{seen.decode(errors='replace')}"
    return seen.decode(errors="replace")


def test_the_real_command_serves_and_a_restart_keeps_the_identity(tmp_path):
    """`python kollabor_cli_main.py relay serve` on a throwaway HOME, a loopback domain and free ports."""
    domain, port = f"localhost:{_free_port()}", _free_port()
    env = {**os.environ, "HOME": str(tmp_path), "KOLLAB_NO_KEYRING": "1", "PYTHONUNBUFFERED": "1"}
    command = [sys.executable, "kollabor_cli_main.py", "relay", "serve", "--domain", domain, "--port", str(port)]
    keys = []
    for run in range(2):
        process = subprocess.Popen(
            command, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0
        )
        try:
            banner = _read_until(process, "ready: relay up")
            assert f'_agent.localhost  TXT  "v=aid1;u=https://{domain}/.well-known/agent-keys.json"' in banner
            assert ("new signing key created" if run == 0 else "signing key loaded") in banner
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/.well-known/agent-keys.json", timeout=5) as reply:
                served = json.loads(reply.read())
            verify_manifest(served, normalize_target(domain))
            keys.append(served["coordinator"]["public_key"])
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/relay/v1/health", timeout=5) as reply:
                assert json.loads(reply.read())["status"] == "ok"
        finally:
            process.send_signal(signal.SIGTERM)
            assert process.wait(timeout=30) == 0
    assert keys[0] == keys[1]
    assert (tmp_path / ".kollab" / "relay" / domain.replace(":", "-") / "service.key").exists()


def test_the_command_refuses_to_start_on_a_port_in_use(tmp_path):
    env = {**os.environ, "HOME": str(tmp_path), "KOLLAB_NO_KEYRING": "1"}
    command = [sys.executable, "kollabor_cli_main.py", "relay", "serve", "--domain", "localhost:9443"]
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        result = subprocess.run(
            [*command, "--port", str(taken.getsockname()[1])],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=60, check=False,
        )  # fmt: skip
    assert result.returncode == 1
    assert "cannot listen" in result.stderr and "Traceback" not in result.stderr
    assert "still to do" not in result.stdout  # no instructions for a server that is not up


def test_serve_dispatches_to_the_directory_and_origin_keeps_the_bare_worker():
    script = """
import sys
import plugins.hub.relay_selfhost as directory
import plugins.hub.relay_service as worker
directory.main = lambda argv: print("directory", argv) or 0
worker.main = lambda argv: print("worker", argv) or 0
import kollabor_cli_main
for argv in (["--domain", "a.example"], ["--origin", "https://a.example"], ["--origin=https://a.example"]):
    sys.argv = ["kollab", "relay", "serve", *argv]
    kollabor_cli_main.cli_main()
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, capture_output=True, text=True, check=True)
    assert result.stdout.splitlines() == [
        "directory ['--domain', 'a.example']",
        "worker ['--origin', 'https://a.example']",
        "worker ['--origin=https://a.example']",
    ]
