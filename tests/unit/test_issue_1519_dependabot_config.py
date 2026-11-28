"""The Dependabot config points at real manifests and never double-covers one (#1519)."""

import re
import tomllib
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG = ROOT / ".github" / "dependabot.yml"

# A directory an ecosystem lists must hold at least one file it reads.
MANIFESTS = {
    "uv": ("uv.lock",),
    "pip": ("requirements*.txt", "pyproject.toml", "setup.py"),
    "npm": ("package.json",),
    "cargo": ("Cargo.toml",),
    "docker": ("Dockerfile",),
    "github-actions": (".github/workflows/*.yml",),
}
GROUP_ID = re.compile(r"[A-Za-z]([A-Za-z|_-]*[A-Za-z])?")


def _updates():
    config = yaml.safe_load(CONFIG.read_text())
    assert config["version"] == 2
    return config["updates"]


def _directories(entry):
    assert ("directory" in entry) != ("directories" in entry), entry[
        "package-ecosystem"
    ]
    return [entry["directory"]] if "directory" in entry else entry["directories"]


def _pairs():
    return [(e["package-ecosystem"], d) for e in _updates() for d in _directories(e)]


@pytest.mark.parametrize(
    ("ecosystem", "directory"), _pairs() if CONFIG.exists() else []
)
def test_each_directory_holds_a_manifest_for_its_ecosystem(ecosystem, directory):
    folder = ROOT / directory.strip("/")
    assert folder.is_dir(), directory
    assert any(any(folder.glob(p)) for p in MANIFESTS[ecosystem]), (
        ecosystem,
        directory,
    )


def test_no_ecosystem_lists_a_directory_twice():
    pairs = _pairs()
    assert len(pairs) == len(set(pairs))


def test_pip_stays_off_the_uv_workspace():
    workspace = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["uv"][
        "workspace"
    ]
    members = {
        "/" + str(p.relative_to(ROOT))
        for g in workspace["members"]
        for p in ROOT.glob(g)
    }
    pip_dirs = {d.rstrip("/") or "/" for e, d in _pairs() if e == "pip"}
    assert not pip_dirs & (members | {"/"})
    assert ("uv", "/") in _pairs()
    # Every package's pyproject.toml is a workspace member, so the uv entry covers it.
    packages = {
        "/" + str(p.parent.relative_to(ROOT))
        for p in ROOT.glob("packages/*/pyproject.toml")
    }
    assert packages <= members


def test_every_entry_has_a_schedule_and_a_minor_patch_group():
    for entry in _updates():
        name = entry["package-ecosystem"]
        assert entry["schedule"]["interval"] in {"daily", "weekly", "monthly"}, name
        assert entry["open-pull-requests-limit"] > 0, name
        assert entry["cooldown"]["default-days"] >= 0, name
        groups = entry["groups"]
        assert all(GROUP_ID.fullmatch(g) for g in groups), name
        assert any(
            sorted(g.get("update-types", [])) == ["minor", "patch"]
            for g in groups.values()
        ), name
