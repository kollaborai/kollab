"""Relay supervisor recovery tests, including a private loopback Redis lease."""

from __future__ import annotations

import asyncio
import shutil
import socket
import sys
import tempfile
import time
from pathlib import Path

import pytest

from plugins.hub.relay_backend import (
    RedisRelayBackend,
    RelayBackendError,
    RelayLimits,
    relay_owner_key,
)
from plugins.hub.relay_runtime import RelayRuntime, RuntimeConfig, Worker


def _config() -> RuntimeConfig:
    return RuntimeConfig(
        origin="https://relay.example",
        bind_host="127.0.0.1",
        base_port=19078,
        workers=1,
        health_port=19080,
        node_prefix="relay-test",
        state_dir=Path(tempfile.gettempdir()),
        trusted_proxies=(),
        backend={"mode": "external", "cluster": False},
        limits={"max_connections_per_node": 16},
    )


class _NoopRelayState:
    async def room_changed(self, _room_hash):
        pass

    async def deliver_local(self, _route):
        return False

    async def backend_failed(self):
        pass


@pytest.mark.asyncio
async def test_supervisor_waits_for_expiring_owner_lease():
    config = _config()
    runtime = RelayRuntime(config)
    worker = Worker(1)

    class Backend:
        async def pttl(self, key):
            assert key == relay_owner_key(config.node_id(1))
            return 2_500

    runtime.backend = Backend()
    runtime.backend_ready = True
    before = time.monotonic()

    assert not await runtime._worker_owner_lease_expired(worker)
    assert worker.retry_at >= before + 2.59


def test_startup_deadline_covers_a_previous_owners_lease():
    worker = Worker(1)
    now = time.monotonic()
    worker.retry_at = now + 30  # previous owner's lease still has 30 s left
    deadline = RelayRuntime._extend_first_ready_deadline(now + 45, worker)
    assert deadline >= now + 30 + 45
    # A lease that ends inside the window leaves the deadline alone.
    worker.retry_at = now + 1
    assert RelayRuntime._extend_first_ready_deadline(deadline, worker) == deadline


@pytest.mark.asyncio
async def test_spawned_worker_learns_its_supervisor_pid(monkeypatch):
    import os

    from plugins.hub import relay_runtime

    runtime = RelayRuntime(_config())
    runtime.backend_url = "redis://127.0.0.1:1/0"
    captured = {}

    class Process:
        pid = 4242
        stdout = asyncio.StreamReader()

    async def fake_exec(*args, env=None, **kwargs):
        captured["env"] = env
        Process.stdout.feed_eof()
        return Process()

    monkeypatch.setattr(relay_runtime.asyncio, "create_subprocess_exec", fake_exec)
    worker = Worker(1)
    await runtime._spawn(worker)
    await worker.log_task
    assert captured["env"]["KOLLAB_RELAY_SUPERVISOR_PID"] == str(os.getpid())


@pytest.mark.asyncio
async def test_worker_stops_itself_when_its_supervisor_is_gone(monkeypatch):
    import os
    import signal

    from plugins.hub import relay_service

    sent = []
    monkeypatch.setattr(relay_service.os, "getppid", lambda: 1)  # reparented to init
    monkeypatch.setattr(relay_service.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    await asyncio.wait_for(relay_service._watch_supervisor(98765), timeout=1)
    assert sent == [(os.getpid(), signal.SIGTERM)]


@pytest.mark.asyncio
async def test_supervisor_retries_persistent_lease_without_bypassing_fence():
    runtime = RelayRuntime(_config())
    worker = Worker(1)

    class Backend:
        async def pttl(self, _key):
            return -1

    runtime.backend = Backend()
    runtime.backend_ready = True
    before = time.monotonic()

    assert not await runtime._worker_owner_lease_expired(worker)
    assert worker.retry_at >= before + 29


@pytest.mark.asyncio
async def test_supervisor_allows_restart_only_after_loopback_redis_lease_expires():
    redis_server = shutil.which("redis-server")
    redis_package = pytest.importorskip("redis.asyncio")
    if redis_server is None:
        pytest.skip("redis-server is unavailable")

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

    config = _config()
    node_id = config.node_id(1)
    url = f"redis://127.0.0.1:{port}/0"
    source_root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="relay-owner-redis-") as directory:
        server = await asyncio.create_subprocess_exec(
            redis_server,
            "--bind",
            "127.0.0.1",
            "--port",
            str(port),
            "--protected-mode",
            "yes",
            "--save",
            "",
            "--appendonly",
            "no",
            "--dir",
            directory,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        redis = redis_package.Redis.from_url(url, decode_responses=True)
        owner = None
        try:
            deadline = asyncio.get_running_loop().time() + 5
            while True:
                try:
                    await redis.ping()
                    break
                except Exception:
                    if asyncio.get_running_loop().time() >= deadline:
                        pytest.fail("temporary loopback Redis did not become ready")
                    await asyncio.sleep(0.025)

            child_source = f"""
import asyncio
from plugins.hub import relay_backend
from plugins.hub.relay_backend import RedisRelayBackend, RelayLimits

NODE_ID = {node_id!r}
URL = {url!r}

class State:
    async def room_changed(self, room_hash):
        pass
    async def deliver_local(self, route):
        return False
    async def backend_failed(self):
        pass

async def main():
    relay_backend.LEASE_SECONDS = 2
    backend = RedisRelayBackend(URL, NODE_ID, cluster=False, limits=RelayLimits())
    await backend.start(State())
    print("READY", flush=True)
    await asyncio.Event().wait()

asyncio.run(main())
"""
            child = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                child_source,
                cwd=source_root,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            try:
                ready = await asyncio.wait_for(child.stdout.readline(), timeout=8)
                assert ready.strip() == b"READY", ready.decode(errors="replace")

                runtime = RelayRuntime(config)
                runtime.backend = redis
                runtime.backend_ready = True
                worker = Worker(1)
                assert not await runtime._worker_owner_lease_expired(worker)

                competing = RedisRelayBackend(
                    url, node_id, cluster=False, limits=RelayLimits()
                )
                with pytest.raises(RelayBackendError, match="already active"):
                    await competing.start(_NoopRelayState())

                child.kill()
                await asyncio.wait_for(child.wait(), timeout=5)
                quota_key = RedisRelayBackend._node_quota_key(node_id)
                await redis.zadd(
                    quota_key,
                    {"stale-reservation": int(time.time() * 1000) + 60_000},
                )
                assert await redis.zcard(quota_key) == 1

                lease_key = relay_owner_key(node_id)
                deadline = asyncio.get_running_loop().time() + 5
                while int(await redis.pttl(lease_key)) != -2:
                    if asyncio.get_running_loop().time() >= deadline:
                        pytest.fail("abruptly dead worker lease did not expire")
                    await asyncio.sleep(0.025)

                assert await runtime._worker_owner_lease_expired(worker)
                owner = RedisRelayBackend(
                    url, node_id, cluster=False, limits=RelayLimits()
                )
                await owner.start(_NoopRelayState())
                assert await redis.zcard(quota_key) == 0
            finally:
                if child.returncode is None:
                    child.kill()
                    await asyncio.wait_for(child.wait(), timeout=5)
        finally:
            if owner is not None:
                await owner.close()
            await redis.aclose()
            if server.returncode is None:
                server.terminate()
                try:
                    await asyncio.wait_for(server.wait(), timeout=5)
                except TimeoutError:
                    server.kill()
                    await server.wait()
