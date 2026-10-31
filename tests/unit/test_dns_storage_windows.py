"""Windows behavior for DNS storage atomic writes (#439).

os.open() on a directory raises PermissionError on Windows, so the
crash-safe directory fsync after os.replace is POSIX-only there. On the
unfixed tree this write dies at that fsync; the pinned JSON must still
land and round-trip.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from plugins.hub.dns.storage import _locked_atomic_write

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows only")


def test_atomic_write_lands_without_directory_fsync(tmp_path: Path):
    target = tmp_path / "publisher.json"
    _locked_atomic_write(target, {"revision": 7})
    assert json.loads(target.read_text(encoding="utf-8")) == {"revision": 7}
    _locked_atomic_write(target, {"revision": 8}, compact=True)
    assert json.loads(target.read_text(encoding="utf-8")) == {"revision": 8}
