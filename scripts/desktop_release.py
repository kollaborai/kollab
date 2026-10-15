#!/usr/bin/env python3
"""Stage desktop sidecars and verify the exact, flattened release asset set.

Uses only the standard library so the publishing job needs no Kollab install.
The CLI's existing macOS/Linux assets and names remain unchanged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CLI_PLATFORMS = ("macos-aarch64", "macos-x86_64", "linux-x86_64", "linux-aarch64")
TARGETS = {
    "linux-x86_64": ("x86_64-unknown-linux-gnu", (("appimage", "*.AppImage", ".AppImage"), ("deb", "*.deb", ".deb"))),
}


def release_version(value: str = "") -> str:
    source = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    version = value.removeprefix("v") if value else source
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?", version):
        raise ValueError(f"invalid release version: {version!r}")
    if version != source:
        raise ValueError(f"tag version {version} differs from source version {source}")
    return version


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_checksum(asset: Path) -> Path:
    checksum = asset.with_name(asset.name + ".sha256")
    checksum.write_text(f"{digest(asset)}  {asset.name}\n", encoding="utf-8")
    return checksum


def verify_checksum(asset: Path) -> None:
    checksum = asset.with_name(asset.name + ".sha256")
    if asset.is_symlink() or checksum.is_symlink() or not asset.is_file() or not checksum.is_file():
        raise ValueError(f"asset and checksum must be ordinary files: {asset.name}")
    line = checksum.read_text(encoding="utf-8").strip()
    match = re.fullmatch(r"([0-9a-fA-F]{64}) [ *](.+)", line)
    if not match or match[2] != asset.name:
        raise ValueError(f"checksum must name only the asset basename: {checksum.name}")
    if digest(asset) != match[1].lower():
        raise ValueError(f"SHA-256 mismatch: {asset.name}")


def desktop_names(platform: str, version: str) -> list[str]:
    return [f"kollab-desktop-{platform}-{version}{suffix}" for _, _, suffix in TARGETS[platform][1]]


def manifest(version: str) -> set[str]:
    payloads = [f"kollab-{platform}" for platform in CLI_PLATFORMS]
    payloads += [name for platform in TARGETS for name in desktop_names(platform, version)]
    payloads += [
        f"kollab-desktop-macos-{arch}-{version}{suffix}"
        for arch in ("aarch64", "x86_64") for suffix in (".app.zip", ".dmg")
    ]
    return {name + suffix for name in payloads for suffix in ("", ".sha256")}


def verify_manifest(directory: Path, version: str) -> None:
    expected = manifest(version)
    actual = {path.name for path in directory.iterdir()}
    if actual != expected:
        raise ValueError(
            f"release manifest mismatch; missing={sorted(expected - actual)}, unexpected={sorted(actual - expected)}"
        )
    for name in sorted(expected):
        if not name.endswith(".sha256"):
            verify_checksum(directory / name)
    print(f"verified {len(expected)} release assets and {len(expected) // 2} SHA-256 pairs")


def stage(platform: str, source: Path, version: str) -> Path:
    verify_checksum(source)
    target, packages = TARGETS[platform]
    destination = ROOT / "desktop/src-tauri/binaries" / f"kollab-{target}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    destination.chmod(0o755)
    env = {key: val for key, val in os.environ.items() if key not in ("SCIE", "SCIE_ARGV0", "PEX")}
    env["KOLLAB_NO_KEYRING"] = "1"
    result = subprocess.run(
        [str(destination), "--version"], env=env, capture_output=True, text=True, timeout=600, check=True
    )
    if result.stdout.split()[-1:] != [version]:
        raise ValueError(f"sidecar reports {result.stdout!r}, expected {version}")
    config = ROOT / "desktop/src-tauri/tauri.release.json"
    # linuxdeploy rewrites ELFs under usr/bin and usr/lib, including a scie's
    # appended archive. Keep the verified CLI opaque under usr/share instead.
    files = {"/usr/share/kollab/runtime/kollab": f"binaries/{destination.name}"}
    config.write_text(
        json.dumps(
            {
                "version": version,
                "bundle": {
                    "targets": [package[0] for package in packages],
                    "externalBin": [],
                    "linux": {"deb": {"files": files, "depends": ["xdg-utils"]}, "appimage": {"files": files}},
                },
            },
            indent=2,
        )
        + "\n"
    )
    print(f"staged {destination.name}: {result.stdout.strip()}")
    return destination


def collect(platform: str, bundle_dir: Path, output: Path, version: str) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    collected = []
    for (folder, pattern, _), name in zip(TARGETS[platform][1], desktop_names(platform, version), strict=True):
        candidates = list((bundle_dir / folder).glob(pattern))
        if len(candidates) != 1 or not candidates[0].is_file() or candidates[0].is_symlink():
            raise ValueError(f"expected exactly one {folder}/{pattern}; found {candidates}")
        destination = output / name
        shutil.copyfile(candidates[0], destination)
        if destination.suffix == ".AppImage":
            destination.chmod(0o755)
        checksum = write_checksum(destination)
        verify_checksum(destination)
        collected.extend((destination, checksum))
    print("\n".join(path.name for path in collected))
    return collected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", default="", help="release tag, or the source version for PR builds")
    commands = parser.add_subparsers(dest="command", required=True)
    staging = commands.add_parser("stage")
    staging.add_argument("--platform", choices=TARGETS, required=True)
    staging.add_argument("--source", type=Path, required=True)
    collecting = commands.add_parser("collect")
    collecting.add_argument("--platform", choices=TARGETS, required=True)
    collecting.add_argument("--bundle-dir", type=Path, default=ROOT / "desktop/src-tauri/target/release/bundle")
    collecting.add_argument("--out", type=Path, default=ROOT / "dist/desktop")
    verification = commands.add_parser("verify")
    verification.add_argument("directory", type=Path)
    commands.add_parser("manifest")
    args = parser.parse_args()
    try:
        version = release_version(args.version)
        if args.command == "stage":
            stage(args.platform, args.source, version)
        elif args.command == "collect":
            collect(args.platform, args.bundle_dir, args.out, version)
        elif args.command == "verify":
            verify_manifest(args.directory, version)
        else:
            print("\n".join(sorted(manifest(version))))
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, f"desktop release: {error}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
