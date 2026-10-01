"""Ownership checks for the /tmp hub socket directory and socket files.

mkdir() never tightens an existing directory, so a pre-created
world-writable socket dir would leave every agent socket replaceable by
another local user. These tests pin the two guards: the directory is
ours (tightened to 0700) or refused, and a client never connects to a
socket file another user owns.
"""

import os
import socket as socket_mod
import stat
import tempfile
from pathlib import Path

import pytest

from plugins.hub import presence
from plugins.hub.local_directory import LocalAgentDirectory, LocalDirectoryError
from plugins.hub.messenger import require_own_socket


def _make_dir(path: Path, mode: int) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(mode)
    return path


def _foreign_lstat(monkeypatch, uid: int) -> None:
    """Make os.lstat report every path as owned by ``uid``."""
    real_lstat = os.lstat

    def fake_lstat(path, *args, **kwargs):
        info = real_lstat(path, *args, **kwargs)
        return os.stat_result(
            (
                info.st_mode,
                info.st_ino,
                info.st_dev,
                info.st_nlink,
                uid,
                info.st_gid,
                info.st_size,
                info.st_atime,
                info.st_mtime,
                info.st_ctime,
            )
        )

    monkeypatch.setattr(os, "lstat", fake_lstat)


def test_socket_dir_owned_by_self_tightened_to_0700(tmp_path, monkeypatch):
    root = _make_dir(tmp_path / "kollabor-hub", 0o777)
    monkeypatch.setattr(presence, "SOCKET_DIR_ROOT", root)
    monkeypatch.setattr("plugins.hub.project_scope.is_project_scoped", lambda: False)
    assert presence.get_socket_dir() == root
    assert stat.S_IMODE(os.stat(root).st_mode) & 0o077 == 0


def test_project_scoped_socket_dir_both_levels_checked(tmp_path, monkeypatch):
    root = _make_dir(tmp_path / "kollabor-hub", 0o777)
    monkeypatch.setattr(presence, "SOCKET_DIR_ROOT", root)
    monkeypatch.setattr("plugins.hub.project_scope.is_project_scoped", lambda: True)
    monkeypatch.setattr(
        "plugins.hub.project_scope.get_project_socket_key", lambda: "abc123"
    )
    scoped = presence.get_socket_dir()
    assert scoped == root / "abc123"
    assert stat.S_IMODE(os.stat(root).st_mode) & 0o077 == 0
    assert stat.S_IMODE(os.stat(scoped).st_mode) & 0o077 == 0


def test_socket_dir_foreign_owner_refused(tmp_path, monkeypatch):
    root = _make_dir(tmp_path / "kollabor-hub", 0o777)
    monkeypatch.setattr(presence, "SOCKET_DIR_ROOT", root)
    monkeypatch.setattr("plugins.hub.project_scope.is_project_scoped", lambda: False)
    _foreign_lstat(monkeypatch, os.getuid() + 1)
    with pytest.raises(RuntimeError, match="kollabor-hub.*uid"):
        presence.get_socket_dir()


def test_secure_socket_dir_absent_without_create_is_noop(tmp_path):
    target = tmp_path / "missing"
    assert presence.secure_socket_dir(target, create=False) == target
    assert not target.exists()


def test_local_directory_refuses_foreign_socket_dir(tmp_path, monkeypatch):
    root = _make_dir(tmp_path / "kollabor-hub", 0o755)
    _foreign_lstat(monkeypatch, os.getuid() + 1)
    with pytest.raises(LocalDirectoryError, match="kollabor-hub"):
        LocalAgentDirectory(tmp_path / "config", socket_dir=root)


def test_client_refuses_foreign_socket(tmp_path, monkeypatch):
    sock_path = tmp_path / "lapis.sock"
    sock_path.touch()
    sock_path.chmod(0o600)
    # A proper socket file owned by someone else: the uid is the only
    # reason to refuse.
    real_lstat = os.lstat

    def foreign_socket_lstat(path, *args, **kwargs):
        info = real_lstat(path, *args, **kwargs)
        return os.stat_result(
            (
                stat.S_IFSOCK | 0o600,
                info.st_ino,
                info.st_dev,
                info.st_nlink,
                os.getuid() + 1,
                info.st_gid,
                info.st_size,
                info.st_atime,
                info.st_mtime,
                info.st_ctime,
            )
        )

    monkeypatch.setattr(os, "lstat", foreign_socket_lstat)
    with pytest.raises(ConnectionError, match="lapis.sock"):
        require_own_socket(str(sock_path))


def test_client_accepts_own_socket():
    # Real AF_UNIX bind needs a short path on macOS.
    with tempfile.TemporaryDirectory(prefix="kso-", dir="/tmp") as root:
        server = socket_mod.socket(socket_mod.AF_UNIX, socket_mod.SOCK_STREAM)
        route = Path(root) / "s.sock"
        server.bind(str(route))
        try:
            require_own_socket(str(route))
        finally:
            server.close()


def _foreign_socket_lstat(monkeypatch, uid: int) -> None:
    """Make os.lstat report every path as a socket file owned by ``uid``."""
    real_lstat = os.lstat

    def fake_lstat(path, *args, **kwargs):
        info = real_lstat(path, *args, **kwargs)
        return os.stat_result(
            (
                stat.S_IFSOCK | 0o600,
                info.st_ino,
                info.st_dev,
                info.st_nlink,
                uid,
                info.st_gid,
                info.st_size,
                info.st_atime,
                info.st_mtime,
                info.st_ctime,
            )
        )

    monkeypatch.setattr(os, "lstat", fake_lstat)


def test_attach_client_refuses_foreign_socket(tmp_path, monkeypatch, capsys):
    import asyncio

    from kollabor.attach_client import AttachClient

    sock_path = tmp_path / "lapis.sock"
    sock_path.touch()
    _foreign_socket_lstat(monkeypatch, os.getuid() + 1)

    async def fail_if_connected(path, *args, **kwargs):
        raise AssertionError("must not connect to a foreign socket")

    monkeypatch.setattr(asyncio, "open_unix_connection", fail_if_connected)
    client = AttachClient(str(sock_path), "lapis", interactive=False)
    asyncio.run(client.run())
    err = capsys.readouterr().err
    assert "refusing hub socket" in err and "lapis.sock" in err


def test_attach_client_same_uid_socket_reaches_connect(monkeypatch, capsys):
    import asyncio
    import tempfile

    from kollabor.attach_client import AttachClient

    # A real socket file we own; the connect itself is what fails.
    with tempfile.TemporaryDirectory(prefix="kso-", dir="/tmp") as root:
        server = socket_mod.socket(socket_mod.AF_UNIX, socket_mod.SOCK_STREAM)
        sock_path = Path(root) / "lapis.sock"
        server.bind(str(sock_path))
        try:

            async def refused(path, *args, **kwargs):
                raise ConnectionRefusedError("no listener")

            monkeypatch.setattr(asyncio, "open_unix_connection", refused)
            client = AttachClient(str(sock_path), "lapis", interactive=False)
            asyncio.run(client.run())
        finally:
            server.close()
        err = capsys.readouterr().err
        assert "cannot connect" in err
        assert "refusing hub socket" not in err


def test_hub_state_client_refuses_foreign_socket(tmp_path, monkeypatch):
    import asyncio

    from kollabor.state.hub_client import HubStateClient, HubStateClientError

    sock_path = tmp_path / "lapis.sock"
    sock_path.touch()
    _foreign_socket_lstat(monkeypatch, os.getuid() + 1)
    monkeypatch.setattr(
        HubStateClient, "discover_peer_socket", classmethod(lambda cls, _: sock_path)
    )

    async def attempt():
        async with HubStateClient.connect("lapis"):
            pass

    with pytest.raises(HubStateClientError, match="refusing hub socket"):
        asyncio.run(attempt())


def test_hub_state_client_same_uid_socket_reaches_connect(monkeypatch):
    import asyncio
    import tempfile

    from kollabor.state.hub_client import HubStateClient, HubStateClientError

    # A real socket file we own, with no listener: connect itself must fail.
    with tempfile.TemporaryDirectory(prefix="kso-", dir="/tmp") as root:
        server = socket_mod.socket(socket_mod.AF_UNIX, socket_mod.SOCK_STREAM)
        sock_path = Path(root) / "lapis.sock"
        server.bind(str(sock_path))
        server.close()
        monkeypatch.setattr(
            HubStateClient,
            "discover_peer_socket",
            classmethod(lambda cls, _: sock_path),
        )

        async def attempt():
            async with HubStateClient.connect("lapis"):
                pass

        with pytest.raises(HubStateClientError, match="failed to open socket"):
            asyncio.run(attempt())
