"""Windows private-folder walk for the local hub directory view (#439 lane E2).

Fails on main: os.O_DIRECTORY and dir_fd do not exist on Windows, so
LocalAgentDirectory construction raises AttributeError before any assertion here.
"""

import os
import subprocess

import pytest

from kollabor_config import private_fs
from plugins.hub.local_directory import LocalAgentDirectory

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows only")

ICACLS = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "icacls.exe")


def _directory(tmp_path):
    config = tmp_path / "config"
    private_fs.ensure_private_dir(config)
    sockets = tmp_path / "sockets"
    sockets.mkdir(parents=True, exist_ok=True)
    return LocalAgentDirectory(config, socket_dir=sockets)


def test_local_directory_boots_on_windows(tmp_path):
    directory = _directory(tmp_path)
    assert len(directory.machine_id) == 32
    assert directory.agents() == []


def test_socket_probe_accepts_private_endpoint_on_windows(tmp_path):
    directory = _directory(tmp_path)
    endpoint = directory.socket_dir / "hub.sock"
    endpoint.write_text("127.0.0.1:0\n", encoding="ascii")
    assert directory._socket_valid(str(endpoint)) is True


def test_socket_probe_refuses_others_readable_endpoint_on_windows(tmp_path):
    directory = _directory(tmp_path)
    endpoint = directory.socket_dir / "hub.sock"
    endpoint.write_text("127.0.0.1:0\n", encoding="ascii")
    subprocess.run(
        [ICACLS, str(endpoint), "/grant", "*S-1-1-0:(R)"], check=True, capture_output=True
    )
    assert directory._socket_valid(str(endpoint)) is False


def test_local_directory_agents_walks_presence_on_windows(tmp_path):
    """Regression (#439): _presence_path ran os.fstat on the directory handle,
    but the nt port keeps a plain Path there, so a real presence folder made
    agents() raise TypeError out of RelayAgentBridge._refresh_directory."""
    directory = _directory(tmp_path)
    private_fs.ensure_private_dir(directory.config_dir / "hub" / "presence")
    assert directory.agents() == []
