"""Windows behavior for relay service listener setup (#439).

AF_UNIX sockets, os.getuid and grp do not exist on Windows, so
bind_unix_socket refuses instead of dying partway through its checks.
"""

from __future__ import annotations

import os

import pytest

from plugins.hub.relay_service import RelayConfigError, bind_unix_socket

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows only")


def test_unix_socket_is_refused_on_windows(tmp_path):
    with pytest.raises(RelayConfigError, match="Linux or macOS"):
        bind_unix_socket(str(tmp_path / "relay.sock"))
