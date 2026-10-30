"""On Windows the relay owner refuses with its own error, never AttributeError on os.getuid (#268)."""

import asyncio
import os
import types

import pytest

from plugins.hub import relay_owner
from plugins.hub.relay_owner import RelayOwnerError, WorkspaceRelayOwner, local_relay_rpc


@pytest.fixture
def windows_os(monkeypatch):
    """The module's os as Windows shows it: os.name is "nt" and there is no getuid."""
    fake = types.SimpleNamespace(**{key: value for key, value in vars(os).items() if key != "getuid"})
    fake.name = "nt"
    monkeypatch.setattr(relay_owner, "os", fake)


def test_owner_refuses_on_windows(windows_os, tmp_path):
    with pytest.raises(RelayOwnerError, match="not available on Windows"):
        WorkspaceRelayOwner(tmp_path, tmp_path / "state")


def test_local_rpc_refuses_on_windows(windows_os, tmp_path):
    with pytest.raises(RelayOwnerError, match="not available on Windows"):
        asyncio.run(local_relay_rpc(str(tmp_path / "hub.sock"), "relay_status", {}))
