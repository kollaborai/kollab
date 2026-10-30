"""The hub's lock helper stays a byte-for-byte copy of kollabor_config.file_lock (#268).

The kill switch (plugins/hub/halt_runtime.py) loads before anything else and the
headless relay cannot import a workspace package, so plugins/hub carries its own copy.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_hub_lock_is_the_config_lock():
    hub = ROOT / "plugins/hub/file_lock.py"
    config = ROOT / "packages/kollabor-config/src/kollabor_config/file_lock.py"
    assert hub.read_bytes() == config.read_bytes()


@pytest.mark.parametrize(
    "path",
    [
        "plugins/hub/halt_runtime.py",
        "plugins/hub/relay_owner.py",
        "plugins/hub/relay_runtime.py",
        "plugins/hub/relay_selfhost.py",
        "plugins/hub/dns/storage.py",
        "plugins/hub/dns/discovery_store.py",
        "plugins/hub/dns/discovery_publish.py",
    ],
)
def test_hub_locks_without_fcntl(path):
    assert "fcntl" not in (ROOT / path).read_text(encoding="utf-8")
