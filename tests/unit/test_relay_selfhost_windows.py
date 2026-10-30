"""Windows behavior for relay self-host state locking (#439).

Skipped on POSIX, where the serve.lock open keeps its POSIX shape. On
Windows this fails on main (os.O_NOFOLLOW does not exist there) and passes
once the lock opens through private_fs.open_nofollow; the exclusive-lock
behavior itself stays on file_lock, already proven by windows-locks.yml.
"""

from __future__ import annotations

import os

import pytest

from plugins.hub.relay_selfhost import _lock

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows only")


def test_lock_creates_private_serve_lock(tmp_path):
    fd = _lock(tmp_path)
    try:
        assert os.fstat(fd).st_mode & 0o170000 == 0o100000
        assert (tmp_path / "serve.lock").exists()
    finally:
        os.close(fd)
