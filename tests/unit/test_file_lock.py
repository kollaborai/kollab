import importlib.util
import os
import stat
import threading
import time
from pathlib import Path

import pytest

# Load the helper from its file: importing the kollabor_config package pulls in
# the whole config stack, which the bare Windows job (windows-locks.yml) skips.
_SPEC = importlib.util.spec_from_file_location(
    "file_lock",
    Path(__file__).resolve().parents[2]
    / "packages/kollabor-config/src/kollabor_config/file_lock.py",
)
file_lock = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(file_lock)
lock, locked_file, try_lock, unlock = (
    file_lock.lock,
    file_lock.locked_file,
    file_lock.try_lock,
    file_lock.unlock,
)


def test_try_lock_is_exclusive(tmp_path):
    path = tmp_path / "state.lock"
    with open(path, "a+") as holder, open(path, "a+") as other:
        lock(holder.fileno())
        assert try_lock(other.fileno()) is False
        unlock(holder.fileno())
        assert try_lock(other.fileno()) is True
        unlock(other.fileno())


def test_locked_file_persists_and_releases(tmp_path):
    path = tmp_path / "state.txt"
    with locked_file(path) as f:
        f.write("hello\n")
    assert path.read_text(encoding="utf-8") == "hello\n"
    with open(path, "a+") as other:
        assert try_lock(other.fileno()) is True
        unlock(other.fileno())


def test_reader_not_blocked_while_locked(tmp_path):
    path = tmp_path / "state.txt"
    path.write_text("hello\n", encoding="utf-8")
    with locked_file(path):
        with open(path, encoding="utf-8") as reader:
            assert reader.read() == "hello\n"


def test_w_mode_empties_the_file_only_once_the_lock_is_held(tmp_path):
    path = tmp_path / "state.txt"
    path.write_text("keep", encoding="utf-8")

    def rewrite():
        with locked_file(path, "w") as f:
            f.write("new")

    with locked_file(path, "r+"):
        writer = threading.Thread(target=rewrite)
        writer.start()
        time.sleep(0.3)  # the writer is now waiting for the lock
        assert path.read_text(encoding="utf-8") == "keep"
    writer.join(5)
    assert not writer.is_alive()
    assert path.read_text(encoding="utf-8") == "new"


def test_writes_are_flushed_before_the_lock_is_released(tmp_path, monkeypatch):
    path = tmp_path / "state.txt"
    seen = []

    def spy(fd):
        seen.append(path.read_text(encoding="utf-8"))
        unlock(fd)

    monkeypatch.setattr(file_lock, "unlock", spy)
    with locked_file(path, "w") as f:
        f.write("data")
    assert seen == ["data"]


def test_text_is_utf8_on_every_platform(tmp_path):
    path = tmp_path / "state.txt"
    with locked_file(path, "w") as f:
        f.write("héllo ✓")
    assert path.read_bytes() == "héllo ✓".encode()


def test_an_unsupported_mode_is_refused(tmp_path):
    with pytest.raises(ValueError):
        with locked_file(tmp_path / "state.txt", "x"):
            pass


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission bits")
def test_private_creates_an_owner_only_file_and_refuses_a_symlink(tmp_path):
    path = tmp_path / "state.txt"
    with locked_file(path, "w", private=True) as f:
        f.write("secret")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    link = tmp_path / "link.txt"
    link.symlink_to(path)
    with pytest.raises(OSError):
        with locked_file(link, "a+", private=True):
            pass
