"""Hatch build hook for the assistant-ui static web application.

The frontend's ``dist`` directory is intentionally ignored in git.  When a
package is built, regenerate it from the checked-in frontend source whenever
npm is available, and force-include the generated assets in the wheel.  If a
build environment has no Node.js toolchain (or npm cannot reach dependencies),
keep any existing dist output intact so Python-only installs remain usable with
the last generated UI or the legacy static fallback.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    """Build and include the Vite output without tracking generated files."""

    PLUGIN_NAME = "custom"

    @staticmethod
    def _run(command: list[str], cwd: Path) -> None:
        """Run a frontend command and include its output in build errors."""

        completed = subprocess.run(
            command,
            cwd=cwd,
            check=False,
            timeout=300,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if completed.returncode:
            output = completed.stdout.strip()
            detail = f"\n{output}" if output else ""
            raise RuntimeError(
                f"{command[0]} command failed ({completed.returncode}){detail}"
            )

    @staticmethod
    def _restore_dist(dist: Path, backup: Path | None) -> None:
        """Restore prior output or remove a failed partial build.

        Vite may empty ``dist`` before reporting a compile failure. If no
        previous build existed, leaving that partial directory behind makes a
        later package build think frontend output is available and can cause
        the source checkout to serve incomplete assets. Remove it so the
        server cleanly falls back to the legacy static UI instead.
        """

        if dist.exists():
            shutil.rmtree(dist)
        if backup is not None:
            shutil.copytree(backup, dist)

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        del version
        package_root = Path(self.root)
        frontend = package_root / "frontend"
        dist = frontend / "dist"
        package_json = frontend / "package.json"

        if package_json.exists():
            npm = shutil.which("npm")
            if npm:
                # Vite's emptyOutDir can remove a previously good build before
                # discovering a compile error. Back it up so an offline or
                # broken dependency install never leaves the package unusable.
                with tempfile.TemporaryDirectory(prefix="kollabor-webui-dist-") as tmp:
                    backup: Path | None = None
                    if (dist / "index.html").is_file():
                        backup = Path(tmp) / "dist"
                        shutil.copytree(dist, backup)
                    try:
                        lockfile = frontend / "package-lock.json"
                        install_command = [npm, "ci"] if lockfile.is_file() else [npm, "install"]
                        install_command.extend(
                            ["--ignore-scripts", "--no-audit", "--no-fund"]
                        )
                        self._run(install_command, frontend)
                        self._run([npm, "run", "build"], frontend)
                    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                        self._restore_dist(dist, backup)
                        print(f"WARNING: assistant-ui build skipped: {exc}")
            else:
                print("WARNING: npm not found; using existing/legacy WebUI static fallback")

        if (dist / "index.html").is_file():
            force_include = build_data.setdefault("force_include", {})
            force_include[str(dist)] = "kollabor_webui/static/assistant"
