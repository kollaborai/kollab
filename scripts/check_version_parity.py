#!/usr/bin/env python3
"""Fail if any versioned surface drifts from the release version.

The release version is the engine version in the root pyproject.toml, or
--expected (a bare version or a vX.Y.Z tag) where a workflow already knows
the release. Checked surfaces:

- pyproject.toml                     (engine / release reference)
- packages/*/pyproject.toml          (every workspace package)
- packages/kollabor-webui/frontend/package.json
                                     (web bundle version, injected into the
                                      frontend at build time as __APP_VERSION__)
- desktop/src-tauri/tauri.conf.json  (desktop bundle version)

Wired in as a release-prep check (docs/release-process.md), a CI step
(.github/workflows/ci.yml), and a tag gate (.github/workflows/publish.yml):
a tag whose surfaces disagree must fail the publish workflow before any
build or upload happens, not drift silently into artifacts.

Exit 0 when every surface matches; exit 1 with a drift report otherwise.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][A-Za-z0-9.-]+)?$")

FRONTEND_PACKAGE_JSON = Path("packages/kollabor-webui/frontend/package.json")
TAURI_CONF = Path("desktop/src-tauri/tauri.conf.json")


def pyproject_version(path: Path) -> str:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    version = data["project"]["version"]
    if not isinstance(version, str):
        raise ValueError(f"{path}: project.version is not a string: {version!r}")
    return version


def package_json_version(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    version = data["version"]
    if not isinstance(version, str):
        raise ValueError(f"{path}: version is not a string: {version!r}")
    return version


def _strip_jsonc(text: str) -> str:
    """Remove // and /* */ comments outside string literals (Tauri allows JSONC)."""
    out: list[str] = []
    i, n = 0, len(text)
    in_string = False
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if ch == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def tauri_conf_version(path: Path) -> str:
    # tauri.conf.json is parsed strictly first; only fall back to JSONC
    # comment stripping (string-literal aware, so "https://..." survives)
    # if the strict parse fails.
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = json.loads(_strip_jsonc(text))
    version = data["version"]
    if not isinstance(version, str):
        raise ValueError(f"{path}: version is not a string: {version!r}")
    return version


def collect_versions(root: Path) -> dict[str, str]:
    """Return {surface: version} for every versioned file under root.

    Raises FileNotFoundError (with a named surface) if a versioned file is
    missing -- a deleted version is drift, not an acceptable state.
    """
    versions: dict[str, str] = {}

    engine = root / "pyproject.toml"
    if not engine.is_file():
        raise FileNotFoundError(f"missing versioned file: {engine}")
    versions["pyproject.toml (engine)"] = pyproject_version(engine)

    packages = sorted((root / "packages").glob("*/pyproject.toml"))
    if not packages:
        raise FileNotFoundError(f"no packages/*/pyproject.toml under {root / 'packages'}")
    for path in packages:
        versions[f"{path.parent.name} (pyproject.toml)"] = pyproject_version(path)

    frontend = root / FRONTEND_PACKAGE_JSON
    if not frontend.is_file():
        raise FileNotFoundError(f"missing versioned file: {frontend}")
    versions["kollabor-webui frontend (package.json)"] = package_json_version(frontend)

    tauri = root / TAURI_CONF
    if not tauri.is_file():
        raise FileNotFoundError(f"missing versioned file: {tauri}")
    versions["desktop tauri.conf.json"] = tauri_conf_version(tauri)

    return versions


def find_drift(expected: str, versions: dict[str, str]) -> list[str]:
    """Return human-readable drift lines for surfaces not at `expected`."""
    if not VERSION_RE.fullmatch(expected):
        raise ValueError(f"invalid release version: {expected!r}")
    return sorted(
        f"{name}: {version} != {expected}"
        for name, version in versions.items()
        if version != expected
    )


def report(drift: list[str]) -> None:
    for line in drift:
        # GitHub annotation in CI, plain line locally.
        if os.environ.get("GITHUB_ACTIONS") == "true":
            print(f"::error::version drift: {line}", file=sys.stderr)
        else:
            print(f"version drift: {line}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--expected",
        default="",
        help="release version (0.16.2) or tag (v0.16.2); default: root pyproject.toml version",
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=ROOT,
        help="repository root (default: this script's checkout; tests pass a fixture)",
    )
    args = parser.parse_args()

    try:
        versions = collect_versions(args.root)
        expected = args.expected.removeprefix("v") or versions["pyproject.toml (engine)"]
        drift = find_drift(expected, versions)
    except (FileNotFoundError, ValueError, KeyError, json.JSONDecodeError, tomllib.TOMLDecodeError) as error:
        print(f"version parity check could not run: {error}", file=sys.stderr)
        return 2

    if drift:
        report(drift)
        print(
            f"version parity FAILED: {len(drift)} of {len(versions)} surfaces "
            f"drift from {expected}. Land a prep PR that bumps every version, "
            f"then re-tag (release workflows refuse to publish on drift).",
            file=sys.stderr,
        )
        return 1

    print(f"version parity OK: all {len(versions)} surfaces at {expected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
