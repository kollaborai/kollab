#!/usr/bin/env python3
"""Stage desktop sidecars and verify the exact, flattened release asset set.

Uses only the standard library so the publishing job needs no Kollab install.
The CLI's existing macOS/Linux assets and names remain unchanged.

macOS gets no scie: codesign cannot sign one (its jump keeps 15 bytes after
LC_CODE_SIGNATURE), and notarization rejects any unsigned Mach-O. So `stage`
unpacks the verified CLI scie into a plain tree, the scie's own CPython with
kollab installed in its site-packages, which desktop/scripts/sign-macos.sh
copies into Kollab.app and signs file by file:

    Kollab.app/Contents/Resources/kollab     sh launcher (`kollab --version`, `--web-ui`)
    Kollab.app/Contents/Resources/runtime/   bin/python3.14, lib/python3.14/..., KOLLAB-APP
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
# kollabor/binary.py reads this marker in sys.prefix: the app's runtime starts
# again through the launcher beside it and never updates itself in place.
APP_MARKER = "KOLLAB-APP"
# The scie's CPython minor version: scripts/build_binary.py PYTHON (a test keeps them equal).
RUNTIME_PYTHON = "python3.14"
# Keep in lockstep with scripts/build_binary.py PEX: pex-tools must resolve the
# same pex generation that built the scie.
PEX_PIN = "pex==2.103.4"
# Standalone pex-tools entry point (tests substitute the tuple with a fake script).
PEX_TOOLS_CMD = ("uvx", "--python", RUNTIME_PYTHON, "--from", PEX_PIN, "pex-tools")
LAUNCHER = f"""#!/bin/sh
# Kollab.app's kollab: the app's own Python, isolated (-I) from the caller's
# Python settings, never writing bytecode (-B) into the sealed bundle.
self=$0
while [ -L "$self" ]; do
  link=$(readlink "$self")
  case $link in /*) self=$link ;; *) self=$(dirname "$self")/$link ;; esac
done
# -I ignores PYTHONUNBUFFERED, which the app sets so its log stays current.
exec "$(dirname "$self")/runtime/bin/{RUNTIME_PYTHON}" -I -B ${{PYTHONUNBUFFERED:+-u}} -m kollabor_cli_main "$@"
"""
# What kollab never runs: Tcl/Tk, headers, pip (nothing installs into a signed
# app), the embedding dylib (python3.x links libpython statically) and the
# scripts whose shebangs name the build machine. Fewer Mach-O files to sign.
PRUNE = (
    "include", "share", "lib/pkgconfig", f"lib/lib{RUNTIME_PYTHON}.dylib", "lib/itcl*", "lib/libtcl*",
    "lib/tcl9*", "lib/thread*", "lib/tk9*", f"lib/{RUNTIME_PYTHON}/config-*-darwin", f"lib/{RUNTIME_PYTHON}/ensurepip",
    f"lib/{RUNTIME_PYTHON}/idlelib", f"lib/{RUNTIME_PYTHON}/tkinter", f"lib/{RUNTIME_PYTHON}/turtle.py",
    f"lib/{RUNTIME_PYTHON}/turtledemo", f"lib/{RUNTIME_PYTHON}/lib-dynload/_tkinter.*",
    f"lib/{RUNTIME_PYTHON}/site-packages/pip*",
)
MACOS_TARGETS = {"macos-aarch64": "aarch64-apple-darwin", "macos-x86_64": "x86_64-apple-darwin"}
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
        f"kollab-desktop-{platform}-{version}{suffix}"
        for platform in MACOS_TARGETS for suffix in (".app.zip", ".dmg")
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


def scie_env() -> dict[str, str]:
    """This environment minus any scie/pex identity, which would make one act as another."""
    env = {
        key: val
        for key, val in os.environ.items()
        if key not in ("SCIE", "SCIE_ARGV0", "PEX", "__PEX_EXE__", "__PEX_ENTRY_POINT__")
    }
    env["KOLLAB_NO_KEYRING"] = "1"
    return env


def unpack_runtime(scie: Path, destination: Path) -> Path:
    """The scie as a signable tree: its CPython, kollab in that Python's site-packages, a launcher."""
    if destination.exists():
        shutil.rmtree(destination)
    work = destination.with_name(destination.name + ".work")
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    # A downloaded workflow artifact loses its executable bit, so run an
    # executable copy of the verified scie rather than the download itself.
    runnable = work / scie.name
    shutil.copyfile(scie, runnable)
    runnable.chmod(0o755)
    # SCIE=split writes the scie's files, each checked against its lift manifest.
    subprocess.run([str(runnable), str(work / "split")], env={**scie_env(), "SCIE": "split"}, check=True)
    lift = json.loads((work / "split/lift.json").read_text())["scie"]["lift"]
    (python,) = [entry for entry in lift["files"] if entry.get("key") == "python-distribution"]
    archive = work / "split" / python["name"]
    if digest(archive) != python["hash"]:
        raise ValueError(f"{archive.name} does not match the scie's manifest")
    shutil.unpack_archive(archive, work / "cpython", "gztar")
    runtime = destination / "runtime"
    destination.mkdir(parents=True)
    (work / "cpython/python").rename(runtime)
    interpreter = runtime / "bin" / RUNTIME_PYTHON
    if not interpreter.is_file():
        raise ValueError(f"the scie's CPython has no bin/{RUNTIME_PYTHON}; match RUNTIME_PYTHON to build_binary.PYTHON")
    # The pex's own tools lay out exactly the distributions the scie resolved.
    # Standalone pex-tools, not PEX_TOOLS=1 on the pex itself: venv-built pexes
    # (PEX_VENV, our build) re-exec through a shim that ignores PEX_TOOLS, and
    # an inherited __PEX_EXE__ then runs the kollab app, which exits 0 on the
    # unknown flag and hides the failure until copytree finds no venv.
    venv_dir = work / "venv"
    result = subprocess.run(
        [*PEX_TOOLS_CMD, str(work / "split/pex"), "venv", "--site-packages-copies", str(venv_dir)],
        env=scie_env(),
    )
    if result.returncode != 0:
        raise ValueError(f"pex-tools venv failed with exit code {result.returncode}")
    if not (venv_dir / "lib" / RUNTIME_PYTHON / "site-packages").is_dir():
        raise ValueError("pex-tools venv produced no site-packages; refusing to stage a broken runtime")
    for pattern in PRUNE:
        for path in runtime.glob(pattern):
            shutil.rmtree(path) if path.is_dir() and not path.is_symlink() else path.unlink()
    for path in (runtime / "bin").iterdir():
        if path != interpreter:
            path.unlink()
    site_packages = runtime / "lib" / RUNTIME_PYTHON / "site-packages"
    shutil.copytree(work / "venv/lib" / RUNTIME_PYTHON / "site-packages", site_packages, dirs_exist_ok=True)
    shutil.rmtree(work)
    # Bytecode that never goes stale: -B stops writes, unchecked-hash stops
    # recompiles, and a write inside the app would break its seal.
    subprocess.run(
        [str(interpreter), "-I", "-m", "compileall", "-q", "-j0", "--invalidation-mode", "unchecked-hash",
         str(runtime / "lib" / RUNTIME_PYTHON)],
        env=scie_env(), check=False, stdout=subprocess.DEVNULL,
    )
    # Python children started without -B (sys.executable -m ...) write no bytecode either.
    (site_packages / "kollab-app.pth").write_text("import sys; sys.dont_write_bytecode = True\n")
    (runtime / APP_MARKER).write_text("Kollab.app runtime: update the app, not this tree.\n")
    launcher = destination / "kollab"
    launcher.write_text(LAUNCHER)
    launcher.chmod(0o755)
    return launcher


def stage(platform: str, source: Path, version: str) -> Path:
    verify_checksum(source)
    if platform in MACOS_TARGETS:
        # sign-macos.sh copies this tree into the built app and signs it.
        target = MACOS_TARGETS[platform]
        launcher = unpack_runtime(source, ROOT / "desktop/src-tauri/binaries" / f"app-runtime-{target}")
        return check_staged(launcher, version, {"version": version, "bundle": {"targets": ["app"]}})
    target, packages = TARGETS[platform]
    # linuxdeploy rewrites ELFs under usr/bin and usr/lib, including a
    # scie's appended archive. Keep the verified CLI opaque under usr/share.
    files = {"/usr/share/kollab/runtime/kollab": f"binaries/kollab-{target}"}
    bundle = {
        "targets": [package[0] for package in packages],
        "externalBin": [],
        "linux": {"deb": {"files": files, "depends": ["xdg-utils"]}, "appimage": {"files": files}},
    }
    destination = ROOT / "desktop/src-tauri/binaries" / f"kollab-{target}"
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    destination.chmod(0o755)
    return check_staged(destination, version, {"version": version, "bundle": bundle})


def check_staged(destination: Path, version: str, release_config: dict) -> Path:
    """The staged kollab reports `version`; then tauri.release.json is written."""
    result = subprocess.run(
        [str(destination), "--version"], env=scie_env(), capture_output=True, text=True, timeout=600, check=True
    )
    if result.stdout.split()[-1:] != [version]:
        raise ValueError(f"sidecar reports {result.stdout!r}, expected {version}")
    config = ROOT / "desktop/src-tauri/tauri.release.json"
    config.write_text(json.dumps(release_config, indent=2) + "\n")
    print(f"staged {destination}: {result.stdout.strip()}")
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
    staging.add_argument("--platform", choices=(*TARGETS, *MACOS_TARGETS), required=True)
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
