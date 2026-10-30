"""Tests for kollabor_config.private_fs (kollab #268)."""

import errno
import importlib
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import types
from collections.abc import Iterator
from pathlib import Path

import pytest

_SRC = (
    Path(__file__).resolve().parents[2] / "packages/kollabor-config/src/kollabor_config"
)
_PKG = "kollabor_config_private_fs_under_test"
_pkg = types.ModuleType(_PKG)
_pkg.__path__ = [str(_SRC)]
sys.modules[_PKG] = _pkg
private_fs = importlib.import_module(f"{_PKG}.private_fs")

posix_only = pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits and uids")
windows_only = pytest.mark.skipif(os.name != "nt", reason="Windows ACLs and junctions")


@pytest.fixture
def workdir() -> Iterator[Path]:
    path = Path(tempfile.mkdtemp())
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


@posix_only
def test_owned_private_follows_mode_masks(workdir: Path) -> None:
    os.chmod(workdir, 0o700)
    assert private_fs.owned_private(workdir)
    os.chmod(workdir, 0o755)
    assert not private_fs.owned_private(workdir, mask=0o077)
    assert private_fs.owned_private(workdir, mask=0o022)


@posix_only
def test_check_private_dir_refuses_symlink(workdir: Path) -> None:
    target = workdir / "target"
    target.mkdir(mode=0o700)
    link = workdir / "link"
    os.symlink(target, link)
    with pytest.raises(PermissionError):
        private_fs.check_private_dir(link)


@posix_only
def test_check_private_dir_refuses_loose_mode(workdir: Path) -> None:
    os.chmod(workdir, 0o755)
    with pytest.raises(PermissionError):
        private_fs.check_private_dir(workdir)


@posix_only
def test_ensure_private_dir_creates_and_tightens(workdir: Path) -> None:
    deep = workdir / "a" / "b" / "c"
    private_fs.ensure_private_dir(deep)
    assert stat.S_IMODE(deep.stat().st_mode) == 0o700
    loose = workdir / "loose"
    loose.mkdir()
    os.chmod(loose, 0o755)
    private_fs.ensure_private_dir(loose)
    assert stat.S_IMODE(loose.stat().st_mode) == 0o700


@posix_only
def test_open_nofollow_opens_regular_file(workdir: Path) -> None:
    f = workdir / "f"
    f.write_text("x")
    fd = private_fs.open_nofollow(str(f), os.O_RDONLY)
    try:
        assert os.read(fd, 1) == b"x"
    finally:
        os.close(fd)


@posix_only
def test_open_nofollow_refuses_symlink(workdir: Path) -> None:
    f = workdir / "f"
    f.write_text("x")
    link = workdir / "link"
    os.symlink(f, link)
    with pytest.raises(OSError) as info:
        private_fs.open_nofollow(str(link), os.O_RDONLY)
    assert info.value.errno == errno.ELOOP


@posix_only
def test_user_tag_is_uid() -> None:
    assert private_fs.current_user_id() == private_fs.user_tag() == str(os.getuid())


def _icacls(path: Path) -> str:
    return subprocess.run(
        ["icacls", str(path)], capture_output=True, text=True, check=True
    ).stdout


@windows_only
def test_ensure_private_dir_grants_no_broad_ace(workdir: Path) -> None:
    target = workdir / "private"
    private_fs.ensure_private_dir(target)
    # icacls starts each output with the folder path, and under C:\Users that path contains "Users".
    ace_text = _icacls(target).replace(str(target), "")
    for broad in ("Users", "Authenticated Users", "Everyone"):
        assert broad not in ace_text
    assert private_fs.owned_private(target)


@windows_only
def test_users_write_grant_breaks_private_mask(workdir: Path) -> None:
    target = workdir / "private"
    private_fs.ensure_private_dir(target)
    subprocess.run(
        ["icacls", str(target), "/grant", "*S-1-5-32-545:(W)"],
        check=True,
        capture_output=True,
    )
    assert not private_fs.owned_private(target, mask=0o022)


@windows_only
def test_owned_private_rejects_other_masks(workdir: Path) -> None:
    with pytest.raises(ValueError):
        private_fs.owned_private(workdir, mask=0o755)


@windows_only
def test_junction_is_refused(workdir: Path) -> None:
    private_dir = workdir / "private"
    private_fs.ensure_private_dir(private_dir)
    other = workdir / "other"
    other.mkdir()
    (other / "f").write_text("x")
    junction = private_dir / "j"
    subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(junction), str(other)],
        check=True,
        capture_output=True,
    )
    with pytest.raises(PermissionError):
        private_fs.check_private_dir(junction)
    with pytest.raises(OSError):
        private_fs.open_nofollow(str(junction / "f"), os.O_RDONLY)


@windows_only
def test_user_id_is_a_sid() -> None:
    assert private_fs.current_user_id().startswith("S-1-5-")
    tag = private_fs.user_tag()
    assert len(tag) == 12
    assert all(c in "0123456789abcdef" for c in tag)
