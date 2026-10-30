"""Windows private folders and files for the provisioned state store: these only run on Windows."""

import os
import subprocess

import pytest

from kollabor_config import private_fs
from kollabor_config.provisioned_state import ProvisionedStateError, ProvisionedStateFile

# icacls by full path; *S-1-1-0 is Everyone by SID, so the grant works in any Windows language
ICACLS = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "icacls.exe")
EVERYONE_READS = "*S-1-1-0:(R)"


@pytest.mark.skipif(os.name != "nt", reason="Windows private folders")
def test_state_round_trips_in_private_folder_on_windows(tmp_path):
    state_file = ProvisionedStateFile(tmp_path / "state" / "provisioned.json")
    with state_file.edit() as state:
        state.update({"installs": {}})
    assert state_file.read() == {"version": 1, "installs": {}}
    private_fs.check_private_dir(tmp_path / "state")


@pytest.mark.skipif(os.name != "nt", reason="Windows private files")
def test_state_file_others_can_read_is_refused_on_windows(tmp_path):
    state_file = ProvisionedStateFile(tmp_path / "state" / "provisioned.json")
    with state_file.edit() as state:
        state.update({"installs": {}})
    subprocess.run([ICACLS, str(state_file.path), "/grant", EVERYONE_READS], check=True, capture_output=True)
    with pytest.raises(ProvisionedStateError, match="must be private and owned"):
        state_file.read()


@pytest.mark.skipif(os.name != "nt", reason="Windows private folders")
def test_state_folder_others_can_read_is_refused_on_windows(tmp_path):
    folder = tmp_path / "state"
    private_fs.ensure_private_dir(folder)
    subprocess.run([ICACLS, str(folder), "/grant", EVERYONE_READS], check=True, capture_output=True)
    with pytest.raises(ProvisionedStateError, match="parent must be user-owned"):
        ProvisionedStateFile(folder / "provisioned.json").read()
