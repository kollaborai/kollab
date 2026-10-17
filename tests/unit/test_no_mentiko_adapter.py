"""Guard: the unwired Mentiko spawn adapter stays removed.

Issue #204: ``kollabor_agent/mentiko_adapter.py`` was ~200 lines of
subprocess-spawning code with no importer anywhere in the repo. Its
docstring claimed an external mentiko integration via ``MENTIKO_CLI``,
but that contract was never built (the RFC that planned it left phase 7c
unchecked) and the live spawn path is ``kollabor_engine.daemon_pool``.
The live Mentiko surface is the ``MENTIKO_SESSION_ID`` /
``MENTIKO_SESSION_TOKEN`` passthrough in the daemon pool and MCP
integration, which is unaffected.

If a real consumer for a spawn adapter appears, reintroduce it behind
that consumer and update this test.
"""

import importlib.util
from pathlib import Path

import kollabor_agent

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_mentiko_adapter_module_is_not_importable() -> None:
    spec = importlib.util.find_spec("kollabor_agent.mentiko_adapter")
    assert spec is None, "kollabor_agent.mentiko_adapter must stay removed (issue #204)"


def test_mentiko_adapter_file_is_gone() -> None:
    package_root = Path(kollabor_agent.__file__).resolve().parent
    stale = package_root / "mentiko_adapter.py"
    assert not stale.exists(), f"stale file must not reappear: {stale}"


def test_no_source_references_to_mentiko_adapter() -> None:
    scanned = [
        REPO_ROOT / "kollabor",
        REPO_ROOT / "plugins",
        REPO_ROOT / "packages",
    ]
    offenders: list[str] = []
    for base in scanned:
        if not base.is_dir():
            continue
        for path in base.rglob("*.py"):
            if "tests" in path.parts:
                continue
            if "mentiko_adapter" in path.read_text(encoding="utf-8", errors="ignore"):
                offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == [], (
        "mentiko_adapter references crept back into source: " + ", ".join(offenders)
    )
