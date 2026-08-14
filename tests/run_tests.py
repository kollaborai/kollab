#!/usr/bin/env python3
"""Test runner for Kollab test suite."""

import os
import sys
from pathlib import Path

import pytest

# Run from the repository root so pytest loads the project configuration.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

if __name__ == "__main__":
    os.environ.setdefault("KOLLAB_HUB_DISABLED", "1")
    sys.exit(pytest.main(["tests"]))
