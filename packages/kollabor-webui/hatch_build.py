"""Hatch build hook for the assistant-ui static web application.

Wheel builds compile the checked-in frontend source into an isolated temporary
directory and force-include those assets. A missing toolchain or failed build is
an error; an older checkout dist must never substitute for the selected app.

Set ``KOLLAB_FRONTEND_DIST`` to package an explicit prebuilt frontend without
running npm or changing the checkout's dist directory. An invalid explicit
input fails the build instead of falling back to other assets.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    """Build and include the Vite output without tracking generated files."""

    PLUGIN_NAME = "custom"
    _frontend_output: tempfile.TemporaryDirectory[str] | None = None

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
    def _validate_dist(dist: Path) -> None:
        if dist.is_symlink() or not (dist / "index.html").is_file():
            raise ValueError("Frontend output must be a directory containing index.html")
        if any(asset.is_symlink() for asset in dist.rglob("*")):
            raise ValueError("Frontend assets must not be symlinks")

    def initialize(self, version: str, build_data: dict[str, Any]) -> None:
        del version
        package_root = Path(self.root)
        frontend = package_root / "frontend"
        # The Vite changelog module normally reads the monorepo's canonical
        # file. Carry that input inside standalone source distributions too.
        if self.target_name == "sdist":
            changelog = package_root.parents[1] / "kollabor/updates/CHANGELOG.md"
            if changelog.is_file():
                force_include = build_data.setdefault("force_include", {})
                force_include[str(changelog)] = "frontend/CHANGELOG.md"
            elif not (frontend / "CHANGELOG.md").is_file():
                raise RuntimeError("The canonical frontend changelog input is missing")
            return
        # Only a wheel needs generated assets; source publication must not
        # compile the app a second time.
        if self.target_name != "wheel":
            return
        prebuilt = os.environ.get("KOLLAB_FRONTEND_DIST")
        if prebuilt is not None:
            if not prebuilt:
                raise ValueError("KOLLAB_FRONTEND_DIST must name a built frontend directory containing index.html")
            dist = Path(prebuilt)
            self._validate_dist(dist)
        else:
            if not (frontend / "package.json").is_file():
                raise RuntimeError("Frontend source is missing; supply KOLLAB_FRONTEND_DIST")
            npm = shutil.which("npm")
            if npm is None:
                raise RuntimeError(
                    "npm is required to build the frontend; supply KOLLAB_FRONTEND_DIST to reuse a build"
                )
            self._frontend_output = tempfile.TemporaryDirectory(prefix="kollabor-webui-dist-")
            dist = Path(self._frontend_output.name) / "dist"
            try:
                lockfile = frontend / "package-lock.json"
                install_command = [npm, "ci"] if lockfile.is_file() else [npm, "install"]
                install_command.extend(["--ignore-scripts", "--no-audit", "--no-fund"])
                self._run(install_command, frontend)
                self._run([npm, "run", "build", "--", "--outDir", str(dist)], frontend)
                self._validate_dist(dist)
            except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
                self._frontend_output.cleanup()
                self._frontend_output = None
                raise

        force_include = build_data.setdefault("force_include", {})
        force_include[str(dist)] = "kollabor_webui/static/assistant"

    def finalize(self, version: str, build_data: dict[str, Any], artifact_path: str) -> None:
        del version, build_data, artifact_path
        if self._frontend_output is not None:
            self._frontend_output.cleanup()
            self._frontend_output = None
