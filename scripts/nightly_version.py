#!/usr/bin/env python3
"""Nightly build version: print it, or stamp it into the checkout (#1349).

    python3 scripts/nightly_version.py                  # prints 0.22.2.dev202611200900
    python3 scripts/nightly_version.py --stamp VERSION  # writes VERSION into the checkout

For release X.Y.Z in pyproject.toml a nightly is X.Y.(Z+1).devYYYYMMDDHHMM (UTC):
newer than that release, older than the next one, newer than last night's.
Stamped: every pyproject.toml version and kollabor-* constraint, and the web
bundle's package.json version, because the web UI flags a bundle whose version
differs from the engine's. The desktop app is not built nightly, so
tauri.conf.json keeps the release version. The stamp is for a build only;
never commit it.
"""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRONTEND_PACKAGE_JSON = Path("packages/kollabor-webui/frontend/package.json")


def release_version(root: Path) -> str:
    return tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def nightly_version(release: str, now: datetime) -> str:
    major, minor, patch = (int(part) for part in release.split("."))
    return f"{major}.{minor}.{patch + 1}.dev{now.astimezone(timezone.utc):%Y%m%d%H%M}"


def stamp(root: Path, version: str) -> None:
    """Replace the release version with version; write nothing if any file lacks it."""
    release = re.escape(release_version(root))
    pyproject = re.compile(rf'^(version = "|\s*"kollabor-[a-z-]+>=){release}"', re.M)
    package_json = re.compile(rf'^(  "version": "){release}"', re.M)
    targets = [
        (root / "pyproject.toml", pyproject),
        *((path, pyproject) for path in sorted((root / "packages").glob("*/pyproject.toml"))),
        (root / FRONTEND_PACKAGE_JSON, package_json),
    ]
    stamped = []
    for path, pattern in targets:
        text, count = pattern.subn(rf'\g<1>{version}"', path.read_text(encoding="utf-8"))
        if not count:
            raise SystemExit(f"{path} does not carry the release version; nothing was stamped")
        stamped.append((path, text))
    for path, text in stamped:
        path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--stamp", metavar="VERSION", help="write VERSION into this checkout")
    args = parser.parse_args()
    if args.stamp:
        stamp(ROOT, args.stamp)
        print(f"stamped {args.stamp}")
    else:
        print(nightly_version(release_version(ROOT), datetime.now(timezone.utc)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
