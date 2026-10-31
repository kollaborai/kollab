"""One-shot Windows ACL diagnostic for the relay serve smoke (temporary, #519).

Replicates, on a mkdtemp state directory under the runner TEMP, the exact os
calls `kollab relay serve` makes during prepare() and publish(), and prints
the outcome of each so a runner-only PermissionError can be pinned to the
exact call. Deleted once #519 is green.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def step(name, fn):
    try:
        print(f"OK   {name}: {fn()!r}")
    except BaseException as exc:  # diagnostic: report, never crash
        print(f"FAIL {name}: {type(exc).__name__}: {exc!r}")


def storage_probe(d: Path):
    """The exact write sequence of dns/storage._locked_atomic_write."""
    fd = os.open(d / "publisher.json.lock", os.O_CREAT | os.O_RDWR, 0o644)
    try:
        tmp_fd, tmp_path = tempfile.mkstemp(dir=d, prefix=".publisher.", suffix=".tmp")
        with os.fdopen(tmp_fd, "w") as f:
            f.write("{}")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, d / "publisher.json")
        directory_fd = os.open(d, os.O_RDONLY)  # crash-durability fsync of the dir
        os.close(directory_fd)
    finally:
        os.close(fd)
    return str(d / "publisher.json")


def main() -> int:
    print(f"python: {sys.version}")
    print(f"TMP={os.environ.get('TMP')!r} TEMP={os.environ.get('TEMP')!r}")
    print(f"USERPROFILE={os.environ.get('USERPROFILE')!r}")
    d = Path(tempfile.mkdtemp(prefix="relay-diag-"))
    print(f"state dir: {str(d)!r}")
    print(
        f"O_NOFOLLOW={getattr(os, 'O_NOFOLLOW', 'MISSING')} "
        f"O_DIRECTORY={getattr(os, 'O_DIRECTORY', 'MISSING')} "
        f"O_NONBLOCK={getattr(os, 'O_NONBLOCK', 'MISSING')}"
    )

    step("lstat(dir)", lambda: os.lstat(d))
    step("is_symlink", lambda: d.is_symlink())
    step("resolve(dir)", lambda: str(Path(d).resolve()))
    step("mkdir(exist_ok)", lambda: d.mkdir(mode=0o700, exist_ok=True))

    from kollabor_config import private_fs

    step("owned_private(raw, 0o077)", lambda: private_fs.owned_private(d, mask=0o077))
    step("owned_private(raw, 0o022)", lambda: private_fs.owned_private(d, mask=0o022))
    step("check_private_dir(raw)", lambda: private_fs.check_private_dir(d))
    resolved = Path(d).resolve()
    step("owned_private(resolved, 0o077)", lambda: private_fs.owned_private(resolved, mask=0o077))
    step("check_private_dir(resolved)", lambda: private_fs.check_private_dir(resolved))
    step(
        "open_nofollow(serve.lock, O_CREAT)",
        lambda: os.close(
            private_fs.open_nofollow(d / "serve.lock", os.O_WRONLY | os.O_CREAT, 0o600)
        ),
    )
    step("os.open(dir, O_RDONLY)", lambda: os.close(os.open(d, os.O_RDONLY)))
    step(
        "os.open(dir, O_RDONLY|O_DIRECTORY)",
        lambda: os.close(os.open(d, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))),
    )
    step("mkstemp(dir)", lambda: os.close(tempfile.mkstemp(dir=d)[0]))
    step("storage_probe(publisher.json)", lambda: storage_probe(d))
    step("key probe: O_EXCL service.key", lambda: os.close(os.open(d / "service.key", os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)))
    step("owned_private(service.key)", lambda: private_fs.owned_private(d / "service.key"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
