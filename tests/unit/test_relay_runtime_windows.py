"""Windows behavior for relay runtime private state paths (#439).

Every test here is skipped on POSIX, where these paths keep their original
checks byte-for-byte. On Windows each test fails on main's code (os.getuid
and os.O_NOFOLLOW do not exist there) and passes only once the runtime uses
the kollabor_config private_fs ACL helpers.
"""

from __future__ import annotations

import os
from dataclasses import replace

import pytest

from plugins.hub.relay_runtime import (
    ManagedValkey,
    RelayRuntime,
    RuntimeConfig,
    RuntimeConfigError,
    _private_directory,
    _read_private,
    _write_private,
)

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows only")


def _config(state_dir) -> RuntimeConfig:
    return RuntimeConfig(
        origin="https://relay.example",
        bind_host="127.0.0.1",
        base_port=19078,
        workers=1,
        health_port=19080,
        node_prefix="relay-win-test",
        state_dir=state_dir,
        trusted_proxies=(),
        backend={"mode": "external", "cluster": False},
        limits={"max_connections_per_node": 16},
    )


def test_private_directory_creates_and_reaccepts(tmp_path):
    state = _private_directory(tmp_path / "state", create=True)
    assert state.is_dir()
    assert _private_directory(state) == state


def test_private_file_roundtrip(tmp_path):
    state = _private_directory(tmp_path / "state", create=True)
    target = state / "runtime.json"
    _write_private(target, "payload")
    assert _read_private(target) == "payload"


def test_managed_valkey_is_refused_up_front(tmp_path):
    config = replace(_config(tmp_path / "state"), backend={"mode": "managed"})
    with pytest.raises(RuntimeConfigError, match="external"):
        ManagedValkey(config)


def test_run_stops_on_console_signals(tmp_path, monkeypatch):
    """RelayRuntime.run() reaches its stop wait on the Proactor loop and stops on SIGINT (#439).

    On Windows add_signal_handler raises NotImplementedError, so the unfixed
    run dies before its health listener; with the fix the stop handlers
    install through signal.signal and a raised SIGINT ends the run cleanly.
    """
    import asyncio
    import signal

    config = _config(_private_directory(tmp_path / "state", create=True))

    async def hang(self):
        await asyncio.Event().wait()

    monkeypatch.setattr(RelayRuntime, "_open_backend", hang)
    runtime = RelayRuntime(config)

    async def scenario():
        task = asyncio.create_task(runtime.run())
        before = signal.getsignal(signal.SIGINT)
        for _ in range(200):  # up to 10s for the stop handlers to install
            if task.done() or signal.getsignal(signal.SIGINT) is not before:
                break
            await asyncio.sleep(0.05)
        assert not task.done(), "run() exited before its stop wait"
        assert signal.getsignal(signal.SIGINT) is not before, "run() installed no stop handlers"
        signal.raise_signal(signal.SIGINT)
        await asyncio.wait_for(task, 20)

    before_int, before_break = signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGBREAK)
    try:
        asyncio.run(scenario())
    finally:
        signal.signal(signal.SIGINT, before_int)
        signal.signal(signal.SIGBREAK, before_break)
