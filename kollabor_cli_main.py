"""Entry point module for kollab to avoid namespace conflicts.

This module serves as the CLI entry point and ensures imports are resolved
from the correct location, avoiding conflicts with other 'core' packages.
"""

import sys
from pathlib import Path

# Add the current directory to sys.path to ensure we import from the right place
package_dir = Path(__file__).parent.absolute()
if str(package_dir) not in sys.path:
    sys.path.insert(0, str(package_dir))

# Now import from our local core package
from kollabor.cli import cli_main  # noqa: E402

# This is the entry point that setuptools will call
__all__ = ["cli_main"]
