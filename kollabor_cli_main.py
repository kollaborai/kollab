"""Entry point module for kollab to avoid namespace conflicts.

This module serves as the CLI entry point and ensures imports are resolved
from the correct location, avoiding conflicts with other 'core' packages.
"""

import os
import sys

# --------------------------------------------------------------------------
# Stdlib-shadowing preflight
#
# Obsolete Python-2-era backport packages (typing, dataclasses, asyncio,
# argparse, enum34, pathlib, uuid, ...) install top-level modules whose names
# collide with modules that are built into Python 3. On 3.12+ they shadow the
# genuine standard library and make kollab crash on launch with a raw,
# confusing traceback (it dies importing asyncio / importlib.metadata). This is
# not kollab's fault, but the user just sees "kollab is broken."
#
# This guard runs BEFORE any kollabor import (and before importing pathlib,
# which is itself a shadowable name) so the crash never happens. It uses only
# ``sys`` and ``os`` so the check itself cannot be poisoned, and it never raises
# for healthy environments -- only the intentional exit (when offenders are
# found) propagates.
# --------------------------------------------------------------------------

# Module name (as installed in site-packages) -> PyPI distribution to uninstall,
# for the rare cases where they differ.
_STDLIB_SHADOW_DIST_NAMES = {
    "enum": "enum34",
    "concurrent": "futures",
}


def _detect_stdlib_shadowing():
    """Return ``{module_name: path}`` for site-packages entries shadowing stdlib.

    Scans every site-packages/dist-packages directory on ``sys.path`` for
    top-level module names (``.py`` files or package dirs) that collide with
    ``sys.stdlib_module_names``. Names starting with ``_`` are skipped.
    """
    stdlib = getattr(sys, "stdlib_module_names", None)
    if not stdlib:
        # Python < 3.10 has no reliable stdlib name set; skip the check.
        return {}

    offenders = {}
    seen = set()
    for entry in sys.path:
        if not entry or ("site-packages" not in entry and "dist-packages" not in entry):
            continue
        real = os.path.realpath(entry)
        if real in seen:
            continue
        seen.add(real)
        try:
            names = os.listdir(entry)
        except OSError:
            continue
        for name in names:
            if name.startswith("_"):
                continue
            if name.endswith(".py"):
                module = name[:-3]
            elif os.path.isdir(os.path.join(entry, name)):
                module = name
            else:
                continue
            if module in stdlib and module not in offenders:
                offenders[module] = os.path.join(entry, name)
    return offenders


def _stdlib_shadow_preflight():
    """Exit with an actionable message if stdlib-shadowing backports exist."""
    try:
        offenders = _detect_stdlib_shadowing()
    except Exception:
        # A bug in the guard must never break startup for healthy environments.
        return
    if not offenders:
        return

    modules = sorted(offenders)
    dists = sorted({_STDLIB_SHADOW_DIST_NAMES.get(m, m) for m in modules})
    lines = [
        "",
        "kollab: incompatible packages detected in this Python environment.",
        "",
        "These obsolete backport packages shadow the standard library and make",
        "kollab (and other modern Python tools) crash on startup:",
        "",
    ]
    lines.extend(f"    {module:<13} {offenders[module]}" for module in modules)
    lines.extend(
        [
            "",
            "They are Python-2-era backports of modules now built into Python 3.",
            "Remove them with:",
            "",
            "    pip uninstall " + " ".join(dists),
            "",
            "To avoid this class of failure entirely, install kollab in an",
            "isolated environment with pipx:",
            "",
            "    pipx install kollab",
            "",
        ]
    )
    try:
        sys.stderr.write("\n".join(lines) + "\n")
        sys.stderr.flush()
    except Exception:
        pass
    raise SystemExit(1)


_stdlib_shadow_preflight()

from pathlib import Path  # noqa: E402

package_dir = Path(__file__).parent.absolute()


def _prepend_dev_workspace_paths(repo_root: Path = package_dir) -> None:
    """Prefer this checkout's workspace packages when running from source."""
    packages_dir = repo_root / "packages"
    if not packages_dir.exists():
        return

    paths = [repo_root]
    paths.extend(sorted(packages_dir.glob("kollabor-*/src")))

    for path in reversed(paths):
        if path.exists():
            path_str = str(path)
            if path_str in sys.path:
                sys.path.remove(path_str)
            sys.path.insert(0, path_str)


_prepend_dev_workspace_paths()

# Now import from our local core package
from kollabor.cli import cli_main  # noqa: E402

# This is the entry point that setuptools will call
__all__ = ["cli_main"]

if __name__ == "__main__":
    cli_main()
