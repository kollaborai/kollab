"""One-command directory: ``kollab relay serve --domain <domain>``.

One process runs the relay, keeps the signed discovery document published and
serves it (the key file). The operator adds one DNS TXT record and points a TLS
proxy at the port; nothing touches kollabor.ai.

The relay is one worker on its in-memory backend. The managed Valkey sidecar
never persisted anything either: presence rebuilds when devices reconnect, and
a join code or knock that was waiting ends with the process. What has to survive
a restart is the publisher identity, so the signing key and the revision counter
live in the state directory and are reused by ``publish`` unchanged. For several
workers or hosts keep using ``kollab relay run --config``.
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import getpass
import ipaddress
import os
import secrets
import shlex
import signal
import sys
from dataclasses import dataclass
from pathlib import Path

from aiohttp import web

from .dns.discovery import DOCUMENT_PATHS, WELL_KNOWN, DiscoveryError, DiscoveryTarget, normalize_target
from .dns.discovery_publish import publish
from .relay_backend import RelayLimits
from .relay_runtime import RuntimeConfigError, _private_directory
from .relay_service import (
    MAX_CONNECTIONS_PER_NODE,
    MAX_CONNECTIONS_PER_ROOM,
    MAX_CONNECTIONS_PER_SOURCE,
    RelayConfig,
    _parse_trusted_proxy,
    create_app,
)

DEFAULT_BIND = "127.0.0.1"
DEFAULT_PORT = 9078
PUBLISH_SECONDS = 60
# The five routes a TLS proxy forwards. Nothing else (metrics included) is public.
ROUTES = (
    ("GET", WELL_KNOWN, "key file"),
    ("GET", "/relay/v1/health", "health"),
    ("WS", "/relay/v1/ws", "websocket"),
    ("POST", "/relay/v1/enrollment/*", "join codes"),
    ("POST", "/relay/v1/contact/*", "knocks"),
)


@dataclass(frozen=True)
class Settings:
    target: DiscoveryTarget
    state_dir: Path
    bind: str = DEFAULT_BIND
    port: int = DEFAULT_PORT
    trusted_proxies: tuple[str, ...] = ()
    max_per_room: int = MAX_CONNECTIONS_PER_ROOM
    max_per_source: int = MAX_CONNECTIONS_PER_SOURCE

    @property
    def origin(self) -> str:
        return self.target.origin

    @property
    def domain(self) -> str:
        """What people type after /connect: the origin without its scheme."""
        return self.origin.removeprefix("https://")

    @property
    def key_file(self) -> Path:
        return self.state_dir / "public" / "agent-keys.json"

    @property
    def upstream(self) -> str:
        """The host:port a proxy connects to."""
        host = {"0.0.0.0": "127.0.0.1", "::": "::1"}.get(self.bind, self.bind)
        return f"[{host}]:{self.port}" if ":" in host else f"{host}:{self.port}"


def say(message: str) -> None:
    print(message, flush=True)


def default_state_dir(target: DiscoveryTarget) -> Path:
    name = target.origin.removeprefix("https://").replace(":", "-")
    return Path.home() / ".kollab" / "relay" / name


def default_trusted(bind: str) -> tuple[str, ...]:
    """A proxy on this machine connects from loopback; it is the only default."""
    return ("127.0.0.1", "::1") if ipaddress.ip_address(bind).is_loopback else ()


def txt_value(settings: Settings) -> str:
    return f"v=aid1;u={settings.origin}{WELL_KNOWN}"


def flags(settings: Settings, *, state: bool = False) -> list[str]:
    """The options that recreate these settings; defaults are left out unless state=True names the state dir."""
    out = ["--domain", settings.domain]
    if state or settings.state_dir != default_state_dir(settings.target):
        out += ["--state-dir", str(settings.state_dir)]
    if settings.bind != DEFAULT_BIND:
        out += ["--bind", settings.bind]
    if settings.port != DEFAULT_PORT:
        out += ["--port", str(settings.port)]
    if settings.trusted_proxies != default_trusted(settings.bind):
        for proxy in settings.trusted_proxies:
            out += ["--trusted-proxy", proxy]
    if settings.max_per_room != MAX_CONNECTIONS_PER_ROOM:
        out += ["--max-connections-per-room", str(settings.max_per_room)]
    if settings.max_per_source != MAX_CONNECTIONS_PER_SOURCE:
        out += ["--max-connections-per-source", str(settings.max_per_source)]
    return out


def setup_text(settings: Settings, *, created: bool) -> str:
    """What the operator still has to do, printed once the port is listening."""
    command = shlex.join(["kollab", "relay", "serve", *flags(settings)])
    routes = [f"       {method:<5} {path:<32} {note}" for method, path, note in ROUTES]
    return "\n".join(
        [
            f"kollab relay serve: {settings.domain}",
            f"  state    {settings.state_dir}",
            f"           {'new signing key created' if created else 'signing key loaded'}; back this directory up",
            f"  listen   http://{settings.upstream}  (plain HTTP, behind your TLS proxy)",
            f"  proxies  X-Real-IP trusted from {' '.join(settings.trusted_proxies) or 'none'}",
            "",
            "still to do, once:",
            "  1. DNS: add this TXT record",
            f'       _agent.{settings.target.authority}  TXT  "{txt_value(settings)}"',
            f"  2. TLS proxy: terminate TLS for {settings.domain} and forward only these routes to {settings.upstream}",
            *routes,
            f"     paste-ready: {command} --print nginx   (or --print caddy)",
            f"  3. keep it running: {command} --install   (systemd; --print systemd shows the unit)",
            "",
            f"then devices connect with /connect {settings.domain}",
        ]
    )


_NGINX = """\
# Kollab directory for @DOMAIN@. Put these inside the TLS server block for that name.
# Only these five routes are forwarded; /relay/v1/metrics and everything else stay private.
location = /.well-known/agent-keys.json {
    proxy_pass http://@UPSTREAM@;
    proxy_set_header Host $host;
}
location = /relay/v1/health {
    proxy_pass http://@UPSTREAM@;
    proxy_set_header Host $host;
}
location = /relay/v1/ws {
    proxy_pass http://@UPSTREAM@;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_read_timeout 75s;
    proxy_send_timeout 10s;
    proxy_buffering off;
}
location /relay/v1/enrollment/ {
    limit_except POST { deny all; }
    proxy_pass http://@UPSTREAM@;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}
location /relay/v1/contact/ {
    limit_except POST { deny all; }
    proxy_pass http://@UPSTREAM@;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}
"""

_CADDY = """\
# Kollab directory for @DOMAIN@. Only these five routes are forwarded;
# /relay/v1/metrics and everything else stay private.
@DOMAIN@ {
    @kollab path /.well-known/agent-keys.json /relay/v1/health /relay/v1/ws /relay/v1/enrollment/* /relay/v1/contact/*
    handle @kollab {
        reverse_proxy @UPSTREAM@ {
            header_up X-Real-IP {remote_host}
        }
    }
    handle {
        respond 404
    }
}
"""

_SYSTEMD = """\
# Kollab directory for @DOMAIN@ as a systemd service, run by @USER@ with the state directory this command uses now.
# Install:  @COMMAND@ --install   (writes this file, enables and starts it; --uninstall removes it)
# or:       @COMMAND@ --print systemd | sudo tee /etc/systemd/system/@UNIT@.service
#           sudo systemctl daemon-reload && sudo systemctl enable --now @UNIT@
[Unit]
Description=Kollab directory for @DOMAIN@
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=@USER@
ExecStart=@EXEC@
Restart=on-failure
RestartSec=5
UMask=0077
LimitNOFILE=65536
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=@STATE@
PrivateTmp=true

[Install]
WantedBy=multi-user.target
"""


def _render(template: str, **values: str) -> str:
    for name, value in values.items():
        template = template.replace(f"@{name}@", value)
    return template


def nginx_config(settings: Settings) -> str:
    return _render(_NGINX, DOMAIN=settings.domain, UPSTREAM=settings.upstream)


def caddy_config(settings: Settings) -> str:
    return _render(_CADDY, DOMAIN=settings.domain, UPSTREAM=settings.upstream)


def unit_name(settings: Settings) -> str:
    return "kollab-relay-" + settings.domain.replace(":", "-")


def systemd_unit(settings: Settings) -> str:
    """A unit that runs as this user on the same state directory, so the identity survives the move to systemd."""
    try:
        user = getpass.getuser()
    except (KeyError, OSError):
        user = "kollab"
    command = shlex.join(["kollab", "relay", "serve", *flags(settings)])
    argv = [sys.executable, "-m", "kollabor_cli_main", "relay", "serve", *flags(settings, state=True)]
    return _render(
        _SYSTEMD,
        DOMAIN=settings.domain,
        UNIT=unit_name(settings),
        USER=user,
        COMMAND=command,
        EXEC=shlex.join(argv),
        STATE=str(settings.state_dir),
    )


PRINTABLE = {"nginx": nginx_config, "caddy": caddy_config, "systemd": systemd_unit}


def _lock(state_dir: Path) -> int:
    """One process per state directory; the descriptor is held until exit."""
    fd = os.open(state_dir / "serve.lock", os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        raise RuntimeConfigError(f"{state_dir} is already served by another kollab relay serve") from None
    return fd


def prepare(settings: Settings) -> tuple[int, bool]:
    """Private state directory, the lock, and the signing key (made on first run).

    Publishing identity-only here is what creates or validates the key, so a
    bad state directory stops the command before it listens.
    """
    try:
        _private_directory(settings.state_dir, create=True)
    except RuntimeConfigError as exc:
        raise RuntimeConfigError(f"{settings.state_dir}: {exc}") from None
    lock = _lock(settings.state_dir)
    created = not (settings.state_dir / "service.key").exists()
    try:
        publish(settings.origin, settings.state_dir, settings.key_file)
    except BaseException:
        os.close(lock)
        raise
    return lock, created


def relay_ready(app: web.Application) -> bool:
    state = app["relay_state"]
    return state.ready and state.backend.is_ready and not state.shutting_down


async def publish_once(app: web.Application, *, stopping: bool = False) -> dict:
    """Sign and write the document; it advertises the relay only while the relay is ready and not stopping."""
    settings: Settings = app["settings"]
    control = settings.origin + "/relay/v1" if relay_ready(app) and not stopping else None
    return await asyncio.to_thread(
        publish, settings.origin, settings.state_dir, settings.key_file, relay_control=control
    )


async def key_file(request: web.Request) -> web.Response:
    settings: Settings = request.app["settings"]
    try:
        body = settings.key_file.read_bytes()
    except OSError:
        raise web.HTTPServiceUnavailable(text="the discovery document is not published yet") from None
    return web.Response(body=body, content_type="application/json", headers={"Cache-Control": "no-store"})


def build_app(settings: Settings) -> web.Application:
    """The relay app plus the key file route on the same port."""
    config = RelayConfig(
        origin=settings.origin,
        node_id=secrets.token_hex(16),
        dev_in_memory=True,
        bind=settings.bind,
        port=settings.port,
        trusted_proxies=frozenset(ipaddress.ip_address(proxy) for proxy in settings.trusted_proxies),
        limits=RelayLimits(
            max_connections_per_node=MAX_CONNECTIONS_PER_NODE,
            max_connections_per_room=settings.max_per_room,
            max_connections_per_source=settings.max_per_source,
        ),
    )
    app = create_app(config)
    app["settings"] = settings
    for path in sorted(DOCUMENT_PATHS):
        app.router.add_get(path, key_file)
    return app


async def keep_published(app: web.Application, advertised: bool) -> None:
    """Renew the document every minute (it expires in five), tracking whether it names the relay."""
    while not app["relay_state"].shutting_down:
        await asyncio.sleep(PUBLISH_SECONDS)
        try:
            payload = await publish_once(app)
        except Exception as exc:  # a renewal that fails must not end the loop; the next minute tries again
            say(f"publishing failed, the key file will expire in five minutes: {exc}")
            continue
        now = bool(payload["discovery"]["roles"])
        if now != advertised:
            say("relay ready again: the key file names it" if now else "relay not ready: key file is identity only")
            advertised = now


async def serve(settings: Settings, *, created: bool) -> None:
    app = build_app(settings)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    task = None
    try:
        try:
            await web.TCPSite(runner, settings.bind, settings.port).start()
        except OSError as exc:
            raise RuntimeConfigError(f"cannot listen: {exc.strerror or exc}") from None
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        say(setup_text(settings, created=created))
        payload = await publish_once(app)
        advertised = bool(payload["discovery"]["roles"])
        say(
            f"\nready: relay up, key file published (revision {payload['revision']})"
            if advertised
            else "\nnot ready: the relay is not answering yet; the key file is identity only"
        )
        task = asyncio.create_task(keep_published(app, advertised))
        await stop.wait()
    finally:
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            try:  # a stopped relay must not stay named in the key file until it expires
                await publish_once(app, stopping=True)
            except Exception as exc:
                say(f"publishing failed, the key file will name this relay for up to five minutes: {exc}")
        await runner.cleanup()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kollab relay serve",
        description="Run a directory on your own domain: the relay, its signed discovery document and the "
        "key file, in one process. It prints the DNS record and the proxy routes it still needs.",
        epilog="Several workers or hosts: kollab relay run --config <file>.",
    )
    parser.add_argument("--domain", required=True, help="what people type after /connect, e.g. agents.example.com")
    parser.add_argument(
        "--state-dir", type=Path, help="signing key and revision counter; back it up (default ~/.kollab/relay/<domain>)"
    )
    parser.add_argument(
        "--bind", default=DEFAULT_BIND, help="literal listen address; the TLS proxy connects here (default loopback)"
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--trusted-proxy",
        action="append",
        type=_parse_trusted_proxy,
        metavar="IP",
        help="proxy address allowed to send X-Real-IP (repeatable; default loopback when listening on loopback)",
    )
    parser.add_argument(
        "--max-connections-per-source",
        type=int,
        default=MAX_CONNECTIONS_PER_SOURCE,
        help="raise it for an office behind one address",
    )
    parser.add_argument("--max-connections-per-room", type=int, default=MAX_CONNECTIONS_PER_ROOM)
    once = parser.add_mutually_exclusive_group()
    once.add_argument("--print", choices=sorted(PRINTABLE), help="print that config for these settings and exit")
    once.add_argument(
        "--install",
        action="store_true",
        help="install the --print systemd unit and start it: the directory runs at boot and after a crash",
    )
    once.add_argument("--uninstall", action="store_true", help="stop and remove that systemd unit")
    return parser


def settings_from(args: argparse.Namespace) -> Settings:
    """Validated settings; raises ValueError with a message fit for the operator."""
    try:
        target = normalize_target(args.domain, document=False)
    except DiscoveryError as exc:
        raise ValueError(f"--domain: {exc}") from None
    if target.explicit:
        raise ValueError("--domain takes a domain, not a URL with a path")
    try:
        ipaddress.ip_address(args.bind)
    except ValueError:
        raise ValueError("--bind must be a literal IP address") from None
    if not 1 <= args.port <= 65535:
        raise ValueError("--port must be in 1..65535")
    RelayLimits(  # its ValueError names the offending limit
        max_connections_per_room=args.max_connections_per_room,
        max_connections_per_source=args.max_connections_per_source,
    )
    return Settings(
        target=target,
        state_dir=(args.state_dir or default_state_dir(target)).expanduser().absolute(),
        bind=args.bind,
        port=args.port,
        trusted_proxies=tuple(str(proxy) for proxy in args.trusted_proxy)
        if args.trusted_proxy
        else default_trusted(args.bind),
        max_per_room=args.max_connections_per_room,
        max_per_source=args.max_connections_per_source,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        settings = settings_from(args)
    except ValueError as exc:
        parser.error(str(exc))
    if args.print:
        sys.stdout.write(PRINTABLE[args.print](settings))
        return 0
    if args.install or args.uninstall:
        return _install(settings, remove=args.uninstall)
    try:
        lock, created = prepare(settings)
        try:
            asyncio.run(serve(settings, created=created))
        finally:
            os.close(lock)
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"kollab relay serve: {exc}", file=sys.stderr)
        return 1
    return 0


def _install(settings: Settings, *, remove: bool) -> int:
    """`--install` / `--uninstall`: the systemd unit `--print systemd` shows (kollabor.service)."""
    from kollabor.service import has_systemd, install_systemd, uninstall_systemd

    name = unit_name(settings)
    if not has_systemd():
        flag = "--uninstall" if remove else "--install"
        print(f"kollab relay serve: {flag} needs systemd; see --print systemd", file=sys.stderr)
        return 1
    if remove:
        print(f"removed {name}" if uninstall_systemd(name) else f"{name} is not installed")
        return 0
    try:
        # The unit may write only the state directory, so it must exist with its key first;
        # this also refuses a state directory another relay serve is using.
        os.close(prepare(settings)[0])
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print(f"kollab relay serve: {exc}", file=sys.stderr)
        return 1
    install_systemd(name, systemd_unit(settings))
    print(f"installed {name}: starts at boot, restarts 5 s after a failure")
    print(f"  check:  systemctl status {name}   then curl {settings.origin}/relay/v1/health")
    print(f"  logs:   journalctl -u {name}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
