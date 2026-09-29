"""The packaged relay must import with nothing but its own allowlist on the path.

scripts/relay/build_service.py ships an explicit file list to kollabor.ai. A
module added to the relay's import graph but not to that list only fails on
the server, at service start. This test builds the archive and imports the
service entry modules from the extracted tree in a fresh interpreter.
"""

import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENTRY_MODULES = (
    "plugins.hub.relay_service",
    "plugins.hub.relay_backend",
    "plugins.hub.relay_runtime",
    "plugins.hub.dns.discovery_publish",
)


def test_packaged_relay_imports_from_its_allowlist_alone(tmp_path):
    sys.path.insert(0, str(ROOT / "scripts" / "relay"))
    try:
        from build_service import build
    finally:
        sys.path.pop(0)
    archive = tmp_path / "source.tar.gz"
    build(archive)
    with tarfile.open(archive) as tar:
        tar.extractall(tmp_path / "tree", filter="data")
    script = "import sys; sys.path.insert(0, '.')\n" + "".join(
        f"import {name}\n" for name in ENTRY_MODULES
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=tmp_path / "tree",
        capture_output=True,
        text=True,
        env={"PATH": "", "KOLLAB_NO_KEYRING": "1", "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == 0, result.stderr[-2000:]
