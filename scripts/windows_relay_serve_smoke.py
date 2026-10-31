"""`kollab relay serve`, end to end, on a Windows CI runner.

The relay's service.key is created 0o600 and its privacy check must pass on
nt, where ACLs decide and POSIX stat bits never could (kollab #450). This
starts a real `kollab relay serve` on a loopback port in a throwaway state
directory, waits for the ready line, and checks the signing key and the
published key file landed. It fails if serve exits early, prints
key_permissions, or never becomes ready.
"""

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

# A reserved TLD: a syntactically valid domain that can never resolve or
# collide with anything real. The document is signed and written locally.
DOMAIN = "relay-smoke.invalid"
READY = re.compile(r"ready: relay up, key file published")
FATAL = re.compile(r"key_permissions|Traceback|RuntimeConfigError|DiscoveryError")


def smoke_env() -> dict[str, str]:
    """This environment minus any binary identity, which would make one kollab act as another."""
    build_env = {"KOLLAB_BUILD_BRANCH", "KOLLAB_BUILD_SHA"}
    env = {key: value for key, value in os.environ.items() if key not in {"SCIE", "SCIE_ARGV0", "PEX"} | build_env}
    env["KOLLAB_NO_KEYRING"] = "1"
    return env


def free_port() -> int:
    """A loopback port that is free right now; serve binds it next."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def read_lines(stream, lines: list[str]) -> None:
    for line in stream:
        lines.append(line.rstrip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kollab", required=True, help="path to the kollab executable (e.g. .venv/Scripts/kollab.exe)")
    parser.add_argument("--timeout", type=float, default=120.0, help="seconds to wait for the relay to become ready")
    args = parser.parse_args()

    state_dir = Path(tempfile.mkdtemp(prefix="relay-serve-smoke-"))
    try:
        return serve_once(args, state_dir)
    finally:
        shutil.rmtree(state_dir, ignore_errors=True)


def serve_once(args: argparse.Namespace, state_dir: Path) -> int:
    port = free_port()
    command = [
        args.kollab,
        "relay",
        "serve",
        "--domain",
        DOMAIN,
        "--state-dir",
        str(state_dir),
        "--bind",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    print(f"starting: {' '.join(command)}", flush=True)
    proc = subprocess.Popen(
        command,
        env=smoke_env(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
    )
    lines: list[str] = []
    reader = threading.Thread(target=read_lines, args=(proc.stdout, lines), daemon=True)
    reader.start()

    ready = False
    fatal = None
    deadline = time.monotonic() + args.timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            break
        if any(READY.search(line) for line in lines):
            ready = True
            break
        hit = next((line for line in lines if FATAL.search(line)), None)
        if hit:
            fatal = hit.strip()
            break
        time.sleep(0.5)
    if not ready:
        # A relay that never became ready must not stay bound on the runner.
        proc.kill()
    try:
        proc.wait(timeout=30)
    except subprocess.TimeoutExpired:
        pass

    problems: list[str] = []
    if fatal:
        problems.append(f"relay serve printed: {fatal}")
    if not ready:
        problems.append("the 'ready: relay up, key file published' line never appeared")
    key = state_dir / "service.key"
    if not key.is_file():
        problems.append("service.key was not created in the state directory")
    doc = state_dir / "public" / "agent-keys.json"
    try:
        payload = json.loads(doc.read_text(encoding="utf-8"))
        if "discovery" not in payload:
            problems.append("the published key file carries no discovery document")
    except (OSError, ValueError) as exc:
        problems.append(f"the published key file is missing or unreadable: {exc}")

    print("\n".join(lines))
    if problems:
        print("\n".join(problems))
        print("FAILED windows relay serve smoke")
        return 1
    print(
        f"passed windows relay serve smoke: relay ready on 127.0.0.1:{port}, "
        "service.key checked, signed discovery document published"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
