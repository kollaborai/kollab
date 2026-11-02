"""The engine suite must exercise this package's own source tree.

The package's pytest ini historically set no pythonpath, so a run rooted at
the package (bare `pytest packages/kollabor-engine/tests`, e.g. inside a
git worktree) resolved kollabor_engine through the environment's editable
install instead. In a worktree that means the shared checkout's engine is
imported and the suite can report green while testing the wrong tree.
pyproject.toml now sets pythonpath; this guard fails loudly if that
regresses.
"""

from pathlib import Path

import kollabor_engine


def test_suite_imports_this_package_src_not_an_editable_install():
    package_src = Path(__file__).resolve().parents[1] / "src" / "kollabor_engine"
    imported = Path(kollabor_engine.__file__).resolve()
    assert imported == package_src / "__init__.py", (
        f"engine suite imported {imported} instead of this tree's {package_src}"
    )
