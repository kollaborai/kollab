#!/usr/bin/env python3
"""Clean up Python cache files and build artifacts.

This script removes:
- __pycache__ directories
- *.pyc, *.pyo files
- build/ directories
- dist/ directories
- *.egg-info directories

Usage:
    python scripts/clean.py
    python scripts/clean.py --dry-run
    python scripts/clean.py --cache-only
    python scripts/clean.py --build-only
"""

import argparse
import shutil
import sys
from pathlib import Path


def clean_cache_files():
    """Remove Python cache files."""
    removed = []

    # Remove __pycache__ directories
    for pycache in Path(".").rglob("__pycache__"):
        if pycache.is_dir():
            shutil.rmtree(pycache)
            removed.append(str(pycache))

    # Remove .pyc files
    for pyc in Path(".").rglob("*.pyc"):
        if pyc.is_file():
            pyc.unlink()
            removed.append(str(pyc))

    # Remove .pyo files
    for pyo in Path(".").rglob("*.pyo"):
        if pyo.is_file():
            pyo.unlink()
            removed.append(str(pyo))

    return removed


def clean_build_artifacts():
    """Remove build artifacts."""
    removed = []

    # Patterns to remove
    patterns = ["build", "dist", "*.egg-info"]

    for pattern in patterns:
        for path in Path(".").glob(pattern):
            if path.is_dir():
                shutil.rmtree(path)
                removed.append(str(path))
            elif path.is_file():
                path.unlink()
                removed.append(str(path))

    return removed


def main():
    """Main cleanup function."""
    parser = argparse.ArgumentParser(
        description="Clean Python cache files and build artifacts",
        epilog="""
examples:
  python scripts/clean.py              clean everything
  python scripts/clean.py --dry-run   show what would be removed
  python scripts/clean.py --cache-only  only __pycache__ and .pyc files
  python scripts/clean.py --build-only  only build/, dist/, *.egg-info
        """,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        help="show what would be removed without actually removing",
    )
    parser.add_argument(
        "--cache-only",
        action="store_true",
        help="only remove cache files (__pycache__, .pyc, .pyo)",
    )
    parser.add_argument(
        "--build-only",
        action="store_true",
        help="only remove build artifacts (build/, dist/, *.egg-info)",
    )
    args = parser.parse_args()

    if args.dry_run:
        print("dry run - nothing will be removed")
        print()

    print("Cleaning Python cache files and build artifacts...")
    print()

    if not args.build_only:
        cache_removed = clean_cache_files()
        if args.dry_run:
            if cache_removed:
                print(f"would remove {len(cache_removed)} cache files/directories:")
                for item in cache_removed:
                    print(f"    - {item}")
            else:
                print("no cache files to remove")
        else:
            if cache_removed:
                print(f"✓ Removed {len(cache_removed)} cache files/directories")
            else:
                print("✓ No cache files to remove")

    if not args.cache_only:
        build_removed = clean_build_artifacts()
        if args.dry_run:
            if build_removed:
                print(f"would remove {len(build_removed)} build artifacts:")
                for artifact in build_removed:
                    print(f"    - {artifact}")
            else:
                print("no build artifacts to remove")
        else:
            if build_removed:
                print(f"✓ Removed {len(build_removed)} build artifacts:")
                for artifact in build_removed:
                    print(f"    - {artifact}")
            else:
                print("✓ No build artifacts to remove")

    print()
    if args.dry_run:
        print("dry run complete - nothing was removed")
    else:
        print("✓ Cleanup complete!")

    return 0


if __name__ == "__main__":
    sys.exit(main())
