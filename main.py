#!/usr/bin/env python3
"""Kollab - Development Entry Point.

This is a simple wrapper for development mode (python main.py).
It delegates to the main CLI implementation in core/cli.py.

For production use, install via pip and use the 'kollab' command.
"""

import sys

# Fix encoding for Windows to support Unicode characters
# Note: This is also in core/cli.py to ensure pip-installed users get it too
# Having it here ensures encoding is fixed before any imports in development mode
if sys.platform == "win32":
    # Set UTF-8 mode for stdin/stdout/stderr
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stdin, "reconfigure") and sys.stdin.isatty():
        sys.stdin.reconfigure(encoding="utf-8", errors="replace")

    # Also set console output code page to UTF-8
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleOutputCP(65001)  # UTF-8
        kernel32.SetConsoleCP(65001)  # UTF-8 for input too
    except Exception:
        pass  # Ignore if this fails

# Import and delegate to the main CLI implementation
from kollabor.cli import cli_main

if __name__ == "__main__":
    cli_main()
