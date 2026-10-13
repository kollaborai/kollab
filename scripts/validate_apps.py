#!/usr/bin/env python3
"""Validate the Apps catalog (bundles/data/apps.json).

Schema rules live in kollabor/apps/catalog.py (validate_catalog). --online also
checks that every npm, PyPI and brew package exists (npm must not be deprecated)
and that every GitHub skill folder has a SKILL.md. Run from the repo root with
the project venv so the kollabor imports resolve.
"""

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "bundles" / "data" / "apps.json"
TIMEOUT = 60

sys.path.insert(0, str(ROOT))

from kollabor.apps.catalog import validate_catalog  # noqa: E402


def _http_status(url: str) -> int:
    request = urllib.request.Request(url, headers={"User-Agent": "kollab-validate-apps"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def _npm_problem(package: str) -> str | None:
    exists = subprocess.run(["npm", "view", package, "name"], capture_output=True, text=True, timeout=TIMEOUT)
    if exists.returncode != 0 or not exists.stdout.strip():
        return f"npm package {package} not found"
    deprecated = subprocess.run(["npm", "view", package, "deprecated"], capture_output=True, text=True, timeout=TIMEOUT)
    if deprecated.stdout.strip():
        return f"npm package {package} is deprecated"
    return None


def _brew_problem(package: str, cask: bool) -> str | None:
    kind = "--cask" if cask else "--formula"
    result = subprocess.run(
        ["brew", "info", "--json=v2", kind, package],
        capture_output=True,
        text=True,
        timeout=TIMEOUT,
    )
    if result.returncode == 0:
        return None
    if package.count("/") == 2 and not cask:
        # A tap formula: `brew info` sees only taps already tapped here, while
        # `brew install owner/tap/name` taps on its own. Look in the tap repo.
        owner, tap, name = package.split("/")
        for path in (f"Formula/{name}.rb", f"Formula/{name[0]}/{name}.rb", f"{name}.rb"):
            if _http_status(f"https://raw.githubusercontent.com/{owner}/homebrew-{tap}/HEAD/{path}") == 200:
                return None
    return f"brew {kind[2:]} {package} not found"


def _pypi_problem(package: str) -> str | None:
    status = _http_status(f"https://pypi.org/pypi/{package}/json")
    return None if status == 200 else f"PyPI package {package} not found (HTTP {status})"


def _skill_problem(spec: dict) -> str | None:
    url = f"https://api.github.com/repos/{spec['repo']}/contents/{spec['path']}/SKILL.md?ref={spec['ref']}"
    status = _http_status(url)
    if status == 200:
        return None
    return f"GitHub {spec['repo']} {spec['path']}/SKILL.md not found at {spec['ref']} (HTTP {status})"


def _app_problems(app: dict) -> list[str]:
    if app["kind"] == "mcp":
        mcp = app["mcp"]
        package = next((arg for arg in mcp.get("args", []) if not arg.startswith("-")), "")
        if mcp["command"] == "npx":
            checks = [_npm_problem(package)]
        elif mcp["command"] == "uvx":
            checks = [_pypi_problem(package)]
        else:
            checks = []  # not a package runner, nothing to look up
    elif app["kind"] == "cli":
        checks = []
        for method in app["cli"]["install"]:
            if method["manager"] == "brew":
                checks.append(_brew_problem(method["package"], method.get("cask", False)))
            elif method["manager"] == "npm":
                checks.append(_npm_problem(method["package"]))
            else:
                checks.append(_pypi_problem(method["package"]))
    else:
        checks = [_skill_problem(app["skill"])]
    return [check for check in checks if check]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate bundles/data/apps.json.")
    parser.add_argument("--online", action="store_true", help="also check packages and GitHub skill folders exist")
    args = parser.parse_args(argv)

    try:
        data = json.loads(CATALOG.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        print(f"FAIL: cannot read {CATALOG}: {exc}")
        return 1

    problems = validate_catalog(data)
    if args.online and not problems:
        for app in data["apps"]:
            problems += [f"{app['kind']}:{app['id']}: {problem}" for problem in _app_problems(app)]

    for problem in problems:
        print(f"FAIL: {problem}")
    if problems:
        print(f"\n{len(problems)} problem(s) in {CATALOG}")
        return 1
    mode = "schema and online checks" if args.online else "schema"
    print(f"OK: {len(data['apps'])} apps, {mode} pass (updated {data['updated']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
