"""Tests for kollabor_config.local_ipc (kollab #268): unix sockets on POSIX, handshaked loopback TCP on Windows."""

import asyncio
import hashlib
import hmac
import importlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import types
from pathlib import Path

import pytest

# Import local_ipc as a standalone package, so kollabor_config/__init__.py (the whole config stack) does not run.
# The bare Windows job (windows-locks.yml) skips installing it. The name is private so no other test sees it.
_SRC = (
    Path(__file__).resolve().parents[2] / "packages/kollabor-config/src/kollabor_config"
)
_PKG = "kollabor_config_local_ipc_under_test"
_pkg = types.ModuleType(_PKG)
_pkg.__path__ = [str(_SRC)]
sys.modules[_PKG] = _pkg
local_ipc = importlib.import_module(f"{_PKG}.local_ipc")

posix_only = pytest.mark.skipif(os.name == "nt", reason="unix sockets are POSIX only")
windows_only = pytest.mark.skipif(
    os.name != "nt", reason="loopback TCP handshake is Windows only"
)

# A child that connects with connect_blocking, then sleeps. argv: package dir, endpoint path.
_CHILD = """
import importlib, sys, time, types
pkg = types.ModuleType("local_ipc_child")
pkg.__path__ = [sys.argv[1]]
sys.modules[pkg.__name__] = pkg
ipc = importlib.import_module("local_ipc_child.local_ipc")
sock = ipc.connect_blocking(sys.argv[2], 10)
time.sleep(60)
"""


@pytest.fixture
def work_dir():
    # macOS caps unix socket paths at 104 bytes, so POSIX uses a short folder under /tmp.
    path = tempfile.mkdtemp(dir="/tmp") if os.name == "posix" else tempfile.mkdtemp()
    yield Path(path)
    shutil.rmtree(path, ignore_errors=True)


async def _echo(reader, writer):
    writer.write(await reader.readline())
    await writer.drain()
    writer.close()


async def _round_trip(path: Path) -> bytes:
    endpoint = await local_ipc.serve(_echo, path)
    try:
        reader, writer = await local_ipc.connect(path)
        writer.write(b"hello\n")
        await writer.drain()
        line = await reader.readline()
        writer.close()
        return line
    finally:
        endpoint.close()


@posix_only
def test_serve_connect_round_trip_over_unix_socket(work_dir):
    assert asyncio.run(_round_trip(work_dir / "ep.sock")) == b"hello\n"


@posix_only
def test_handler_sees_the_peer_pid_and_uid(work_dir):
    seen = []

    async def handler(reader, writer):
        seen.append((local_ipc.peer_pid(writer), local_ipc.peer_is_me(writer)))
        writer.close()

    async def main():
        endpoint = await local_ipc.serve(handler, work_dir / "ep.sock")
        try:
            reader, writer = await local_ipc.connect(work_dir / "ep.sock")
            await reader.read()  # the handler closes its end, so this returns at EOF
            writer.close()
        finally:
            endpoint.close()

    asyncio.run(main())
    assert seen == [(os.getpid(), True)]


@posix_only
def test_connect_to_missing_path_fails(work_dir):
    with pytest.raises((FileNotFoundError, ConnectionRefusedError)):
        asyncio.run(local_ipc.connect(work_dir / "missing.sock"))


def _blocking_echo(path: Path) -> bytes:
    sock = local_ipc.connect_blocking(path, 5)
    with sock, sock.makefile("rb") as f:
        sock.sendall(b"blocking\n")
        return f.readline()


@posix_only
def test_connect_blocking_works_against_serve(work_dir):
    async def main():
        endpoint = await local_ipc.serve(_echo, work_dir / "ep.sock")
        try:
            return await asyncio.to_thread(_blocking_echo, work_dir / "ep.sock")
        finally:
            endpoint.close()

    assert asyncio.run(main()) == b"blocking\n"


@windows_only
def test_serve_connect_round_trip_over_loopback(work_dir):
    assert asyncio.run(_round_trip(work_dir / "ep.json")) == b"hello\n"


@windows_only
def test_corrupted_key_is_refused(work_dir):
    path = work_dir / "ep.json"

    async def main():
        endpoint = await local_ipc.serve(_echo, path)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            record["key"] = "00" * 32
            path.write_text(json.dumps(record), encoding="utf-8")
            with pytest.raises(ConnectionRefusedError):
                await local_ipc.connect(path)
        finally:
            endpoint.close()

    asyncio.run(main())


def _raw_client(port: int) -> bytes:
    # Reads N_s, then answers with N_s as N_c and 32 zero bytes as the MAC. No proof must come back.
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        f = sock.makefile("rb")
        n_s = f.read(32)
        sock.sendall(n_s + bytes(32))
        return f.read()  # reads until EOF


@windows_only
def test_raw_client_gets_eof_and_no_server_proof(work_dir):
    path = work_dir / "ep.json"

    async def main():
        endpoint = await local_ipc.serve(_echo, path)
        try:
            port = json.loads(path.read_text(encoding="utf-8"))["port"]
            return await asyncio.to_thread(_raw_client, port)
        finally:
            endpoint.close()

    assert asyncio.run(main()) == b""


def _reflect_client(port: int, key: bytes) -> bytes:
    # Sends N_s back as N_c, with the server-labelled proof offered as the client's. No proof must come back.
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sock:
        f = sock.makefile("rb")
        n_s = f.read(32)
        proof = hmac.new(key, b"kollab-ipc/server" + n_s + n_s, hashlib.sha256).digest()
        sock.sendall(n_s + proof)
        try:
            return f.read()  # reads until EOF
        except ConnectionError:
            return b""


@windows_only
def test_reflected_server_label_is_refused(work_dir):
    path = work_dir / "ep.json"

    async def main():
        endpoint = await local_ipc.serve(_echo, path)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            key = bytes.fromhex(record["key"])
            return await asyncio.to_thread(_reflect_client, record["port"], key)
        finally:
            endpoint.close()

    assert asyncio.run(main()) == b""


@windows_only
def test_dead_pid_in_endpoint_file_is_refused(work_dir):
    path = work_dir / "ep.json"
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()

    async def main():
        endpoint = await local_ipc.serve(_echo, path)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            record["pid"] = child.pid
            path.write_text(json.dumps(record), encoding="utf-8")
            with pytest.raises(ConnectionRefusedError):
                await local_ipc.connect(path)
        finally:
            endpoint.close()

    asyncio.run(main())


@windows_only
def test_peer_pid_is_the_connecting_child(work_dir):
    path = work_dir / "ep.json"

    async def main():
        loop = asyncio.get_running_loop()
        peer = loop.create_future()

        async def handler(reader, writer):
            peer.set_result(local_ipc.peer_pid(writer))
            writer.close()

        endpoint = await local_ipc.serve(handler, path)
        child = subprocess.Popen([sys.executable, "-c", _CHILD, str(_SRC), str(path)])
        try:
            got = await asyncio.wait_for(peer, 30)
            return child.pid, got
        finally:
            child.kill()
            child.wait()
            endpoint.close()

    child_pid, got = asyncio.run(main())
    assert got == child_pid
