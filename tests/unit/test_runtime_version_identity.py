"""Runtime version identity for dev/live checkouts (issue #630).

A source checkout must report a PEP 440 local segment (``0.18.5+<sha8>``) so
dev builds cannot masquerade as release builds in join codes, logs, and
support triage. Installed/release artifacts keep the exact base version.
"""

from kollabor import version as kollabor_version
from kollabor.build_info import UNKNOWN
from kollabor.updates.version_comparator import compare_versions, is_newer_version


def test_installed_mode_reports_exact_base(monkeypatch):
    """A release install must keep the exact base version, no suffix."""
    monkeypatch.setattr(kollabor_version, "_is_running_from_source", lambda: False)
    assert kollabor_version.get_runtime_version() == kollabor_version.get_kollabor_version()


def test_source_mode_appends_local_segment(monkeypatch):
    """A source checkout reports base + short sha as a PEP 440 local segment."""
    monkeypatch.setattr(kollabor_version, "_is_running_from_source", lambda: True)
    monkeypatch.setattr(kollabor_version, "_EMBEDDED_PROVENANCE", None)
    monkeypatch.setattr(kollabor_version, "get_kollabor_version", lambda: "0.18.5")
    monkeypatch.setattr(
        kollabor_version,
        "get_build_provenance",
        lambda: kollabor_version.BuildProvenance(
            branch="issue-630", sha="1528fa9e" + "0" * 32
        ),
    )
    assert kollabor_version.get_runtime_version() == "0.18.5+1528fa9e"


def test_unresolved_revision_reports_unknown_suffix(monkeypatch):
    """An unresolvable revision must not masquerade as a release either."""
    monkeypatch.setattr(kollabor_version, "_is_running_from_source", lambda: True)
    monkeypatch.setattr(kollabor_version, "_EMBEDDED_PROVENANCE", None)
    monkeypatch.setattr(kollabor_version, "get_kollabor_version", lambda: "0.18.5")
    monkeypatch.setattr(
        kollabor_version,
        "get_build_provenance",
        lambda: kollabor_version.BuildProvenance(branch="unknown", sha=UNKNOWN),
    )
    assert kollabor_version.get_runtime_version() == "0.18.5+unknown"

def test_embedded_provenance_artifact_stays_exact(monkeypatch):
    """A release artifact (relay/pyinstaller tree) keeps the exact base version.

    The packaged relay ships a source-like tree (pyproject.toml present) with
    baked build identity. Embedded provenance marks it as a release artifact,
    so the runtime identity must stay bare even though it looks like a source
    checkout. Mirrors tests/unit/test_relay_build_allowlist.py.
    """
    monkeypatch.setattr(kollabor_version, "_is_running_from_source", lambda: True)
    monkeypatch.setattr(
        kollabor_version,
        "_EMBEDDED_PROVENANCE",
        kollabor_version.BuildProvenance(branch="release/0.18.0", sha="ab" * 20),
    )
    monkeypatch.setattr(kollabor_version, "get_kollabor_version", lambda: "0.18.5")
    monkeypatch.setattr(
        kollabor_version,
        "get_build_provenance",
        lambda: kollabor_version.BuildProvenance(branch="release/0.18.0", sha="ab" * 20),
    )
    assert kollabor_version.get_runtime_version() == "0.18.5"



def test_dunder_version_is_the_runtime_identity():
    """``kollabor.__version__`` must be the runtime identity, not the bare base."""
    assert kollabor_version.__version__ == kollabor_version.get_runtime_version()


def test_local_segment_sorts_above_base_and_below_next_release():
    """Dev suffix must not trigger a false upgrade nag nor mask a real one."""
    assert compare_versions("0.18.5+1528fa9e", "0.18.5") == 1
    assert is_newer_version("0.18.5+1528fa9e", "0.18.5") is False
    assert is_newer_version("0.18.5+1528fa9e", "0.18.6") is True
