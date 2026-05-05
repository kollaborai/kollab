#!/usr/bin/env python3
"""Publish kollabor packages to PyPI or TestPyPI.

Builds and uploads all sub-packages in dependency order,
then the root kollab package last.

Usage:
    python scripts/publish.py --test     # TestPyPI
    python scripts/publish.py --prod     # Real PyPI
    python scripts/publish.py --build    # Build only, no upload
"""

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Dependency order - packages with no deps first
PACKAGES = [
    # Layer 1: no inter-package deps
    "kollabor-events",
    "kollabor-ai",
    "kollabor-rpc",
    # Layer 2: depends on events
    "kollabor-config",
    "kollabor-plugins",
    "kollabor-tui",
    # Layer 3: depends on events + config
    "kollabor-agent",
    # Layer 4: depends on ai + agent + events + config
    "kollabor-engine",
    # Layer 5: depends on engine
    "kollabor-webui",
]


def run(cmd: list[str], cwd: Path | None = None) -> bool:
    print(f"  >> {' '.join(cmd)}")
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        output = (result.stderr or result.stdout or "").strip()
        print(f"  FAILED: {output[-500:]}")
        return False
    if result.stdout:
        for line in result.stdout.strip().split("\n")[-3:]:
            print(f"  {line}")
    return True


def clean_dist(pkg_dir: Path) -> None:
    dist = pkg_dir / "dist"
    if dist.exists():
        for f in dist.iterdir():
            f.unlink()


def build_package(pkg_dir: Path, package_name: str | None = None) -> bool:
    clean_dist(pkg_dir)
    cmd = ["uv", "build", "--out-dir", str(pkg_dir / "dist")]
    if package_name:
        cmd.extend(["--package", package_name])
    return run(cmd, cwd=ROOT)


def upload_package(pkg_dir: Path, repository: str) -> bool:
    dist = pkg_dir / "dist"
    files = list(dist.glob("*.whl")) + list(dist.glob("*.tar.gz"))
    if not files:
        print(f"  No dist files found in {dist}")
        return False
    cmd = ["uv", "run", "twine", "upload"]
    if repository == "testpypi":
        cmd.extend(["--repository", "testpypi"])
    cmd.extend(str(f) for f in files)
    return run(cmd, cwd=pkg_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish kollabor packages")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--test", action="store_true", help="Upload to TestPyPI")
    group.add_argument("--prod", action="store_true", help="Upload to real PyPI")
    group.add_argument("--build", action="store_true", help="Build only, no upload")
    parser.add_argument(
        "--skip-root", action="store_true", help="Skip root kollabor package"
    )
    parser.add_argument("--only", type=str, help="Only build/upload this package name")
    args = parser.parse_args()

    repository = "testpypi" if args.test else "pypi"
    packages = PACKAGES
    if args.only:
        if args.only not in PACKAGES and args.only != "kollab":
            print(f"Unknown package: {args.only}")
            print(f"Available: {', '.join(PACKAGES)}, kollab")
            sys.exit(1)
        if args.only == "kollab":
            packages = []
        else:
            packages = [args.only]

    failed = []

    # Sub-packages
    for name in packages:
        pkg_dir = ROOT / "packages" / name
        print(f"\n{'='*50}")
        print(f"  {name}")
        print(f"{'='*50}")

        if not build_package(pkg_dir, name):
            failed.append(name)
            print("  SKIPPING upload due to build failure")
            continue

        if not args.build:
            if not upload_package(pkg_dir, repository):
                failed.append(name)

    # Root package
    if not args.skip_root and not args.only or args.only == "kollab":
        print(f"\n{'='*50}")
        print("  kollabor (root)")
        print(f"{'='*50}")

        clean_dist(ROOT)
        if not build_package(ROOT, "kollab"):
            failed.append("kollab")
        elif not args.build:
            if not upload_package(ROOT, repository):
                failed.append("kollab")

    # Summary
    print(f"\n{'='*50}")
    if failed:
        print(f"  FAILED: {', '.join(failed)}")
        sys.exit(1)
    else:
        target = "TestPyPI" if args.test else "PyPI" if args.prod else "local"
        print(f"  ALL PACKAGES {'built' if args.build else f'published to {target}'}")


if __name__ == "__main__":
    main()
