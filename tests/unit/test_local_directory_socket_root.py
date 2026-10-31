"""#439: the local directory's default socket root must match the writer's.

The hub writes sockets under presence.SOCKET_DIR_ROOT (per-platform). A
hardcoded "/tmp/kollabor-hub" default in LocalAgentDirectory resolves to
C:\\tmp\\kollabor-hub on Windows, where no socket is ever relative to it,
so _socket_valid silently rejects every presence record and relay lookups
fail with "local agent is no longer online in this workspace".
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).parent.parent.parent))

from plugins.hub import presence
from plugins.hub.local_directory import LocalAgentDirectory


def test_default_socket_dir_is_the_presence_root(tmp_path):
    # conftest isolates presence.SOCKET_DIR_ROOT per test; read it at runtime.
    directory = LocalAgentDirectory(tmp_path / "config")
    assert directory.socket_dir == Path(presence.SOCKET_DIR_ROOT).absolute()


def test_explicit_socket_dir_wins(tmp_path):
    sockets = tmp_path / "sockets"
    sockets.mkdir()
    directory = LocalAgentDirectory(tmp_path / "config", socket_dir=sockets)
    assert directory.socket_dir == sockets.absolute()


if __name__ == "__main__":
    import unittest

    unittest.main()
