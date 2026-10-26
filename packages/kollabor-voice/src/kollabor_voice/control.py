"""Standard-library-only device service protocol and paths."""

from __future__ import annotations

import hashlib
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


def install_identity() -> dict:
    """Which kollabor_voice this process runs: its directory and a digest of its code.

    The service imports this package from the Kollab that started it and keeps
    that code until it exits, so a different digest means other code is installed.
    """
    package = Path(__file__).resolve().parent
    digest = hashlib.sha256()
    for path in sorted(package.glob("*.py")) + [package / "manifest.json"]:
        digest.update(path.name.encode() + b"\0" + path.read_bytes())
    return {"path": str(package), "code": digest.hexdigest()[:16]}


class VoiceError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
