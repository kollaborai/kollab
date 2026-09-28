"""pytest config: package importable without PYTHONPATH hand-holding.

rootdir = repo root; conftest adds the src layout to sys.path so `pytest`
from anywhere in the repo collects kollabor-voice tests cleanly.
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
