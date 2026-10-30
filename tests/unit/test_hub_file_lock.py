"""The hub's and the voice package's lock helpers stay byte-for-byte copies of kollabor_config.file_lock (#268).

The kill switch (plugins/hub/halt_runtime.py) loads before anything else and the
headless relay cannot import a workspace package, so plugins/hub carries its own copy.
The voice runtime does not install kollabor-config either, so kollabor_voice carries one too.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "copy", ["plugins/hub/file_lock.py", "packages/kollabor-voice/src/kollabor_voice/file_lock.py"]
)
def test_lock_copies_are_the_config_lock(copy):
    config = ROOT / "packages/kollabor-config/src/kollabor_config/file_lock.py"
    assert (ROOT / copy).read_bytes() == config.read_bytes()


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
        "packages/kollabor-voice/src/kollabor_voice/bootstrap.py",
        "packages/kollabor-voice/src/kollabor_voice/service.py",
        "packages/kollabor-voice/src/kollabor_voice/classifiers.py",
        "packages/kollabor-voice/src/kollabor_voice/speech_settings.py",
    ],
)
def test_locks_without_fcntl(path):
    assert "fcntl" not in (ROOT / path).read_text(encoding="utf-8")
