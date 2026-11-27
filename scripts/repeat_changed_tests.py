#!/usr/bin/env python3
"""Repeat changed, non-quarantined Python test files before a PR can merge."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
Runner = Callable[..., subprocess.CompletedProcess[str]]
# The Python suites CI runs with the root environment. The voice suite needs its own
# pinned environment (ci.yml support_voice_tests); the repeat job runs it with --suites there.
ROOT_SUITE = "tests"
PACKAGE_SUITES = ("packages/kollabor-engine/tests", "packages/kollabor-rpc/tests")
DEFAULT_SUITES = (ROOT_SUITE, *PACKAGE_SUITES)


def select_test_files(paths: Sequence[str], suites: Sequence[str] = DEFAULT_SUITES) -> list[str]:
    """Keep added or changed Python tests in the suites CI runs, in stable order."""
    selected = {
        path
        for raw in paths
        if (path := str(PurePosixPath(raw))).startswith(tuple(f"{s}/" for s in suites))
        and len(PurePosixPath(path).parts) > 1
        and PurePosixPath(path).name.startswith("test_")
        and PurePosixPath(path).suffix == ".py"
    }
    return sorted(selected)


def changed_test_files(
    base_ref: str, *, runner: Runner | None = None, suites: Sequence[str] = DEFAULT_SUITES
) -> list[str]:
    """Return test files added or changed between base_ref and HEAD."""
    command = [
        "git",
        "diff",
        "--name-only",
        "--diff-filter=ACMR",
        f"{base_ref}...HEAD",
        "--",
        *suites,
    ]
    result = (runner or subprocess.run)(
        command,
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    return [path for path in select_test_files(result.stdout.splitlines(), suites) if (ROOT / path).is_file()]


def run_test_file(
    path: str,
    runs: int,
    *,
    runner: Runner | None = None,
    python: str | None = None,
) -> int:
    """Run one test file repeatedly and stop at its first failing run."""
    execute = runner or subprocess.run
    interpreter = python or sys.executable
    host_scans = os.environ.get("GITHUB_ACTIONS") == "true"
    command = [
        interpreter,
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "-m",
        "not quarantine",
        *(["-o", "asyncio_mode=auto"] if path.startswith(PACKAGE_SUITES) else []),  # as ci.yml runs them
        # As the unit shards run them; the option is defined in tests/conftest.py, so root suite only.
        # conftest refuses it outside GitHub-hosted CI, so local runs leave host scans skipped.
        *(["--allow-host-process-enumeration"] if host_scans and path.startswith(f"{ROOT_SUITE}/") else []),
        path,
    ]
    for attempt in range(1, runs + 1):
        result = execute(command, cwd=str(ROOT), check=False)
        if result.returncode == 5:
            print(
                f"No non-quarantined tests collected from {path}; skipping repeats.",
                flush=True,
            )
            return 0
        if result.returncode:
            print(
                f"FAILED: {path} run {attempt}/{runs} (pytest exit {result.returncode})",
                file=sys.stderr,
                flush=True,
            )
            return result.returncode
    return 0


def positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="base ref for the PR diff")
    parser.add_argument("--runs", type=positive_int, default=20)
    parser.add_argument(
        "--suites",
        type=lambda value: tuple(filter(None, value.split(","))),
        default=DEFAULT_SUITES,
        help="comma-separated test suites to diff and repeat (the voice suite runs in its own environment)",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths = changed_test_files(args.base, suites=args.suites)
    if not paths:
        print("No changed test files; nothing to repeat.")
        return 0
    for path in paths:
        print(f"Repeating {path} ({args.runs} runs)", flush=True)
        result = run_test_file(path, args.runs)
        if result:
            return result
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
