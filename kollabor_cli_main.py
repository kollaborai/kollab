"""Entry point module for kollab to avoid namespace conflicts.

This module serves as the CLI entry point and ensures imports are resolved
from the correct location, avoiding conflicts with other 'core' packages.
"""

import sys
from pathlib import Path

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
