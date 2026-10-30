"""Windows private folder and files for the relay state store: these only run on Windows (#268)."""

import os
import subprocess

import pytest

from kollabor_config import private_fs
from plugins.hub.relay_state import RelayError, RelayStateStore

# icacls by full path; *S-1-1-0 is Everyone by SID, so the grant works in any Windows language
ICACLS = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "icacls.exe")
EVERYONE_READS = "*S-1-1-0:(R)"


@pytest.mark.skipif(os.name != "nt", reason="Windows private files")
def test_second_store_reads_the_key_on_windows(tmp_path):
    first = RelayStateStore(tmp_path / "project", tmp_path / "relay")
    second = RelayStateStore(tmp_path / "project", tmp_path / "relay")  # device.key exists: it is read back
    assert bytes(second.key) == bytes(first.key)
    private_fs.check_private_dir(tmp_path / "relay")


@pytest.mark.skipif(os.name != "nt", reason="Windows private files")
def test_key_others_can_read_is_refused_on_windows(tmp_path):
    RelayStateStore(tmp_path / "project", tmp_path / "relay")
    subprocess.run([ICACLS, str(tmp_path / "relay" / "device.key"), "/grant", EVERYONE_READS], check=True,
                   capture_output=True)
    with pytest.raises(RelayError, match="private to this user"):
        RelayStateStore(tmp_path / "project", tmp_path / "relay")
