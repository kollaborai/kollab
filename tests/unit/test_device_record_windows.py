"""Windows private folders for device records: the round trip only runs on Windows."""

import os

import pytest

from plugins.hub.device_record import _read_private, _write_private


@pytest.mark.skipif(os.name != "nt", reason="Windows private folders")
def test_write_then_read_private_on_windows(tmp_path):
    from kollabor_config import private_fs

    key = tmp_path / "rec" / "key"
    _write_private(key, b"secret")
    assert _read_private(key, 64) == b"secret"
    private_fs.check_private_dir(tmp_path / "rec")
