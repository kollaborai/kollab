"""Windows private-folder admission for relay conversation state (#439 lane E1).

Fails on main: the store reads os.getuid(), which does not exist on Windows, so
ConversationStore construction raises AttributeError before any assertion here.
"""

import os
import subprocess

import pytest

from kollabor_config import private_fs
from plugins.hub.relay_conversations import ConversationStore
from plugins.hub.relay_state import RelayError

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows only")

WORKSPACE = "0123456789abcdef0123456789abcdef"
ICACLS = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "icacls.exe")


def test_conversation_store_admits_private_state_dir_on_windows(tmp_path):
    root = tmp_path / "network"
    private_fs.ensure_private_dir(root)
    store = ConversationStore(root, WORKSPACE)
    assert store.path.name == "conversations.sqlite3"
    assert store.path.is_file()


def test_conversation_store_refuses_others_readable_state_on_windows(tmp_path):
    root = tmp_path / "network"
    private_fs.ensure_private_dir(root)
    store = ConversationStore(root, WORKSPACE)
    subprocess.run(
        [ICACLS, str(store.path), "/grant", "*S-1-1-0:(R)"], check=True, capture_output=True
    )
    with pytest.raises(RelayError, match="ownership or identity changed"):
        with store._connect():
            pass
