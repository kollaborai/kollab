"""Standard-library-only device service protocol and paths."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

PROTOCOL = 1
LEASE_SECONDS = 3.0
MAX_REQUEST = 128 * 1024


def service_root() -> Path:
    return Path(os.environ.get("KOLLAB_VOICE_ROOT", "~/.kollab/voice")).expanduser()


def prepare_root(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)


def socket_path(root: Path) -> Path:
    path = root / "service.sock"
    if len(os.fsencode(path)) >= 100:
        raise RuntimeError("Voice data path is too long for a local socket")
    return path


def atomic_json(path: Path, value: dict) -> None:
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(value, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def manifest() -> dict:
    return json.loads(Path(__file__).with_name("manifest.json").read_text())


class VoiceError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
