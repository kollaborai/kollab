"""Windows private folders for the provisioned state store: the round trip only runs on Windows."""

import os

import pytest

from kollabor_config import private_fs
from kollabor_config.provisioned_state import ProvisionedStateFile


@pytest.mark.skipif(os.name != "nt", reason="Windows private folders")
def test_state_round_trips_in_private_folder_on_windows(tmp_path):
    state_file = ProvisionedStateFile(tmp_path / "state" / "provisioned.json")
    with state_file.edit() as state:
        state.update({"installs": {}})
    assert state_file.read() == {"version": 1, "installs": {}}
    private_fs.check_private_dir(tmp_path / "state")
