"""Windows behavior for relay runtime private state paths (#439).

Every test here is skipped on POSIX, where these paths keep their original
checks byte-for-byte. On Windows each test fails on main's code (os.getuid
and os.O_NOFOLLOW do not exist there) and passes only once the runtime uses
the kollabor_config private_fs ACL helpers.
"""

from __future__ import annotations

import os

import pytest

from plugins.hub.relay_runtime import (
    ManagedValkey,
    RuntimeConfig,
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


def test_managed_valkey_constructs_without_uid(tmp_path):
    valkey = ManagedValkey(_config(tmp_path / "state"))
    assert valkey.uid is None
    assert valkey.gid is None
