#!/usr/bin/env python3
"""
Documentation Freshness Checker

Verifies docs are up-to-date with their tracked source files using hash comparison.

Usage:
    python scripts/docs-check.py              # Check all docs
    python scripts/docs-check.py --update     # Update hashes after doc refresh
    python scripts/docs-check.py --init       # Initialize all hashes
    python scripts/docs-check.py --verbose    # Show detailed output
"""

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path


def get_project_root() -> Path:
    """Get project root directory."""
    return Path(__file__).parent.parent


def compute_file_hash(filepath: Path) -> str:
    """Compute SHA256 hash of a single file."""
    hasher = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hasher.update(chunk)
        return hasher.hexdigest()[:16]  # Short hash for readability
    except FileNotFoundError:
        return "FILE_NOT_FOUND"
    except Exception as e:
        return f"ERROR:{e}"


def compute_path_hash(path: Path, root: Path) -> str:
    """
    Compute combined hash for a path (file or directory).
    For directories, combines hashes of all .py files.
    """
    full_path = root / path

    if full_path.is_file():
        return compute_file_hash(full_path)

    if full_path.is_dir():
        # Hash all Python files in directory
        hasher = hashlib.sha256()
        py_files = sorted(full_path.rglob("*.py"))

        if not py_files:
            return "EMPTY_DIR"

        for py_file in py_files:
            file_hash = compute_file_hash(py_file)
            hasher.update(f"{py_file.name}:{file_hash}".encode())

        return hasher.hexdigest()[:16]

    return "PATH_NOT_FOUND"


def compute_tracks_hash(tracks: list[str], root: Path) -> str:
    """Compute combined hash of all tracked paths."""
    if not tracks:
        return "NO_TRACKS"

    hasher = hashlib.sha256()
    for track in sorted(tracks):
        path_hash = compute_path_hash(Path(track), root)
        hasher.update(f"{track}:{path_hash}".encode())

    return hasher.hexdigest()[:16]


def load_manifest(root: Path) -> dict:
    """Load the docs manifest file."""
    manifest_path = root / "docs" / ".manifest.json"

    if not manifest_path.exists():
        print(f"[error] Manifest not found: {manifest_path}")
        sys.exit(1)

    with open(manifest_path) as f:
        return json.load(f)


def save_manifest(manifest: dict, root: Path) -> None:
    """Save the docs manifest file."""
    manifest_path = root / "docs" / ".manifest.json"
    manifest["generated"] = datetime.now().strftime("%Y-%m-%d")

    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"[ok] Manifest saved: {manifest_path}")


def check_docs(manifest: dict, root: Path, verbose: bool = False) -> dict:
    """
    Check all docs against their tracked files.
    Returns dict of {doc: status} where status is 'fresh', 'stale', 'unverified', etc.
    """
    results = {
        "fresh": [],
        "stale": [],
        "unverified": [],
        "no_tracking": [],
        "errors": [],
    }

    for doc_name, doc_info in manifest.get("docs", {}).items():
        tracks = doc_info.get("tracks", [])
        stored_hash = doc_info.get("hash")

        if not tracks:
            results["no_tracking"].append(doc_name)
            if verbose:
                print(f"[skip] {doc_name} - no files tracked")
            continue

        if stored_hash is None:
            results["unverified"].append(doc_name)
            if verbose:
                print(f"[warn] {doc_name} - never verified")
            continue

        current_hash = compute_tracks_hash(tracks, root)

        if current_hash.startswith("ERROR") or current_hash == "PATH_NOT_FOUND":
            results["errors"].append((doc_name, current_hash))
            if verbose:
                print(f"[error] {doc_name} - {current_hash}")
            continue

        if current_hash == stored_hash:
            results["fresh"].append(doc_name)
            if verbose:
                print(f"[ok] {doc_name} - fresh")
        else:
            results["stale"].append(doc_name)
            if verbose:
                print(f"[stale] {doc_name} - source files changed")
                print(f"        stored: {stored_hash}")
                print(f"        current: {current_hash}")

    return results


def update_hashes(manifest: dict, root: Path, doc_filter: str = None) -> None:
    """Update hashes for all (or filtered) docs."""
    updated = 0

    for doc_name, doc_info in manifest.get("docs", {}).items():
        if doc_filter and doc_filter not in doc_name:
            continue

        tracks = doc_info.get("tracks", [])
        if not tracks:
            continue

        current_hash = compute_tracks_hash(tracks, root)

        if not current_hash.startswith("ERROR") and current_hash != "PATH_NOT_FOUND":
            doc_info["hash"] = current_hash
            doc_info["verified"] = datetime.now().strftime("%Y-%m-%d")
            doc_info["status"] = "verified"
            updated += 1
            print(f"[updated] {doc_name} -> {current_hash}")

    print(f"\n[ok] Updated {updated} doc(s)")


def print_report(results: dict) -> None:
    """Print summary report."""
    print("\n" + "=" * 50)
    print("documentation freshness report")
    print("=" * 50)

    sum(len(v) if isinstance(v, list) else len(v) for v in results.values())

    if results["stale"]:
        print(f"\n[stale] {len(results['stale'])} doc(s) need review:")
        for doc in results["stale"]:
            print(f"  - {doc}")

    if results["unverified"]:
        print(f"\n[warn] {len(results['unverified'])} doc(s) never verified:")
        for doc in results["unverified"]:
            print(f"  - {doc}")

    if results["errors"]:
        print(f"\n[error] {len(results['errors'])} doc(s) have errors:")
        for doc, err in results["errors"]:
            print(f"  - {doc}: {err}")

    print(f"\n[ok] {len(results['fresh'])} doc(s) are fresh")
    print(f"[skip] {len(results['no_tracking'])} doc(s) have no tracking")

    # Exit code
    if results["stale"]:
        print("\n[!] Some docs are stale. Run with --update after reviewing.")
        return 1

    return 0


def main():
    parser = argparse.ArgumentParser(description="Check documentation freshness")
    parser.add_argument(
        "--update", action="store_true", help="Update hashes after doc refresh"
    )
    parser.add_argument("--init", action="store_true", help="Initialize all hashes")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose output")
    parser.add_argument("--filter", type=str, help="Filter docs by name substring")

    args = parser.parse_args()

    root = get_project_root()
    manifest = load_manifest(root)

    if args.init or args.update:
        update_hashes(manifest, root, args.filter)
        save_manifest(manifest, root)
        return 0

    # Check mode
    results = check_docs(manifest, root, args.verbose)
    return print_report(results)


if __name__ == "__main__":
    sys.exit(main() or 0)
