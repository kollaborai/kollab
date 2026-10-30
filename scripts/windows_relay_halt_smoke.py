"""A network halt end to end on the Windows runner: two homes over a local relay.

Two throwaway homes (win-a, win-b) join a local relay (``kollab relay serve`` behind a
throwaway-CA TLS front). A counting-machine confirmation lets either machine halt the
network; a halt run from win-a stops win-b's agents too, and a resume win-b proposes and
win-a confirms restarts both. The halt stops every kollab of the install, so this runs
only on CI.
"""

import argparse
import asyncio
import datetime
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOMAIN = "relay.win-smoke.test"
RELAY_PORT = 18778
TLS_PORT = 443
AGENT_ONLINE = re.compile(r"^\s+(?P<name>\S+).*\bpid=(?P<pid>\d+)", re.MULTILINE)


def smoke_env(**extra: str) -> dict[str, str]:
    """This environment minus any binary identity, which would make one kollab act as another."""
    build_env = {"KOLLAB_BUILD_BRANCH", "KOLLAB_BUILD_SHA"}
    env = {key: value for key, value in os.environ.items() if key not in {"SCIE", "SCIE_ARGV0", "PEX"} | build_env}
    env["KOLLAB_NO_KEYRING"] = "1"
    return {**env, **extra}


def status_online(kollab: str, project: Path, env: dict[str, str]) -> bool:
    """Print `kollab --hub status` and say whether an agent is online in it."""
    status = subprocess.run(
        [kollab, "--hub", "status"], cwd=project, env=env, capture_output=True, text=True, timeout=60
    )
    print(status.stdout.rstrip() or f"(no output, exit {status.returncode})")
    return any(not m["name"].startswith("halted-") for m in AGENT_ONLINE.finditer(status.stdout))


def start_agent(kollab: str, project: Path, env: dict[str, str], log: Path) -> subprocess.Popen:
    """Launch `kollab --detached`: it starts a detached child and exits; under a halt that child is the waiter."""
    with log.open("ab") as stream:
        return subprocess.Popen(
            [kollab, "--detached"], cwd=project, env=env, stdin=subprocess.DEVNULL, stdout=stream, stderr=stream
        )


def wait_until(check, timeout: float, what: str) -> None:
    """Poll `check` every 2 s until it is true; past `timeout`, name what never happened."""
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() > deadline:
            raise SystemExit(f"windows relay halt smoke: not within {timeout:.0f}s: {what}")
        time.sleep(2)


# ------------------------------------------------------------- hub sockets


def folder_socket(home: Path, project: Path) -> str | None:
    """This home's live daemon socket for `project`, read straight from the presence folder."""
    for record in sorted((home / ".kollab" / "hub" / "presence").glob("*.json")):
        try:
            data = json.loads(record.read_text())
            os.kill(int(data["pid"]), 0)
        except (OSError, ValueError, KeyError):
            continue
        if data.get("project") == str(project) and data.get("socket_path"):
            return data["socket_path"]
    return None


async def open_stream(path: str):
    """A hub socket stream: local_ipc on nt (loopback + endpoint file), a unix socket elsewhere."""
    if os.name == "nt":
        from kollabor_config import local_ipc

        return local_ipc.connect(path)
    return await asyncio.open_unix_connection(path)


async def rpc(path: str, method: str, params: dict, timeout: float = 90) -> dict:
    """One `rpc_request` over a hub socket, the proxy.py harness op."""
    reader, writer = await open_stream(path)
    request = {
        "action": "rpc_request", "request_id": f"rs{time.monotonic_ns()}",
        "method": method, "params": params, "timeout": timeout,
    }
    writer.write((json.dumps(request) + "\n").encode())
    await writer.drain()
    reply = json.loads(await asyncio.wait_for(reader.readline(), timeout + 10))
    writer.close()
    return reply


async def cproxy(path: str, op: str, args: dict, timeout: float = 40) -> dict:
    """One connect_proxy op over a hub socket (offer, decide, snapshot)."""
    from plugins.hub.messenger import AgentMessenger

    return await AgentMessenger.request_connect_proxy(path, op, args, timeout=timeout)


async def halt_act(home: Path, project: Path, action: str, payload: dict) -> dict:
    """One halt op for `project` through its halt listener when one runs, else its live owner."""
    from plugins.hub.halt_context import HaltContext

    return await HaltContext(str(project), home / ".kollab").hub_halt_act(action, payload)


async def halt_members(home: Path, project: Path) -> list:
    from plugins.hub.halt_context import HaltContext

    return await HaltContext(str(project), home / ".kollab").hub_halt_members()


def start_listener(kollab: str, project: Path, env: dict[str, str]) -> None:
    """Run `kollab --halt-listener` for this home; a halted machine's halt ops go through it."""
    subprocess.Popen(
        [kollab, "--halt-listener"], cwd=project, env=env,
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


# ------------------------------------------------------------- the TLS front


def make_certs(host: str, out: Path) -> None:
    """A throwaway CA and leaf for `host`, the tls_proxy.py proof recipe."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    now = datetime.datetime.now(datetime.UTC)
    ca_key, key = ec.generate_private_key(ec.SECP256R1()), ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "windows relay smoke CA")])
    ca = (
        x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
        .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now).not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    leaf = (
        x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
        .issuer_name(ca_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now).not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    pem = serialization.Encoding.PEM
    out.mkdir(parents=True, exist_ok=True)
    (out / "ca.pem").write_bytes(ca.public_bytes(pem))
    (out / "cert.pem").write_bytes(leaf.public_bytes(pem))
    (out / "key.pem").write_bytes(
        key.private_bytes(pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )


async def _pipe(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        writer.close()


async def _tls_front(certs: Path) -> None:
    """TLS on 127.0.0.1:443 for DOMAIN in front of the relay's plain HTTP port.

    The relay itself speaks plain HTTP on a loopback port (plugins/hub/relay_selfhost.py
    serve() mounts a plain TCPSite), but discovery is https-only (plugins/hub/dns/
    discovery.py normalizes every origin to https and dials wss), so this smoke fronts
    it exactly the way tests/docker/kill_switch/tls_proxy.py does.
    """
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.load_cert_chain(certs / "cert.pem", certs / "key.pem")

    async def handle(down):
        try:
            up = await asyncio.open_connection("127.0.0.1", RELAY_PORT)
        except OSError:
            down.close()
            return
        await asyncio.gather(_pipe(down, up[1]), _pipe(up[0], down))

    server = await asyncio.start_server(handle, "127.0.0.1", TLS_PORT, ssl=ctx)
    async with server:
        await server.serve_forever()


HOSTS = Path(r"C:\Windows\System32\drivers\etc\hosts")
HOSTS_LINE = f"127.0.0.1 {DOMAIN}"


def hosts_add() -> None:
    """Point DOMAIN at loopback; GitHub's Windows runner runs elevated."""
    text = HOSTS.read_text(errors="replace")
    if HOSTS_LINE not in text:
        with HOSTS.open("a") as stream:
            stream.write(f"\n{HOSTS_LINE}\n")


def hosts_remove() -> None:
    try:
        lines = [line for line in HOSTS.read_text(errors="replace").splitlines() if line.strip() != HOSTS_LINE]
        HOSTS.write_text("\n".join(lines) + "\n")
    except OSError:
        pass


def kill_home_processes(home: Path) -> None:
    """End every process whose environment names this throwaway home as USERPROFILE."""
    import psutil

    from kollabor_config import proc_ctl

    for proc in psutil.process_iter(["pid", "environ"]):
        try:
            if proc.info["pid"] != os.getpid() and (proc.info.get("environ") or {}).get("USERPROFILE") == str(home):
                proc_ctl.kill_tree(proc.info["pid"], proc_ctl.HARD)
        except Exception:
            pass


# ------------------------------------------------------------- the smoke


async def join_networks(home_a: Path, home_b: Path, project_a: Path, project_b: Path) -> None:
    """Steps 4-6: win-a attaches, offers a code; win-b enrolls, win-a accepts; win-b joins."""
    sock_a = folder_socket(home_a, project_a)
    print(f"step 4: win-a attaches to {DOMAIN}")
    reply = await rpc(sock_a, "state.hub_connect", {"command": DOMAIN}, timeout=180)
    print(f"connect: {json.dumps(reply)[:400]}")

    async def offered() -> dict | None:
        try:
            result = await cproxy(sock_a, "offer", {"domain": DOMAIN})
        except Exception as exc:  # offer only works once the network is attached: retry
            print(f"offer retry: {type(exc).__name__}: {exc}")
            return None
        return result if result.get("status") == "offered" else print(f"offer: {json.dumps(result)[:200]}") or None

    code = None
    deadline = time.monotonic() + 180
    while code is None:
        result = await offered()
        if result:
            code = result["code"]
            print(f"step 5: offer accepted, code minted (expires {result.get('expires_at', '?')})")
        elif time.monotonic() > deadline:
            raise SystemExit("windows relay halt smoke: win-a never offered a code")
        else:
            await asyncio.sleep(2)

    sock_b = folder_socket(home_b, project_b)
    submitted = await rpc(sock_b, "state.hub_enroll", {"domain": DOMAIN, "code": code})
    receipt = submitted.get("receipt_id") if submitted.get("status") == "pending" else None
    if not receipt:
        raise SystemExit(f"win-b enrollment not submitted: {json.dumps(submitted)[:400]}")
    print(f"step 5: win-b submitted the code, receipt {receipt}")

    snapshot = await cproxy(sock_a, "snapshot", {})
    requests = snapshot.get("requests") or []
    if not requests:
        raise SystemExit(f"win-a sees no pending enrollment: {json.dumps(snapshot)[:400]}")
    enrollment_id = requests[0].get("enrollment_id")
    decided = await cproxy(sock_a, "decide", {"enrollment_id": enrollment_id, "decision": "accept"})
    print(f"step 5: win-a accepted enrollment {enrollment_id}: {json.dumps(decided)[:200]}")

    deadline = time.monotonic() + 180
    while True:
        status = await rpc(sock_b, "state.hub_enroll_status", {"receipt_id": receipt})
        if status.get("status") == "approved":
            print(f"step 6: win-b joined: {status.get('detail', '')}")
            return
        if status.get("status") != "pending" or time.monotonic() > deadline:
            raise SystemExit(f"win-b never joined: {json.dumps(status)[:400]}")
        await asyncio.sleep(2)


def relay_halt_smoke(kollab: str, homes: dict, projects: dict, envs: dict, logs: dict) -> None:
    """Steps 4-9: join, halt the network from win-a, resume it win-b to win-a's confirmation."""

    def online(name: str) -> bool:
        return status_online(kollab, projects[name], envs[name])

    def halted(name: str) -> list[Path]:
        return list((homes[name] / ".kollab" / "hub" / "presence").glob("halted-*.json"))

    print(f"step 4: win-a --detached: exit {start_agent(kollab, projects['a'], envs['a'], logs['a']).wait(timeout=60)}")
    wait_until(lambda: folder_socket(homes['a'], projects['a']) is not None, 90, "win-a's daemon socket")
    wait_until(lambda: online("a"), 90, "win-a's agent online in `kollab --hub status`")

    print("step 5: win-b --detached (its hub socket carries the enrollment)")
    start_agent(kollab, projects["b"], envs["b"], logs["b"]).wait(timeout=60)
    wait_until(lambda: folder_socket(homes["b"], projects["b"]) is not None, 90, "win-b's daemon socket")
    asyncio.run(join_networks(homes["a"], homes["b"], projects["a"], projects["b"]))
    wait_until(lambda: online("b"), 90, "win-b's agent online after joining")

    members = asyncio.run(halt_members(homes["a"], projects["a"]))
    if not isinstance(members, list) or len(members) < 2:
        raise SystemExit(f"the network lists fewer than two machines: {json.dumps(members)[:400]}")
    other = next((row.get("machine") for row in members if row.get("name") == "win-b" and row.get("machine")), None)
    if not other:
        raise SystemExit(f"win-a's member list names no win-b machine: {json.dumps(members)[:400]}")
    print(f"step 7: confirming win-b's machine {other[:12]} (a halt needs two counting machines)")
    confirmed = asyncio.run(
        halt_act(homes["a"], projects["a"], "confirm_machine", {"confirmed": True, "machine": other})
    )
    if confirmed.get("halt_error") or confirmed.get("error"):
        raise SystemExit(f"confirm_machine refused: {json.dumps(confirmed)[:400]}")
    counted = asyncio.run(halt_members(homes["a"], projects["a"]))
    counted_b = any(row.get("name") == "win-b" and row.get("counts") for row in counted)
    assert counted_b, f"win-b still does not count: {json.dumps(counted)[:400]}"

    halted_a = asyncio.run(
        halt_act(homes["a"], projects["a"], "halt", {"confirmed": True, "reason": "windows relay smoke"})
    )
    print(f"step 7: win-a halted the network: {json.dumps(halted_a)[:200]}")
    if halted_a.get("halt_error") or halted_a.get("error"):
        raise SystemExit(f"the network halt was refused: {json.dumps(halted_a)[:400]}")
    wait_until(lambda: not online("b"), 90, "win-b's agents stopped by win-a's halt")
    wait_until(lambda: not online("a") and halted("a") and halted("b"), 90, "both machines halted with waiters")

    print("step 8: resume proposed from win-b, confirmed by win-a, both through their halt listeners")
    start_listener(kollab, projects["b"], envs["b"])
    start_listener(kollab, projects["a"], envs["a"])
    wait_until(lambda: (homes["b"] / ".kollab" / "private" / "halt.sock").exists(), 60, "win-b's halt listener socket")
    wait_until(lambda: (homes["a"] / ".kollab" / "private" / "halt.sock").exists(), 60, "win-a's halt listener socket")
    proposal = asyncio.run(halt_act(homes["b"], projects["b"], "propose_resume", {"confirmed": True}))
    print(f"step 8: win-b proposed resume: {json.dumps(proposal)[:200]}")
    order = proposal.get("hash") or ""
    if not order:
        raise SystemExit(f"win-b's resume proposal carries no hash: {json.dumps(proposal)[:400]}")

    deadline = time.monotonic() + 120
    while True:
        confirmed = asyncio.run(
            halt_act(homes["a"], projects["a"], "confirm_resume", {"confirmed": True, "hash": order})
        )
        if not (confirmed.get("halt_error") or confirmed.get("error")) or time.monotonic() > deadline:
            break
        time.sleep(2)
    print(f"step 8: win-a confirmed the resume: {json.dumps(confirmed)[:200]}")

    wait_until(
        lambda: online("a") and online("b") and not halted("a") and not halted("b"),
        120, "both waiters restarted after the resume",
    )
    print("passed windows relay halt smoke: the network halt stopped both machines and the resume restarted them")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--kollab", default=shutil.which("kollab"), help="the kollab to smoke (default: on PATH)")
    args = parser.parse_args()
    if os.environ.get("GITHUB_ACTIONS") != "true":
        print("refusing: this smoke halts every kollab of this install on the machine; it runs only on a CI runner")
        return 2
    if os.name != "nt":
        print("refusing: this smoke proves the Windows paths; run it on the windows-smoke workflow")
        return 2
    if not args.kollab:
        print("no kollab on PATH; pass --kollab PATH")
        return 2
    args.kollab = os.path.abspath(args.kollab)

    sys.path.insert(0, str(ROOT))
    base = Path(tempfile.mkdtemp(prefix="kollab-relay-smoke-"))
    homes, projects, envs, logs = {}, {}, {}, {}
    for name in ("r", "a", "b"):  # the relay gets its own home, so it owns no network
        home = base / name
        (home / ".kollab").mkdir(parents=True)
        homes[name] = home
        envs[name] = smoke_env(HOME=str(home), USERPROFILE=str(home))
        if name != "r":
            projects[name] = home / "project"
            projects[name].mkdir()
            logs[name] = home / "detached.log"

    certs = base / "certs"
    make_certs(DOMAIN, certs)  # the relay speaks plain HTTP; discovery dials https only
    for name in ("a", "b"):  # seed_home.sh's fields, pointed at this throwaway CA and loopback
        (homes[name] / ".kollab" / "connect-guide-seen").touch()
        (homes[name] / ".kollab" / "config.json").write_text(json.dumps({
            "application": {"setup_completed": True},
            "kollabor": {"updates": {"auto_update_enabled": False}},
            "plugins": {"hub": {
                "endpoint_tls_ca": str(certs / "ca.pem"),
                "discovery_private_origins": {"*": ["127.0.0.1/32"]},
                "relay_device_name": f"win-{name}",
            }},
        }))

    relay_log = (base / "relay.log").open("wb")
    relay = subprocess.Popen(  # step 2: `kollab relay serve` on a plain loopback port
        [args.kollab, "relay", "serve", "--domain", DOMAIN, "--port", str(RELAY_PORT)],
        cwd=base, env=envs["r"], stdin=subprocess.DEVNULL, stdout=relay_log, stderr=relay_log,
    )
    front = threading.Thread(target=asyncio.run, args=(_tls_front(certs),), daemon=True)
    try:
        def relay_port_open() -> bool:
            with socket.create_connection(("127.0.0.1", RELAY_PORT), 3):
                return True

        wait_until(relay_port_open, 60, "the relay's plain HTTP port")
        front.start()
        hosts_add()  # step 3: discovery dials https://relay.win-smoke.test, so it must resolve to loopback
        relay_halt_smoke(args.kollab, homes, projects, envs, logs)  # steps 4-9
    except BaseException:
        for path in [*sorted(base.rglob("kollab.log")), base / "relay.log", *logs.values()]:
            if path.exists():
                print(f"--- last 60 lines of {path}")
                print("\n".join(path.read_text(errors="replace").splitlines()[-60:]))
        raise
    finally:
        for name in ("a", "b"):  # every home's kollabs, then the homes themselves
            try:
                stop = [args.kollab, "--hub", "stop", "all"]
                subprocess.run(stop, cwd=projects[name], env=envs[name], capture_output=True, timeout=60)
            except (OSError, subprocess.SubprocessError):
                pass
        relay.terminate()
        for name in ("a", "b", "r"):
            kill_home_processes(homes[name])
        hosts_remove()
        shutil.rmtree(base, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
