#!/usr/bin/env python3
"""Build the single-file kollab binary for this machine and check that it runs.

    python scripts/build_binary.py                    # builds the wheels from this checkout
    python scripts/build_binary.py --wheels dist      # or uses wheels already built
    python scripts/build_binary.py --expect-version 0.15.1
    python scripts/build_binary.py --frontend-dist /tmp/kollab-ui

--frontend-dist bundles an already built UI without rebuilding it. Pass the same
directory to scripts/relay/build_service.py to package the same browser assets.
With --wheels, it checks that those wheels already contain that exact UI.

The binary is a pex scie: one executable holding a portable CPython and every
wheel kollab needs, so it runs with no Python installed. It lands in --out as
kollab-<os>-<arch> (macos-aarch64, linux-x86_64, ...) beside a .sha256 file,
which install.sh and /upgrade check before installing it.

Then the binary must report the expected version, import kollab's packages and
verify an HTTPS certificate with its own Python. CI runs this on each platform
(.github/workflows/binaries.yml).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PEX = "pex==2.103.4"
# The CPython the binary carries: a Python Standalone Builds release and version.
# It, not the kollab file, is what reads the Keychain, and macOS knows it by its
# code hash (it is ad hoc signed): changing either makes every Mac ask once again.
# scripts/desktop_release.py lays out the same runtime as RUNTIME_PYTHON.
PBS_RELEASE = "20261009"
PYTHON = "3.14.8"
# pex runs on the same minor version, since floor_platform reads its wheel tags.
UVX_PEX = ["uvx", "--python", PYTHON.rsplit(".", 1)[0], "--from", PEX]
# The oldest systems the binary runs on: macOS 11, and glibc 2.28 (Debian 10+,
# Ubuntu 20.04+, RHEL 8+). Wheels are picked for these, never for the build
# machine, so a newer CI image cannot raise the floor unnoticed.
FLOORS = {"macosx": (11, 0), "manylinux": (2, 28)}
IMPORTS = "import kollabor.application, kollabor_cli_main, kollabor_engine, kollabor_webui, kollabor_voice, plugins"
TLS_URL = "https://pypi.org/simple/kollab/"
# Workspace members with no wheel of their own: the kollab wheel carries their code.
BUNDLED = ("kollabor_voice-",)


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd), flush=True)
    return subprocess.run(cmd, check=True, **kwargs)


def kollab_version(wheels: list[Path]) -> str:
    for wheel in wheels:
        match = re.fullmatch(r"kollab-([^-]+)-.+\.whl", wheel.name)
        if match:
            return match.group(1)
    raise SystemExit("no kollab wheel among: " + ", ".join(w.name for w in wheels))


def verify_frontend(wheels: list[Path], frontend: Path) -> None:
    """Refuse a binary whose WebUI wheel differs from the chosen frontend output."""
    if frontend.is_symlink() or not (frontend / "index.html").is_file():
        raise SystemExit(f"--frontend-dist must contain index.html and not be a symlink: {frontend}")
    expected = {}
    for asset in sorted(frontend.rglob("*")):
        if asset.is_symlink():
            raise SystemExit(f"frontend assets must not be symlinks: {asset}")
        if asset.is_file():
            expected[asset.relative_to(frontend).as_posix()] = asset.read_bytes()
    webui = [wheel for wheel in wheels if wheel.name.startswith("kollabor_webui-")]
    if len(webui) != 1:
        raise SystemExit(f"expected one kollabor-webui wheel, found {len(webui)}")
    prefix = "kollabor_webui/static/assistant/"
    with zipfile.ZipFile(webui[0]) as wheel:
        bundled = {
            name.removeprefix(prefix): wheel.read(name)
            for name in wheel.namelist()
            if name.startswith(prefix) and not name.endswith("/")
        }
    mismatched = sorted(name for name in expected.keys() | bundled.keys() if expected.get(name) != bundled.get(name))
    if mismatched:
        raise SystemExit(f"{webui[0].name} differs from --frontend-dist: {', '.join(mismatched)}")


def platform_name() -> str:
    """This machine as release binaries name it, as kollabor/updates/auto_update.py:platform_name does."""
    system = {"Darwin": "macos", "Linux": "linux"}.get(platform.system())
    machine = {"arm64": "aarch64", "aarch64": "aarch64", "x86_64": "x86_64", "amd64": "x86_64"}.get(
        platform.machine().lower()
    )
    if not (system and machine):
        raise SystemExit(f"no kollab binary for {platform.system()} {platform.machine()}")
    return f"{system}-{machine}"


def floor_platform(path: Path) -> Path:
    """A pex complete platform: this machine's wheel tags, minus any newer than FLOORS."""
    # pip's --platform cannot do this: it never widens manylinux_2_28 to the older tags.
    inspected = json.loads(run([*UVX_PEX, "pex3", "interpreter", "inspect", "--markers", "--tags"],
                               capture_output=True, text=True).stdout)

    def supported(tag: str) -> bool:
        match = re.fullmatch(r"(macosx|manylinux)_(\d+)_(\d+)_\w+", tag.rsplit("-", 1)[1])
        return not match or (int(match[2]), int(match[3])) <= FLOORS[match[1]]

    markers = {**inspected["marker_environment"], "python_full_version": PYTHON, "implementation_version": PYTHON}
    tags = [tag for tag in inspected["compatible_tags"] if supported(tag)]
    path.write_text(json.dumps({"marker_environment": markers, "compatible_tags": tags}))
    return path


def build(wheels: list[Path], out: Path, scratch: Path) -> Path:
    """The scie for this machine, kollab-<platform>, plus the .sha256 pex writes beside it."""
    out.mkdir(parents=True, exist_ok=True)
    binary = out / f"kollab-{platform_name()}"
    checksum = binary.with_name(binary.name + ".sha256")
    binary.unlink(missing_ok=True)
    checksum.unlink(missing_ok=True)
    run(
        [
            *UVX_PEX, "pex",
            *map(str, wheels),
            "--complete-platform", str(floor_platform(scratch / "platform.json")),
            "--console-script", "kollab",
            "--venv",
            "--scie", "eager",
            "--scie-only",
            "--scie-name-style", "platform-file-suffix",
            "--scie-pbs-release", PBS_RELEASE,
            "--scie-python-version", PYTHON,
            "--scie-pbs-stripped",
            "--scie-hash-alg", "sha256",
            "--output-file", str(out / "kollab"),
        ]
    )
    for path in (binary, checksum):
        if not path.is_file():
            raise SystemExit(f"pex wrote no {path.name}; {out} holds {sorted(p.name for p in out.iterdir())}")
    return binary


def smoke(binary: Path, version: str) -> None:
    """The binary starts outside any checkout, reports the version, imports kollab and verifies TLS."""
    # A binary built from another binary's shell must not inherit that one's identity.
    env = {key: value for key, value in os.environ.items() if key not in ("SCIE", "SCIE_ARGV0", "PEX")}
    env["KOLLAB_NO_KEYRING"] = "1"
    with tempfile.TemporaryDirectory() as cwd:
        shown = subprocess.run(
            [str(binary), "--version"], cwd=cwd, env=env, capture_output=True, text=True, timeout=600
        )
        # --version prints the file name it runs as: "kollab-macos-aarch64 0.15.1".
        if shown.returncode != 0 or shown.stdout.split()[-1:] != [version]:
            raise SystemExit(f"{binary.name} --version: expected {version}, got:\n{shown.stdout}{shown.stderr}")
        checks = f"{IMPORTS}; import urllib.request; urllib.request.urlopen({TLS_URL!r}, timeout=60).close()"
        probe = subprocess.run(
            [str(binary), "-c", checks], cwd=cwd, env={**env, "PEX_INTERPRETER": "1"},
            capture_output=True, text=True, timeout=600,
        )
        if probe.returncode != 0:
            raise SystemExit(f"{binary.name} cannot import kollab or verify HTTPS:\n{probe.stderr[-2000:]}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--wheels", type=Path, help="directory of built wheels (default: build them here)")
    parser.add_argument("--frontend-dist", type=Path, help="use this built frontend; verify it when using --wheels")
    parser.add_argument("--out", type=Path, default=ROOT / "dist" / "bin", help="where the binary goes")
    parser.add_argument("--expect-version", help="fail unless the binary is this version (v prefix allowed)")
    args = parser.parse_args()

    with tempfile.TemporaryDirectory() as scratch:
        wheel_dir = args.wheels
        if wheel_dir is None:
            wheel_dir = Path(scratch, "wheels")
            env = dict(os.environ)
            if args.frontend_dist is not None:
                env["KOLLAB_FRONTEND_DIST"] = str(args.frontend_dist.absolute())
            run(["uv", "build", "--all-packages", "--wheel", "--out-dir", str(wheel_dir)], cwd=ROOT, env=env)
        wheels = sorted(p for p in wheel_dir.glob("*.whl") if not p.name.startswith(BUNDLED))
        if args.frontend_dist is not None:
            verify_frontend(wheels, args.frontend_dist)
        version = kollab_version(wheels)
        if args.expect_version and args.expect_version.removeprefix("v") != version:
            raise SystemExit(f"the wheels are kollab {version}, not {args.expect_version}")
        binary = build(wheels, args.out.resolve(), Path(scratch))
        smoke(binary, version)

    digest = binary.with_name(binary.name + ".sha256").read_text().split()[0]
    size = binary.stat().st_size / 1e6
    print(f"built {binary} (kollab {version}, {size:.0f} MB, sha256 {digest[:16]}...)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
