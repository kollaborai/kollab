"""Keep dependency lockfiles at or above the security patch floors."""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

PYTHON_LOCK_FLOORS = {
    "aiohttp": "3.14.4",
    "black": "26.10.1",
    "certifi": "2026.10.18",
    "cryptography": "50.0.0",
    "dnspython": "2.9.2",
    "filelock": "4.2.1",
    "pyasn1": "0.6.5",
}
SCRIPT_LOCK_FLOORS = {"aiohttp": "3.14.4", "dnspython": "2.9.2"}
NPM_LOCK_FLOORS = {"dompurify": "3.4.17", "zod": "4.6.6"}


def _version_key(version: str) -> tuple[int, ...]:
    assert re.fullmatch(r"\d+(?:\.\d+)*", version), (
        f"expected a release version, got {version!r}"
    )
    return tuple(int(part) for part in version.split("."))


def _assert_floor(name: str, version: str, minimum: str) -> None:
    assert _version_key(version) >= _version_key(minimum), (
        f"{name} is {version}; patched version {minimum} or later is required"
    )


def _requirements_versions(path: Path) -> dict[str, list[str]]:
    versions: dict[str, list[str]] = {}
    for line in path.read_text().splitlines():
        match = re.match(r"^([A-Za-z0-9_.-]+)==([0-9]+(?:\.[0-9]+)*)\b", line)
        if match:
            name = re.sub(r"[-_.]+", "-", match.group(1)).lower()
            versions.setdefault(name, []).append(match.group(2))
    return versions


def test_uv_lock_contains_patched_python_dependency_versions() -> None:
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    packages = lock["package"]

    for name, minimum in PYTHON_LOCK_FLOORS.items():
        versions = [
            package["version"]
            for package in packages
            if package["name"].lower() == name
        ]
        assert versions, f"{name} is missing from uv.lock"
        for version in versions:
            _assert_floor(name, version, minimum)


def test_discovery_requirements_contain_patched_network_dependencies() -> None:
    versions = _requirements_versions(ROOT / "scripts/discovery/requirements.txt")
    for name, minimum in SCRIPT_LOCK_FLOORS.items():
        assert versions.get(name), f"{name} is missing from discovery requirements"
        for version in versions[name]:
            _assert_floor(name, version, minimum)


def test_relay_requirements_contain_patched_network_dependencies() -> None:
    versions = _requirements_versions(ROOT / "scripts/relay/requirements.txt")
    for name, minimum in SCRIPT_LOCK_FLOORS.items():
        assert versions.get(name), f"{name} is missing from relay requirements"
        for version in versions[name]:
            _assert_floor(name, version, minimum)


def test_frontend_lock_contains_patched_dom_dependencies() -> None:
    lock = json.loads(
        (ROOT / "packages/kollabor-webui/frontend/package-lock.json").read_text()
    )
    packages = lock["packages"]

    for name, minimum in NPM_LOCK_FLOORS.items():
        versions = [
            package["version"]
            for path, package in packages.items()
            if path.split("node_modules/")[-1] == name and "version" in package
        ]
        assert versions, f"{name} is missing from package-lock.json"
        for version in versions:
            _assert_floor(name, version, minimum)
