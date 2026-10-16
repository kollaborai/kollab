"""Desktop artifacts must survive download/flattening and remain hash-verifiable."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import desktop_release as release


@pytest.fixture
def source_root(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "1.2.3"\n')
    monkeypatch.setattr(release, "ROOT", tmp_path)
    return tmp_path


def complete_release(directory, version="1.2.3"):
    directory.mkdir()
    for name in release.manifest(version):
        if not name.endswith(".sha256"):
            asset = directory / name
            asset.write_bytes(b"package payload: " + name.encode())
            release.write_checksum(asset)


def test_source_and_tag_must_agree(source_root):
    assert release.release_version() == "1.2.3"
    assert release.release_version("") == "1.2.3"
    assert release.release_version("v1.2.3") == "1.2.3"
    with pytest.raises(ValueError, match="differs from source"):
        release.release_version("v1.2.4")
    with pytest.raises(ValueError, match="invalid release version"):
        release.release_version("../other")


def test_manifest_preserves_all_cli_macos_and_linux_pairs():
    names = release.manifest("1.2.3")
    assert len(names) == 20
    for platform in ("macos-aarch64", "macos-x86_64", "linux-x86_64", "linux-aarch64"):
        assert f"kollab-{platform}" in names
        assert f"kollab-{platform}.sha256" in names
    assert "kollab-desktop-linux-x86_64-1.2.3.AppImage" in names
    assert "kollab-desktop-linux-x86_64-1.2.3.deb.sha256" in names
    for arch in ("aarch64", "x86_64"):
        for suffix in (".app.zip", ".dmg"):
            assert f"kollab-desktop-macos-{arch}-1.2.3{suffix}" in names
            assert f"kollab-desktop-macos-{arch}-1.2.3{suffix}.sha256" in names


def test_flattened_assets_and_checksums_verify_without_build_paths(tmp_path):
    directory = tmp_path / "downloaded"
    complete_release(directory)
    release.verify_manifest(directory, "1.2.3")
    for path in directory.glob("*.sha256"):
        assert path.read_text().strip().split(maxsplit=1)[1] == path.name.removesuffix(".sha256")


def test_balanced_file_count_cannot_hide_wrong_asset_name(tmp_path):
    directory = tmp_path / "downloaded"
    complete_release(directory)
    (directory / "kollab-linux-aarch64").rename(directory / "unexpected")
    with pytest.raises(ValueError, match="release manifest mismatch"):
        release.verify_manifest(directory, "1.2.3")


def test_corrupted_payload_is_rejected(tmp_path):
    directory = tmp_path / "downloaded"
    complete_release(directory)
    (directory / "kollab-desktop-linux-x86_64-1.2.3.AppImage").write_bytes(b"changed")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        release.verify_manifest(directory, "1.2.3")


def test_checksum_cannot_reference_build_path_or_another_file(tmp_path):
    asset = tmp_path / "app.deb"
    asset.write_bytes(b"payload")
    checksum = release.write_checksum(asset)
    checksum.write_text(f"{release.digest(asset)}  dist/desktop/app.deb\n")
    with pytest.raises(ValueError, match="basename"):
        release.verify_checksum(asset)


def test_symlink_cannot_substitute_for_a_release_payload(tmp_path):
    actual = tmp_path / "outside"
    actual.write_bytes(b"payload")
    asset = tmp_path / "app.deb"
    asset.symlink_to(actual)
    release.write_checksum(asset)
    with pytest.raises(ValueError, match="ordinary files"):
        release.verify_checksum(asset)


@pytest.mark.parametrize("platform,target", [
    ("linux-x86_64", "x86_64-unknown-linux-gnu"),
    ("macos-aarch64", "aarch64-apple-darwin"),
    ("macos-x86_64", "x86_64-apple-darwin"),
])
def test_stage_checks_hash_version_permissions_and_target_name(source_root, monkeypatch, platform, target):
    source = source_root / f"kollab-{platform}"
    source.write_bytes(b"test CLI payload")
    source.chmod(0o644)  # Actions artifacts do not retain the executable bit.
    release.write_checksum(source)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "kollab 1.2.3\n", "")

    monkeypatch.setattr(release.subprocess, "run", run)
    staged = release.stage(platform, source, "1.2.3")
    assert staged.name == f"kollab-{target}"
    assert staged.stat().st_mode & 0o111
    assert calls[0][0] == [str(staged), "--version"]
    config = json.loads((source_root / "desktop/src-tauri/tauri.release.json").read_text())
    assert config["version"] == "1.2.3"
    if platform.startswith("macos-"):
        assert config["bundle"] == {
            "targets": ["app"], "macOS": {"files": {"Resources/kollab": f"binaries/kollab-{target}"}}
        }
        return
    assert config["bundle"]["targets"] == ["appimage", "deb"]
    assert config["bundle"]["externalBin"] == []
    expected = {"/usr/share/kollab/runtime/kollab": "binaries/kollab-x86_64-unknown-linux-gnu"}
    assert config["bundle"]["linux"]["deb"]["files"] == expected
    assert config["bundle"]["linux"]["appimage"]["files"] == expected
    assert "xdg-utils" in config["bundle"]["linux"]["deb"]["depends"]


def test_stage_never_executes_a_corrupted_sidecar(source_root, monkeypatch):
    source = source_root / "kollab-linux-x86_64"
    source.write_bytes(b"original")
    release.write_checksum(source)
    source.write_bytes(b"corrupt")
    monkeypatch.setattr(release.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("unverified executable ran"))
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        release.stage("linux-x86_64", source, "1.2.3")


def test_collect_renames_each_package_and_writes_portable_checksums(tmp_path):
    bundles, output = tmp_path / "bundle", tmp_path / "dist"
    for directory, name in (("appimage", "Kollab_1.2.3_amd64.AppImage"), ("deb", "Kollab_1.2.3_amd64.deb")):
        folder = bundles / directory
        folder.mkdir(parents=True)
        (folder / name).write_bytes(b"package contents")
    result = release.collect("linux-x86_64", bundles, output, "1.2.3")
    assert len(result) == 4
    for name in release.desktop_names("linux-x86_64", "1.2.3"):
        release.verify_checksum(output / name)
    extra = bundles / "deb/another.deb"
    extra.write_bytes(b"stale package")
    with pytest.raises(ValueError, match="expected exactly one"):
        release.collect("linux-x86_64", bundles, output, "1.2.3")


def test_publish_cli_validates_flattened_download_and_returns_nonzero_on_corruption(tmp_path):
    root = Path(__file__).resolve().parents[2]
    version = release.release_version()
    directory = tmp_path / "downloaded"
    complete_release(directory, version)
    command = [
        sys.executable,
        str(root / "scripts/desktop_release.py"),
        "--version",
        f"v{version}",
        "verify",
        str(directory),
    ]
    checked = subprocess.run(command, capture_output=True, text=True)
    assert checked.returncode == 0, checked.stderr
    assert "20 release assets" in checked.stdout
    (directory / "kollab-linux-aarch64").write_bytes(b"changed after download")
    rejected = subprocess.run(command, capture_output=True, text=True)
    assert rejected.returncode != 0
    assert "SHA-256 mismatch" in rejected.stderr
