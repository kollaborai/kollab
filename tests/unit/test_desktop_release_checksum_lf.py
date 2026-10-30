"""Checksum files are LF on every OS, so sha256sum -c can read them (#268)."""

from scripts import desktop_release as release


def test_checksum_file_has_no_carriage_return(tmp_path):
    asset = tmp_path / "kollab-desktop-windows-x86_64-1.2.3-setup.exe"
    asset.write_bytes(b"payload")
    checksum = release.write_checksum(asset)
    assert checksum.read_bytes() == f"{release.digest(asset)}  {asset.name}\n".encode()
    release.verify_checksum(asset)
